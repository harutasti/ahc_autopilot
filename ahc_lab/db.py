from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


SCHEMA = """
create table if not exists runs (
  id integer primary key autoincrement,
  problem text not null,
  tag text not null,
  run_type text not null,
  score_direction text not null,
  solver_path text not null,
  build_command text not null,
  source_hash text,
  params_json text not null default '{}',
  status text not null default 'running',
  created_at real not null,
  finished_at real
);

create table if not exists cases (
  id integer primary key autoincrement,
  run_id integer not null references runs(id),
  seed integer not null,
  score real,
  elapsed_sec real not null,
  status text not null,
  input_path text not null,
  output_path text not null,
  stderr_path text not null,
  stdout_excerpt text not null default '',
  stderr_excerpt text not null default '',
  source_case_id integer,
  created_at real not null,
  unique(run_id, seed)
);

create table if not exists comparisons (
  id integer primary key autoincrement,
  base_run_id integer not null,
  new_run_id integer not null,
  summary_json text not null,
  created_at real not null
);

create table if not exists autopilot_sessions (
  id integer primary key autoincrement,
  problem text not null,
  status text not null,
  budget_sec real,
  agent_count integer not null,
  created_at real not null,
  finished_at real
);

create table if not exists autopilot_trials (
  id integer primary key autoincrement,
  session_id integer not null references autopilot_sessions(id),
  role text not null,
  candidate_name text not null,
  status text not null,
  base_run_id integer,
  new_run_id integer,
  decision text,
  summary_json text not null default '{}',
  created_at real not null,
  finished_at real
);

create table if not exists patches (
  id integer primary key autoincrement,
  trial_id integer,
  candidate_name text not null,
  path text not null,
  summary text not null default '',
  status text not null default 'created',
  created_at real not null
);

create table if not exists review_results (
  id integer primary key autoincrement,
  trial_id integer not null,
  reviewer text not null,
  verdict text not null,
  notes text not null,
  created_at real not null
);

create table if not exists tuning_trials (
  id integer primary key autoincrement,
  trial_id integer not null,
  params_json text not null,
  score real,
  status text not null,
  created_at real not null
);

create table if not exists agent_tasks (
  id integer primary key autoincrement,
  session_id integer,
  agent_name text not null,
  role text not null,
  status text not null,
  input_json text not null default '{}',
  output_json text not null default '{}',
  created_at real not null,
  finished_at real
);

create table if not exists agent_messages (
  id integer primary key autoincrement,
  session_id integer,
  agent_name text not null,
  role text not null,
  content text not null,
  created_at real not null
);

create table if not exists agent_scorecards (
  agent_name text primary key,
  attempts integer not null default 0,
  accepted integer not null default 0,
  rejected integer not null default 0,
  total_delta real not null default 0.0,
  updated_at real not null
);
"""


class ExperimentDB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("pragma journal_mode=wal")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        run_columns = {row[1] for row in self.conn.execute("pragma table_info(runs)")}
        if "source_hash" not in run_columns:
            self.conn.execute("alter table runs add column source_hash text")
        case_columns = {row[1] for row in self.conn.execute("pragma table_info(cases)")}
        if "source_case_id" not in case_columns:
            self.conn.execute("alter table cases add column source_case_id integer")

    @classmethod
    def default(cls, root: Path) -> "ExperimentDB":
        return cls(root / "experiments" / "ahc.sqlite3")

    def close(self) -> None:
        self.conn.close()

    def create_run(
        self,
        *,
        problem: str,
        tag: str,
        run_type: str,
        score_direction: str,
        solver_path: Path,
        build_command: str,
        params: dict[str, Any] | None = None,
        source_hash: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            insert into runs
            (problem, tag, run_type, score_direction, solver_path, build_command,
             source_hash, params_json, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                problem,
                tag,
                run_type,
                score_direction,
                str(solver_path),
                build_command,
                source_hash,
                json.dumps(params or {}, sort_keys=True),
                time.time(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str = "done") -> None:
        self.conn.execute(
            "update runs set status = ?, finished_at = ? where id = ?",
            (status, time.time(), run_id),
        )
        self.conn.commit()

    def insert_case(
        self,
        *,
        run_id: int,
        seed: int,
        score: float | None,
        elapsed_sec: float,
        status: str,
        input_path: Path,
        output_path: Path,
        stderr_path: Path,
        stdout_excerpt: str = "",
        stderr_excerpt: str = "",
        source_case_id: int | None = None,
    ) -> None:
        self.conn.execute(
            """
            insert or replace into cases
            (run_id, seed, score, elapsed_sec, status, input_path, output_path,
             stderr_path, stdout_excerpt, stderr_excerpt, source_case_id, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                seed,
                score,
                elapsed_sec,
                status,
                str(input_path),
                str(output_path),
                str(stderr_path),
                stdout_excerpt[:4000],
                stderr_excerpt[:4000],
                source_case_id,
                time.time(),
            ),
        )
        self.conn.commit()

    def find_cached_case(
        self, *, problem: str, source_hash: str, seed: int, params_json: str = "{}"
    ) -> dict[str, Any] | None:
        """Find a previous ok case for the same source, seed, and params."""
        row = self.conn.execute(
            """
            select c.* from cases c
            join runs r on c.run_id = r.id
            where r.problem = ? and r.source_hash = ? and r.params_json = ?
              and c.seed = ? and c.status = 'ok' and c.score is not null
            order by c.id desc limit 1
            """,
            (problem, source_hash, params_json, seed),
        ).fetchone()
        return dict(row) if row is not None else None

    def best_scores(self, problem: str, *, maximize: bool) -> dict[int, float]:
        """Best known ok score per seed across all runs of a problem."""
        agg = "max" if maximize else "min"
        rows = self.conn.execute(
            f"""
            select c.seed as seed, {agg}(c.score) as best from cases c
            join runs r on c.run_id = r.id
            where r.problem = ? and c.status = 'ok' and c.score is not null
            group by c.seed
            """,
            (problem,),
        ).fetchall()
        return {int(row["seed"]): float(row["best"]) for row in rows}

    def get_run(self, run_id: int) -> dict[str, Any]:
        row = self.conn.execute("select * from runs where id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"run not found: {run_id}")
        return dict(row)

    def list_runs(self, problem: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if problem:
            rows = self.conn.execute(
                "select * from runs where problem = ? order by id desc limit ?",
                (problem, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "select * from runs order by id desc limit ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def list_cases(self, run_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "select * from cases where run_id = ? order by seed", (run_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def save_comparison(self, base_run_id: int, new_run_id: int, summary: dict[str, Any]) -> int:
        cur = self.conn.execute(
            """
            insert into comparisons (base_run_id, new_run_id, summary_json, created_at)
            values (?, ?, ?, ?)
            """,
            (base_run_id, new_run_id, json.dumps(summary, sort_keys=True), time.time()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def create_session(self, problem: str, budget_sec: float | None, agent_count: int) -> int:
        cur = self.conn.execute(
            """
            insert into autopilot_sessions
            (problem, status, budget_sec, agent_count, created_at)
            values (?, 'running', ?, ?, ?)
            """,
            (problem, budget_sec, agent_count, time.time()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_session(self, session_id: int, status: str) -> None:
        self.conn.execute(
            "update autopilot_sessions set status = ?, finished_at = ? where id = ?",
            (status, time.time(), session_id),
        )
        self.conn.commit()

    def create_trial(
        self,
        *,
        session_id: int,
        role: str,
        candidate_name: str,
        base_run_id: int | None,
    ) -> int:
        cur = self.conn.execute(
            """
            insert into autopilot_trials
            (session_id, role, candidate_name, status, base_run_id, created_at)
            values (?, ?, ?, 'created', ?, ?)
            """,
            (session_id, role, candidate_name, base_run_id, time.time()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_trial(
        self,
        trial_id: int,
        *,
        status: str,
        decision: str | None = None,
        new_run_id: int | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            update autopilot_trials
            set status = ?, decision = ?, new_run_id = ?, summary_json = ?, finished_at = ?
            where id = ?
            """,
            (
                status,
                decision,
                new_run_id,
                json.dumps(summary or {}, sort_keys=True),
                time.time(),
                trial_id,
            ),
        )
        self.conn.commit()

    def record_patch(
        self,
        *,
        trial_id: int | None,
        candidate_name: str,
        path: str,
        summary: str,
        status: str = "created",
    ) -> int:
        cur = self.conn.execute(
            """
            insert into patches (trial_id, candidate_name, path, summary, status, created_at)
            values (?, ?, ?, ?, ?, ?)
            """,
            (trial_id, candidate_name, path, summary, status, time.time()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def record_agent_message(
        self, session_id: int | None, agent_name: str, role: str, content: str
    ) -> int:
        cur = self.conn.execute(
            """
            insert into agent_messages
            (session_id, agent_name, role, content, created_at)
            values (?, ?, ?, ?, ?)
            """,
            (session_id, agent_name, role, content, time.time()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_scorecard(self, agent_name: str, accepted: bool, delta: float) -> None:
        now = time.time()
        self.conn.execute(
            """
            insert into agent_scorecards
            (agent_name, attempts, accepted, rejected, total_delta, updated_at)
            values (?, 1, ?, ?, ?, ?)
            on conflict(agent_name) do update set
              attempts = attempts + 1,
              accepted = accepted + excluded.accepted,
              rejected = rejected + excluded.rejected,
              total_delta = total_delta + excluded.total_delta,
              updated_at = excluded.updated_at
            """,
            (agent_name, 1 if accepted else 0, 0 if accepted else 1, delta, now),
        )
        self.conn.commit()

    def get_scorecard(self, agent_name: str | None = None) -> list[dict[str, Any]]:
        if agent_name:
            rows = self.conn.execute(
                "select * from agent_scorecards where agent_name = ?", (agent_name,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "select * from agent_scorecards order by total_delta desc, attempts desc"
            ).fetchall()
        return [dict(row) for row in rows]
