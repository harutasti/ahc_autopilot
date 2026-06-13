from __future__ import annotations

import os
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analysis import analyze_run, compare_runs, make_ai_brief, write_analysis_markdown
from .db import ExperimentDB
from .evaluator import Evaluator
from .knowledge import search_knowledge
from .runner import run_shell
from .seeds import parse_seed_spec
from .workspace import create_candidate_workspace


SPECIALISTS = {
    "ProblemAnalyst": "Summarize constraints, score structure, state space, and likely search families.",
    "BeamSearchBuilder": "Improve beam width, state compression, transition generation, pruning, and duplicate removal.",
    "AnnealingBuilder": "Improve neighborhoods, delta evaluation, rollback, temperature schedule, and time allocation.",
    "GreedyConstructiveBuilder": "Improve initial solutions, greedy ordering, and constructive heuristics.",
    "StateDesignBuilder": "Improve state representation, score decomposition, caches, undo, and redo.",
    "PerformanceBuilder": "Reduce hot-path cost, memory churn, loops, random overhead, and avoid TLE.",
    "ParameterTuner": "Search robust parameter settings with grid or random trials.",
}

REVIEWERS = {
    "CorrectnessReviewer": "Check input/output format, constraints, UB, reproducibility, and tester failures.",
    "PerformanceReviewer": "Check TLE risk, memory growth, evaluation count, and hot-path regressions.",
    "SearchQualityReviewer": "Check whether the search hypothesis fits the problem and avoids shallow local optima.",
}


@dataclass(frozen=True)
class AutopilotResult:
    session_id: int
    baseline_run_id: int
    prompt_dir: Path
    selected_agents: list[str]
    status: str


def select_specialists(analysis: dict[str, Any], agent_count: int) -> list[str]:
    selected = ["ProblemAnalyst"]
    mean_elapsed = analysis.get("mean_elapsed_sec") or 0.0
    status_counts = analysis.get("status_counts") or {}
    if status_counts.get("timeout", 0) or mean_elapsed > 1.0:
        selected.append("PerformanceBuilder")
    selected.extend(["AnnealingBuilder", "StateDesignBuilder", "ParameterTuner"])
    ordered: list[str] = []
    for agent in selected:
        if agent not in ordered:
            ordered.append(agent)
    return ordered[: max(1, agent_count)]


def build_agent_prompt(
    *,
    root: Path,
    problem: str,
    agent_name: str,
    run_id: int,
    analysis: dict[str, Any],
    knowledge_hits: list[dict[str, Any]],
) -> str:
    responsibility = SPECIALISTS.get(agent_name) or REVIEWERS.get(agent_name) or "Improve the solver."
    brief = make_ai_brief(ExperimentDB.default(root), run_id)
    return f"""You are {agent_name} in an autonomous AtCoder Heuristic Contest improvement lab.

Responsibility:
{responsibility}

Project rules:
- Modify only what your role requires.
- Keep the C++20 solver valid for AtCoder submission.
- Preserve reproducibility, timer safety, and output format.
- State the hypothesis, changed files, expected score effect, and risk.
- Do not claim success without seed evaluation.

Problem: {problem}

Current run brief:
{brief}

Structured analysis:
{analysis}

Relevant knowledge:
{knowledge_hits}

Return a concise implementation plan first. If you are connected to a writable
workspace, implement the candidate change and leave a summary.
"""


class Autopilot:
    def __init__(self, root: Path, problem: str, db: ExperimentDB | None = None):
        self.root = root.resolve()
        self.problem = problem
        self.db = db or ExperimentDB.default(self.root)

    def run(
        self,
        *,
        budget_sec: float | None,
        max_trials: int,
        agent_count: int,
        seeds: str,
        adapter_command: str | None = None,
    ) -> AutopilotResult:
        session_id = self.db.create_session(self.problem, budget_sec, agent_count)
        started = time.monotonic()
        seed_list = parse_seed_spec(seeds) or list(range(10))
        evaluator = Evaluator(self.root, self.problem, self.db)
        baseline_run_id = evaluator.evaluate(
            seed_list, tag=f"autopilot_s{session_id}_baseline", run_type="baseline"
        )
        analysis = analyze_run(self.db, baseline_run_id)
        prompt_dir = self.root / "experiments" / "autopilot" / f"session_{session_id}" / "prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        write_analysis_markdown(
            prompt_dir.parent / "baseline_analysis.md", "Baseline Analysis", analysis
        )
        selected = select_specialists(analysis, agent_count)
        knowledge_hits = search_knowledge(
            self.root,
            f"{self.problem} annealing beam greedy state performance parameter",
            limit=6,
        )
        for idx, agent_name in enumerate(selected[:max_trials], start=1):
            if budget_sec is not None and time.monotonic() - started > budget_sec:
                break
            candidate_name = f"candidate_{idx}_{agent_name}"
            trial_id = self.db.create_trial(
                session_id=session_id,
                role=agent_name,
                candidate_name=candidate_name,
                base_run_id=baseline_run_id,
            )
            workspace = create_candidate_workspace(
                self.root, session_id=session_id, candidate_name=candidate_name
            )
            prompt = build_agent_prompt(
                root=self.root,
                problem=self.problem,
                agent_name=agent_name,
                run_id=baseline_run_id,
                analysis=analysis,
                knowledge_hits=knowledge_hits,
            )
            prompt_path = prompt_dir / f"{candidate_name}.md"
            prompt_path.write_text(prompt, encoding="utf-8")
            self.db.record_agent_message(session_id, agent_name, "prompt", prompt)
            if adapter_command:
                candidate_root = Path(workspace["path"])
                self._run_adapter(adapter_command, prompt_path, candidate_root)
                try:
                    candidate_run_id = Evaluator(candidate_root, self.problem, self.db).evaluate(
                        seed_list,
                        tag=f"autopilot_s{session_id}_{candidate_name}",
                        run_type="candidate",
                    )
                    comparison = compare_runs(self.db, baseline_run_id, candidate_run_id)
                    accepted = _accept_conservative(comparison)
                    decision = "accepted" if accepted else "rejected"
                    self.db.update_scorecard(
                        agent_name,
                        accepted,
                        float(comparison.get("mean_effective_delta") or 0.0),
                    )
                    self.db.finish_trial(
                        trial_id,
                        status="evaluated",
                        decision=decision,
                        new_run_id=candidate_run_id,
                        summary=comparison,
                    )
                except Exception as exc:
                    self.db.finish_trial(
                        trial_id,
                        status="failed",
                        decision="error",
                        summary={"error": str(exc)},
                    )
            else:
                self.db.finish_trial(
                    trial_id,
                    status="prompt_only",
                    decision="pending_adapter",
                    summary={"prompt_path": str(prompt_path), "workspace": workspace["path"]},
                )
        self.db.finish_session(session_id, "prompts_generated")
        return AutopilotResult(
            session_id=session_id,
            baseline_run_id=baseline_run_id,
            prompt_dir=prompt_dir,
            selected_agents=selected,
            status="prompts_generated",
        )

    def _run_adapter(self, command: str, prompt_path: Path, cwd: Path) -> None:
        rendered = command.format(prompt=shlex.quote(str(prompt_path)), cwd=shlex.quote(str(cwd)))
        env_cmd = os.path.expandvars(rendered)
        result = run_shell(env_cmd, cwd, timeout=600)
        log_dir = self.root / "experiments" / "adapter_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time())
        (log_dir / f"{stamp}.stdout.txt").write_text(result.stdout, encoding="utf-8")
        (log_dir / f"{stamp}.stderr.txt").write_text(result.stderr, encoding="utf-8")


def _accept_conservative(comparison: dict[str, Any]) -> bool:
    common = int(comparison.get("common_seed_count") or 0)
    if common == 0:
        return False
    mean_delta = comparison.get("mean_effective_delta")
    median_delta = comparison.get("median_effective_delta")
    losses = int(comparison.get("losses") or 0)
    return bool(
        mean_delta is not None
        and median_delta is not None
        and float(mean_delta) > 0.0
        and float(median_delta) >= 0.0
        and losses == 0
    )
