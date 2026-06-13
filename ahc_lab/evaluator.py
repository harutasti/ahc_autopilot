from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ProblemConfig, load_problem_config
from .db import ExperimentDB
from .runner import excerpt, run_shell


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    command: str
    solver_path: Path
    stdout: str
    stderr: str
    elapsed_sec: float


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
    ) -> int:
        build = self.build() if build_first else None
        if build is not None and not build.ok:
            raise RuntimeError(f"build failed:\n{build.stderr or build.stdout}")
        run_id = self.db.create_run(
            problem=self.config.name,
            tag=tag,
            run_type=run_type,
            score_direction=self.config.score_direction,
            solver_path=self.solver_path,
            build_command=self.config.build_command,
            params=params,
        )
        run_dir = self.root / "experiments" / "runs" / f"run_{run_id:06d}_{tag}"
        run_dir.mkdir(parents=True, exist_ok=True)
        status = "done"
        for seed in seeds:
            case_dir = run_dir / f"seed_{seed}"
            case_dir.mkdir(parents=True, exist_ok=True)
            input_path = case_dir / "input.txt"
            output_path = case_dir / "output.txt"
            stderr_path = case_dir / "solver.stderr.txt"
            score: float | None = None
            case_status = "ok"
            stdout_text = ""
            stderr_text = ""
            if self.config.generator_command:
                gen_cmd = self.config.generator_command.format(
                    **self._vars(seed=seed, run_dir=run_dir, input_path=input_path, output_path=output_path)
                )
                gen = run_shell(gen_cmd, self.root, timeout=30)
                if gen.returncode != 0:
                    case_status = "generator_error"
                    stderr_text = gen.stderr
                    status = "failed"
                    input_path.write_text(gen.stdout, encoding="utf-8")
                    stderr_path.write_text(gen.stderr, encoding="utf-8")
                    self.db.insert_case(
                        run_id=run_id,
                        seed=seed,
                        score=None,
                        elapsed_sec=gen.elapsed_sec,
                        status=case_status,
                        input_path=input_path,
                        output_path=output_path,
                        stderr_path=stderr_path,
                        stdout_excerpt=excerpt(gen.stdout),
                        stderr_excerpt=excerpt(gen.stderr),
                    )
                    continue
            run_cmd = self.config.run_command.format(
                **self._vars(seed=seed, run_dir=run_dir, input_path=input_path, output_path=output_path)
            )
            solved = run_shell(run_cmd, self.root, timeout=self.config.time_limit_sec + 1.0)
            stdout_text += solved.stdout
            stderr_text += solved.stderr
            stderr_path.write_text(solved.stderr, encoding="utf-8")
            if solved.returncode != 0:
                case_status = "timeout" if solved.timed_out else "runtime_error"
                status = "failed"
            elif self.config.score_command:
                score_cmd = self.config.score_command.format(
                    **self._vars(seed=seed, run_dir=run_dir, input_path=input_path, output_path=output_path)
                )
                scored = run_shell(score_cmd, self.root, timeout=30)
                stdout_text += scored.stdout
                stderr_text += scored.stderr
                if scored.returncode != 0:
                    case_status = "score_error"
                    status = "failed"
                else:
                    score = self._parse_score(scored.stdout + "\n" + scored.stderr)
                    if score is None:
                        case_status = "score_missing"
                        status = "failed"
            self.db.insert_case(
                run_id=run_id,
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
        self.db.finish_run(run_id, status=status)
        return run_id

    def _parse_score(self, text: str) -> float | None:
        match = re.search(self.config.score_regex, text)
        if not match:
            return None
        return float(match.group(1))

