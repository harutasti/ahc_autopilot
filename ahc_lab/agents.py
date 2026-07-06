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
from .policy import AcceptancePolicy, evaluate_acceptance
from .runner import run_shell
from .seeds import parse_seed_spec
from .snapshots import restore_solver_source, store_dir_for
from .workspace import create_candidate_workspace


SPECIALISTS = {
    "ProblemAnalyst": "Summarize constraints, score structure, state space, and likely search families.",
    "BeamSearchBuilder": "Improve beam width, state compression, transition generation, pruning, and duplicate removal.",
    "CutBuilder": "Improve graph cut, separator, bottleneck, multi-edge door, and switch-placement candidates.",
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
    final_baseline_run_id: int
    merged_count: int


def select_specialists(analysis: dict[str, Any], agent_count: int) -> list[str]:
    selected = ["ProblemAnalyst"]
    mean_elapsed = analysis.get("mean_elapsed_sec") or 0.0
    status_counts = analysis.get("status_counts") or {}
    low_score_cases = analysis.get("bad_seeds") or []
    if low_score_cases:
        selected.append("CutBuilder")
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
    focus: str | None = None,
) -> str:
    responsibility = SPECIALISTS.get(agent_name) or REVIEWERS.get(agent_name) or "Improve the solver."
    brief = make_ai_brief(ExperimentDB.default(root), run_id)
    problem_context = read_problem_context(root, problem)
    focus_section = focus.strip() if focus and focus.strip() else "(no explicit focus; infer one scoped improvement from the analysis)"
    return f"""You are {agent_name} in an autonomous AtCoder Heuristic Contest improvement lab.

Responsibility:
{responsibility}

Focus for this run:
{focus_section}

Project rules:
- Modify only what your role requires.
- Keep the C++20 solver valid for AtCoder submission.
- Preserve reproducibility, timer safety, and output format.
- State the hypothesis, changed files, expected score effect, and risk.
- Do not claim success without seed evaluation.
- Keep the candidate scoped to the focus. If the focus is not applicable, explain why and make no broad rewrite.

Problem: {problem}

Problem context:
{problem_context}

Current run brief:
{brief}

Structured analysis:
{analysis}

Relevant knowledge:
{knowledge_hits}

Return a concise implementation plan first. If you are connected to a writable
workspace, implement the candidate change and leave a summary.
"""


def read_problem_context(root: Path, problem: str, limit: int = 12000) -> str:
    problem_dir = root / "problems" / problem
    parts: list[str] = []
    for name in ["problem_brief.md", "statement.md"]:
        path = problem_dir / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue
        parts.append(f"## {name}\n{text}")
    joined = "\n\n".join(parts)
    if len(joined) <= limit:
        return joined or "(no statement or problem brief found)"
    return joined[:limit] + "\n...[truncated]..."


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
        roles: list[str] | None = None,
        focus: str | None = None,
        merge_accepted: bool = True,
        jobs: int | None = None,
        use_cache: bool = True,
        cascade: bool = True,
    ) -> AutopilotResult:
        session_id = self.db.create_session(self.problem, budget_sec, agent_count)
        status = "aborted"
        try:
            result = self._run_session(
                session_id=session_id,
                budget_sec=budget_sec,
                max_trials=max_trials,
                agent_count=agent_count,
                seeds=seeds,
                adapter_command=adapter_command,
                roles=roles,
                focus=focus,
                merge_accepted=merge_accepted,
                jobs=jobs,
                use_cache=use_cache,
                cascade=cascade,
            )
            status = result.status
            return result
        finally:
            self.db.finish_session(session_id, status)

    def _run_session(
        self,
        *,
        session_id: int,
        budget_sec: float | None,
        max_trials: int,
        agent_count: int,
        seeds: str,
        adapter_command: str | None,
        roles: list[str] | None,
        focus: str | None,
        merge_accepted: bool,
        jobs: int | None,
        use_cache: bool,
        cascade: bool,
    ) -> AutopilotResult:
        started = time.monotonic()
        seed_list = parse_seed_spec(seeds) or list(range(10))
        evaluator = Evaluator(self.root, self.problem, self.db)
        policy = AcceptancePolicy.from_config(evaluator.config)
        baseline_run_id = evaluator.evaluate(
            seed_list,
            tag=f"autopilot_s{session_id}_baseline",
            run_type="baseline",
            jobs=jobs,
            use_cache=use_cache,
        )
        initial_baseline_run_id = baseline_run_id
        merged_count = 0
        analysis = analyze_run(self.db, baseline_run_id)
        prompt_dir = self.root / "experiments" / "autopilot" / f"session_{session_id}" / "prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        write_analysis_markdown(
            prompt_dir.parent / "baseline_analysis.md", "Baseline Analysis", analysis
        )
        selected = roles or select_specialists(analysis, agent_count)
        knowledge_hits = search_knowledge(
            self.root,
            " ".join(
                part
                for part in [
                    self.problem,
                    "annealing beam greedy state performance parameter graph cut separator bottleneck",
                    focus or "",
                ]
                if part
            ),
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
                focus=focus,
            )
            prompt_path = prompt_dir / f"{candidate_name}.md"
            prompt_path.write_text(prompt, encoding="utf-8")
            self.db.record_agent_message(session_id, agent_name, "prompt", prompt)
            if adapter_command:
                candidate_root = Path(workspace["path"])
                try:
                    self._run_adapter(adapter_command, prompt_path, candidate_root)
                    candidate_evaluator = Evaluator(candidate_root, self.problem, self.db)
                    candidate_tag = f"autopilot_s{session_id}_{candidate_name}"
                    cascade_info: dict[str, Any] | None = None
                    if cascade:
                        candidate_run_id, cascade_info = candidate_evaluator.evaluate_cascade(
                            seed_list,
                            tag=candidate_tag,
                            baseline_run_id=baseline_run_id,
                            policy=policy,
                            run_type="candidate",
                            jobs=jobs,
                            use_cache=use_cache,
                        )
                    else:
                        candidate_run_id = candidate_evaluator.evaluate(
                            seed_list,
                            tag=candidate_tag,
                            run_type="candidate",
                            jobs=jobs,
                            use_cache=use_cache,
                        )
                    comparison = compare_runs(self.db, baseline_run_id, candidate_run_id)
                    verdict = evaluate_acceptance(comparison, policy)
                    decision = verdict["decision"]
                    if decision != "neutral":
                        self.db.update_scorecard(
                            agent_name,
                            decision == "accepted",
                            float(comparison.get("mean_effective_delta") or 0.0),
                        )
                    summary: dict[str, Any] = {**comparison, "acceptance": verdict}
                    if cascade_info is not None:
                        summary["cascade"] = cascade_info
                    if decision == "accepted" and merge_accepted:
                        summary["merged_source_hash"] = self._merge_candidate(
                            trial_id, candidate_name, candidate_run_id
                        )
                        baseline_run_id = candidate_run_id
                        analysis = analyze_run(self.db, baseline_run_id)
                        merged_count += 1
                    self.db.finish_trial(
                        trial_id,
                        status="evaluated",
                        decision=decision,
                        new_run_id=candidate_run_id,
                        summary=summary,
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
        return AutopilotResult(
            session_id=session_id,
            baseline_run_id=initial_baseline_run_id,
            prompt_dir=prompt_dir,
            selected_agents=selected,
            status="completed" if adapter_command else "prompts_generated",
            final_baseline_run_id=baseline_run_id,
            merged_count=merged_count,
        )

    def _merge_candidate(self, trial_id: int, candidate_name: str, candidate_run_id: int) -> str:
        """Copy an accepted candidate's source back into the mainline solver."""
        source_hash = self.db.get_run(candidate_run_id).get("source_hash")
        if not source_hash:
            raise RuntimeError(f"run {candidate_run_id} has no source snapshot to merge")
        restore_solver_source(store_dir_for(self.db), source_hash, self.root)
        self.db.record_patch(
            trial_id=trial_id,
            candidate_name=candidate_name,
            path=str(self.root / "solver"),
            summary=f"merged accepted source {source_hash[:12]} from run {candidate_run_id}",
            status="merged",
        )
        return source_hash

    def _run_adapter(self, command: str, prompt_path: Path, cwd: Path) -> None:
        rendered = command.format(prompt=shlex.quote(str(prompt_path)), cwd=shlex.quote(str(cwd)))
        env_cmd = os.path.expandvars(rendered)
        result = run_shell(env_cmd, cwd, timeout=600)
        log_dir = self.root / "experiments" / "adapter_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time())
        (log_dir / f"{stamp}.stdout.txt").write_text(result.stdout, encoding="utf-8")
        (log_dir / f"{stamp}.stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(
                f"adapter failed with exit code {result.returncode}; "
                f"see {log_dir / f'{stamp}.stderr.txt'}"
            )
