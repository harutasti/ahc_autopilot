from __future__ import annotations

import json
import os
import re
import shlex
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ProblemConfig, load_problem_config
from .db import ExperimentDB
from .policy import AcceptancePolicy
from .runner import excerpt, run_shell
from .snapshots import snapshot_solver_source, store_dir_for


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    command: str
    solver_path: Path
    stdout: str
    stderr: str
    elapsed_sec: float


@dataclass(frozen=True)
class CaseOutcome:
    seed: int
    score: float | None
    elapsed_sec: float
    status: str
    input_path: Path
    output_path: Path
    stderr_path: Path
    stdout_excerpt: str
    stderr_excerpt: str


def default_jobs() -> int:
    return max(1, (os.cpu_count() or 2) - 1)


def _env_name(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(key)).upper()


def split_stages(seeds: list[int], stage_sizes: tuple[int, ...]) -> list[list[int]]:
    stages: list[list[int]] = []
    start = 0
    for size in stage_sizes:
        if start >= len(seeds):
            break
        stages.append(seeds[start : start + size])
        start += size
    if start < len(seeds):
        stages.append(seeds[start:])
    return [stage for stage in stages if stage]


class Evaluator:
    def __init__(self, root: Path, problem: str, db: ExperimentDB | None = None):
        self.root = root.resolve()
        self.config: ProblemConfig = load_problem_config(self.root, problem)
        self.db = db or ExperimentDB.default(self.root)
        self.build_dir = self.root / ".ahc_lab" / "build" / problem
        self.solver_path = self.build_dir / "solver"

    def _vars(
        self,
        *,
        seed: int | None = None,
        run_dir: Path | None = None,
        input_path: Path | None = None,
        output_path: Path | None = None,
    ) -> dict[str, str]:
        return {
            "root": str(self.root),
            "problem": self.config.name,
            "problem_dir": str(self.config.problem_dir),
            "build_dir": str(self.build_dir),
            "solver": str(self.solver_path),
            "seed": "" if seed is None else str(seed),
            "run_dir": "" if run_dir is None else str(run_dir),
            "input": "" if input_path is None else str(input_path),
            "output": "" if output_path is None else str(output_path),
        }

    def build(self) -> BuildResult:
        self.build_dir.mkdir(parents=True, exist_ok=True)
        command = self.config.build_command.format(**self._vars())
        result = run_shell(command, self.root, timeout=120)
        return BuildResult(
            ok=result.returncode == 0 and self.solver_path.exists(),
            command=command,
            solver_path=self.solver_path,
            stdout=result.stdout,
            stderr=result.stderr,
            elapsed_sec=result.elapsed_sec,
        )

    def evaluate(
        self,
        seeds: list[int],
        *,
        tag: str,
        run_type: str = "manual",
        params: dict[str, Any] | None = None,
        build_first: bool = True,
        jobs: int | None = None,
        use_cache: bool = True,
        parent_run_id: int | None = None,
    ) -> int:
        run_id, run_dir, source_hash, params_json = self._start_run(
            tag=tag,
            run_type=run_type,
            params=params,
            build_first=build_first,
            parent_run_id=parent_run_id,
        )
        all_ok = self._evaluate_batch(
            run_id,
            run_dir,
            seeds,
            jobs=jobs,
            use_cache=use_cache,
            source_hash=source_hash,
            params_json=params_json,
            params=params,
        )
        self.db.finish_run(run_id, status="done" if all_ok else "failed")
        return run_id

    def evaluate_cascade(
        self,
        seeds: list[int],
        *,
        tag: str,
        baseline_run_id: int,
        policy: AcceptancePolicy,
        run_type: str = "candidate",
        params: dict[str, Any] | None = None,
        build_first: bool = True,
        jobs: int | None = None,
        use_cache: bool = True,
        stage_sizes: tuple[int, ...] = (5, 30),
        parent_run_id: int | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Evaluate in stages, aborting once the acceptance gate can no longer pass.

        Pruning saves the cost of seeds that cannot change the verdict: a seed
        failure, an over-limit case, or more worsening seeds than the policy
        budget allows are all unrecoverable, so remaining stages are skipped
        and the run is finished with status `pruned`.
        """
        run_id, run_dir, source_hash, params_json = self._start_run(
            tag=tag,
            run_type=run_type,
            params=params,
            build_first=build_first,
            parent_run_id=parent_run_id if parent_run_id is not None else baseline_run_id,
        )
        baseline_cases = {c["seed"]: c for c in self.db.list_cases(baseline_run_id)}
        stages = split_stages(list(seeds), stage_sizes)
        all_ok = True
        executed = 0
        prune_reason: str | None = None
        for idx, stage_seeds in enumerate(stages):
            batch_ok = self._evaluate_batch(
                run_id,
                run_dir,
                stage_seeds,
                jobs=jobs,
                use_cache=use_cache,
                source_hash=source_hash,
                params_json=params_json,
                params=params,
            )
            all_ok = all_ok and batch_ok
            executed += len(stage_seeds)
            if idx + 1 < len(stages):
                prune_reason = self._prune_reason(run_id, baseline_cases, policy)
                if prune_reason:
                    break
        pruned = prune_reason is not None
        status = "pruned" if pruned else ("done" if all_ok else "failed")
        self.db.finish_run(run_id, status=status)
        cascade = {
            "pruned": pruned,
            "prune_reason": prune_reason,
            "evaluated_seed_count": executed,
            "total_seed_count": len(seeds),
        }
        return run_id, cascade

    def _start_run(
        self,
        *,
        tag: str,
        run_type: str,
        params: dict[str, Any] | None,
        build_first: bool,
        parent_run_id: int | None = None,
    ) -> tuple[int, Path, str | None, str]:
        build = self.build() if build_first else None
        if build is not None and not build.ok:
            raise RuntimeError(f"build failed:\n{build.stderr or build.stdout}")
        source_hash = snapshot_solver_source(self.root, store_dir_for(self.db))
        params_json = json.dumps(params or {}, sort_keys=True)
        run_id = self.db.create_run(
            problem=self.config.name,
            tag=tag,
            run_type=run_type,
            score_direction=self.config.score_direction,
            solver_path=self.solver_path,
            build_command=self.config.build_command,
            params=params,
            source_hash=source_hash,
            parent_run_id=parent_run_id,
        )
        run_dir = self.root / "experiments" / "runs" / f"run_{run_id:06d}_{tag}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_id, run_dir, source_hash, params_json

    def _evaluate_batch(
        self,
        run_id: int,
        run_dir: Path,
        seeds: list[int],
        *,
        jobs: int | None,
        use_cache: bool,
        source_hash: str | None,
        params_json: str,
        params: dict[str, Any] | None = None,
    ) -> bool:
        """Evaluate seeds in parallel; only this (main) thread touches the DB."""
        all_ok = True
        pending: list[int] = []
        for seed in seeds:
            cached = (
                self.db.find_cached_case(
                    problem=self.config.name,
                    source_hash=source_hash,
                    seed=seed,
                    params_json=params_json,
                )
                if use_cache and source_hash
                else None
            )
            if cached is not None:
                self.db.insert_case(
                    run_id=run_id,
                    seed=seed,
                    score=cached["score"],
                    elapsed_sec=cached["elapsed_sec"],
                    status=cached["status"],
                    input_path=Path(cached["input_path"]),
                    output_path=Path(cached["output_path"]),
                    stderr_path=Path(cached["stderr_path"]),
                    stdout_excerpt=cached["stdout_excerpt"],
                    stderr_excerpt=cached["stderr_excerpt"],
                    source_case_id=cached.get("source_case_id") or cached["id"],
                )
                continue
            pending.append(seed)
        if not pending:
            return all_ok
        worker_count = max(1, jobs if jobs is not None else default_jobs())
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = [
                pool.submit(self._evaluate_seed, seed, run_dir, params) for seed in pending
            ]
            for future in as_completed(futures):
                outcome = future.result()
                self.db.insert_case(
                    run_id=run_id,
                    seed=outcome.seed,
                    score=outcome.score,
                    elapsed_sec=outcome.elapsed_sec,
                    status=outcome.status,
                    input_path=outcome.input_path,
                    output_path=outcome.output_path,
                    stderr_path=outcome.stderr_path,
                    stdout_excerpt=outcome.stdout_excerpt,
                    stderr_excerpt=outcome.stderr_excerpt,
                )
                if outcome.status != "ok":
                    all_ok = False
        return all_ok

    def _evaluate_seed(
        self, seed: int, run_dir: Path, params: dict[str, Any] | None = None
    ) -> CaseOutcome:
        case_dir = run_dir / f"seed_{seed}"
        case_dir.mkdir(parents=True, exist_ok=True)
        input_path = case_dir / "input.txt"
        output_path = case_dir / "output.txt"
        stderr_path = case_dir / "solver.stderr.txt"
        stdout_text = ""
        stderr_text = ""
        if self.config.generator_command:
            gen_cmd = self.config.generator_command.format(
                **self._vars(seed=seed, run_dir=run_dir, input_path=input_path, output_path=output_path)
            )
            gen = run_shell(gen_cmd, self.root, timeout=self.config.generator_timeout_sec)
            if gen.returncode != 0:
                input_path.write_text(gen.stdout, encoding="utf-8")
                stderr_path.write_text(gen.stderr, encoding="utf-8")
                return CaseOutcome(
                    seed=seed,
                    score=None,
                    elapsed_sec=gen.elapsed_sec,
                    status="generator_error",
                    input_path=input_path,
                    output_path=output_path,
                    stderr_path=stderr_path,
                    stdout_excerpt=excerpt(gen.stdout),
                    stderr_excerpt=excerpt(gen.stderr),
                )
        run_cmd = self.config.run_command.format(
            **self._vars(seed=seed, run_dir=run_dir, input_path=input_path, output_path=output_path)
        )
        if params:
            # Parameters reach the solver as AHC_PARAM_<NAME> environment
            # variables, so tuning needs no source change beyond reading them.
            assignments = " ".join(
                f"AHC_PARAM_{_env_name(key)}={shlex.quote(str(value))}"
                for key, value in sorted(params.items())
            )
            run_cmd = f"env {assignments} {run_cmd}"
        solved = run_shell(run_cmd, self.root, timeout=self.config.time_limit_sec + 1.0)
        stdout_text += solved.stdout
        stderr_text += solved.stderr
        stderr_path.write_text(solved.stderr, encoding="utf-8")
        score: float | None = None
        case_status = "ok"
        if solved.returncode != 0:
            case_status = "timeout" if solved.timed_out else "runtime_error"
        elif self.config.score_command:
            score_cmd = self.config.score_command.format(
                **self._vars(seed=seed, run_dir=run_dir, input_path=input_path, output_path=output_path)
            )
            scored = run_shell(score_cmd, self.root, timeout=self.config.score_timeout_sec)
            stdout_text += scored.stdout
            stderr_text += scored.stderr
            if scored.returncode != 0:
                case_status = "score_error"
            else:
                score = self._parse_score(scored.stdout + "\n" + scored.stderr)
                if score is None:
                    case_status = "score_missing"
        return CaseOutcome(
            seed=seed,
            score=score,
            elapsed_sec=solved.elapsed_sec,
            status=case_status,
            input_path=input_path,
            output_path=output_path,
            stderr_path=stderr_path,
            stdout_excerpt=excerpt(stdout_text),
            stderr_excerpt=excerpt(stderr_text),
        )

    def _prune_reason(
        self,
        run_id: int,
        baseline_cases: dict[int, dict[str, Any]],
        policy: AcceptancePolicy,
    ) -> str | None:
        losses = 0
        for case in self.db.list_cases(run_id):
            seed = case["seed"]
            base = baseline_cases.get(seed)
            base_ok = base is not None and base["status"] == "ok" and base["score"] is not None
            case_ok = case["status"] == "ok" and case["score"] is not None
            if base_ok and not case_ok:
                return f"seed {seed} failed with status {case['status']}"
            if (
                policy.max_elapsed_sec is not None
                and case_ok
                and float(case["elapsed_sec"]) > policy.max_elapsed_sec
            ):
                return (
                    f"seed {seed} elapsed {float(case['elapsed_sec']):.3f}s "
                    f"exceeds {policy.max_elapsed_sec}s"
                )
            if base_ok and case_ok:
                delta = float(case["score"]) - float(base["score"])
                effective = delta if self.config.maximize else -delta
                if effective < 0:
                    losses += 1
        if losses > policy.max_worsening_seeds:
            return f"{losses} worsening seed(s) exceed the budget of {policy.max_worsening_seeds}"
        return None

    def _parse_score(self, text: str) -> float | None:
        match = re.search(self.config.score_regex, text)
        if not match:
            return None
        return float(match.group(1))
