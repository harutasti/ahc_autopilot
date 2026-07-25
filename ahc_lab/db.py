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
  parent_run_id integer,
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

create table if not exists snapshot_norms (
  source_hash text primary key,
  normalized_hash text not null,
  created_at real not null
);
"""


class ExperimentDB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("pragma journal_mode=wal")
        self.conn.execute("pragma busy_timeout=10000")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        run_columns = {row[1] for row in self.conn.execute("pragma table_info(runs)")}
        if "source_hash" not in run_columns:
            self.conn.execute("alter table runs add column source_hash text")
        if "parent_run_id" not in run_columns:
            self.conn.execute("alter table runs add column parent_run_id integer")
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
        parent_run_id: int | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            insert into runs
            (problem, tag, run_type, score_direction, solver_path, build_command,
             source_hash, parent_run_id, params_json, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                problem,
                tag,
                run_type,
                score_direction,
                str(solver_path),
                build_command,
                source_hash,
                parent_run_id,
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

    def list_archive_runs(self, problem: str, tag_prefix: str) -> list[dict[str, Any]]:
        """Fully finished baseline/candidate runs whose tag starts with a prefix.

        `bad_cases` counts non-ok or unscored cases so callers can require
        full seed coverage before treating the mean as a comparable fitness.
        """
        rows = self.conn.execute(
            """
            select r.id as run_id, r.source_hash, r.score_direction,
                   avg(c.score) as mean_score,
                   sum(case when c.status != 'ok' or c.score is null then 1 else 0 end)
                       as bad_cases
            from runs r join cases c on c.run_id = r.id
            where r.problem = ? and r.status = 'done'
              and r.run_type in ('baseline', 'candidate')
              and r.source_hash is not null
              and r.tag like ?
            group by r.id
            """,
            (problem, tag_prefix + "%"),
        ).fetchall()
        return [dict(row) for row in rows]

    def count_children(self) -> dict[int, int]:
        """How many runs branched from each parent run."""
        rows = self.conn.execute(
            """
            select parent_run_id as pid, count(*) as n from runs
            where parent_run_id is not null group by parent_run_id
            """
        ).fetchall()
        return {int(row["pid"]): int(row["n"]) for row in rows}

    def list_evaluated_sources(self, problem: str) -> list[dict[str, Any]]:
        """Distinct source hashes evaluated for a problem, with their first run."""
        rows = self.conn.execute(
            """
            select source_hash, min(id) as run_id from runs
            where problem = ? and source_hash is not null
            group by source_hash
            """,
            (problem,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_normalized_hash(self, source_hash: str) -> str | None:
        row = self.conn.execute(
            "select normalized_hash from snapshot_norms where source_hash = ?",
            (source_hash,),
        ).fetchone()
        return row["normalized_hash"] if row else None

    def save_normalized_hash(self, source_hash: str, normalized_hash: str) -> None:
        self.conn.execute(
            """
            insert or replace into snapshot_norms (source_hash, normalized_hash, created_at)
            values (?, ?, ?)
            """,
            (source_hash, normalized_hash, time.time()),
        )
        self.conn.commit()

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

    def record_tuning_trial(
        self,
        *,
        autopilot_trial_id: int | None,
        params: dict[str, Any],
        score: float | None,
        status: str,
    ) -> int:
        """Record one parameter-tuning evaluation.

        `trial_id` 0 marks standalone `ahc tune` runs (the column predates
        nullable support); autopilot-driven tuning passes its trial id.
        """
        cur = self.conn.execute(
            """
            insert into tuning_trials (trial_id, params_json, score, status, created_at)
            values (?, ?, ?, ?, ?)
            """,
            (
                int(autopilot_trial_id or 0),
                json.dumps(params, sort_keys=True),
                score,
                status,
                time.time(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

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

    def list_merged_patches(self, limit: int = 3) -> list[dict[str, Any]]:
        """Most recent merged patches joined with their trial run ids."""
        rows = self.conn.execute(
            """
            select p.id, p.candidate_name, p.summary, p.created_at,
                   t.base_run_id, t.new_run_id, t.role
            from patches p
            join autopilot_trials t on p.trial_id = t.id
            where p.status = 'merged'
            order by p.id desc limit ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_session(self, session_id: int) -> dict[str, Any]:
        row = self.conn.execute(
            "select * from autopilot_sessions where id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"session not found: {session_id}")
        return dict(row)

    def latest_session_id(self) -> int | None:
        row = self.conn.execute("select max(id) as m from autopilot_sessions").fetchone()
        return int(row["m"]) if row and row["m"] is not None else None

    def list_trials(self, session_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "select * from autopilot_trials where session_id = ? order by id",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_trial(self, trial_id: int) -> dict[str, Any]:
        row = self.conn.execute(
            "select * from autopilot_trials where id = ?", (trial_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"trial not found: {trial_id}")
        return dict(row)

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
