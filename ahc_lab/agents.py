from __future__ import annotations

import os
import shlex
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analysis import analyze_run, compare_runs, make_ai_brief, write_analysis_markdown
from .db import ExperimentDB
from .evaluator import Evaluator, default_jobs
from .knowledge import search_knowledge
from .policy import AcceptancePolicy, evaluate_acceptance
from .runner import run_shell
from .seeds import parse_seed_spec
from .snapshots import diff_snapshots, restore_solver_source, store_dir_for
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
    generations_run: int


@dataclass
class TrialOutcome:
    trial_id: int
    agent_name: str
    candidate_name: str
    workspace_path: str
    decision: str
    candidate_run_id: int | None = None
    mean_effective_delta: float = 0.0
    summary: dict[str, Any] | None = None


def select_specialists(analysis: dict[str, Any], agent_count: int) -> list[str]:
    """Pick builder roles for implementation trials from the latest analysis.

    Analysis-only roles such as ProblemAnalyst are excluded: they produce no
    candidate source, so giving them a trial slot wastes an evaluation. Use
    `--roles` to request them explicitly.
    """
    selected: list[str] = []
    mean_elapsed = analysis.get("mean_elapsed_sec") or 0.0
    status_counts = analysis.get("status_counts") or {}
    if analysis.get("bad_seeds"):
        selected.append("CutBuilder")
    if status_counts.get("timeout", 0) or mean_elapsed > 1.0:
        selected.append("PerformanceBuilder")
    selected.extend(
        ["AnnealingBuilder", "StateDesignBuilder", "GreedyConstructiveBuilder", "ParameterTuner"]
    )
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
    recent_changes: str = "",
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
- Never write to files outside your current working directory; it is your
  isolated candidate workspace. Notes belong in the workspace, not the shared
  project root.
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

Recent accepted changes (already merged into your workspace; build on them, do not undo them):
{recent_changes or "(none yet)"}

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
        generations: int = 1,
        validation_seeds: str | None = None,
        trial_jobs: int | None = None,
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
                generations=generations,
                validation_seeds=validation_seeds,
                trial_jobs=trial_jobs,
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
        generations: int,
        validation_seeds: str | None,
        trial_jobs: int | None,
    ) -> AutopilotResult:
        started = time.monotonic()
        seed_list = parse_seed_spec(seeds) or list(range(10))
        validation_list = parse_seed_spec(validation_seeds) if validation_seeds else []
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
        generations_run = 0
        analysis = analyze_run(self.db, baseline_run_id)
        prompt_dir = self.root / "experiments" / "autopilot" / f"session_{session_id}" / "prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        write_analysis_markdown(
            prompt_dir.parent / "baseline_analysis.md", "Baseline Analysis", analysis
        )
        all_selected: list[str] = []
        for generation in range(1, max(1, generations) + 1):
            if budget_sec is not None and time.monotonic() - started > budget_sec:
                break
            generations_run = generation
            selected = roles or select_specialists(analysis, agent_count)
            for agent in selected:
                if agent not in all_selected:
                    all_selected.append(agent)
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
            recent_changes = self._recent_accepted_diffs()
            trials: list[dict[str, Any]] = []
            for idx, agent_name in enumerate(selected[:max_trials], start=1):
                candidate_name = f"g{generation}_candidate_{idx}_{agent_name}"
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
                    recent_changes=recent_changes,
                )
                prompt_path = prompt_dir / f"{candidate_name}.md"
                prompt_path.write_text(prompt, encoding="utf-8")
                self.db.record_agent_message(session_id, agent_name, "prompt", prompt)
                trials.append(
                    {
                        "trial_id": trial_id,
                        "agent_name": agent_name,
                        "candidate_name": candidate_name,
                        "workspace_path": workspace["path"],
                        "prompt_path": prompt_path,
                    }
                )
            if not adapter_command:
                for trial in trials:
                    self.db.finish_trial(
                        trial["trial_id"],
                        status="prompt_only",
                        decision="pending_adapter",
                        summary={
                            "prompt_path": str(trial["prompt_path"]),
                            "workspace": trial["workspace_path"],
                        },
                    )
                break
            per_trial_jobs = (
                jobs if jobs is not None else max(1, default_jobs() // max(1, len(trials)))
            )
            outcomes: list[TrialOutcome] = []
            with ThreadPoolExecutor(max_workers=max(1, trial_jobs or len(trials))) as pool:
                futures = [
                    pool.submit(
                        self._execute_trial,
                        adapter_command=adapter_command,
                        baseline_run_id=baseline_run_id,
                        seed_list=seed_list,
                        policy=policy,
                        jobs=per_trial_jobs,
                        use_cache=use_cache,
                        cascade=cascade,
                        session_id=session_id,
                        **trial,
                    )
                    for trial in trials
                ]
                for future in as_completed(futures):
                    outcomes.append(future.result())
            accepted = sorted(
                [o for o in outcomes if o.decision == "accepted"],
                key=lambda o: o.mean_effective_delta,
                reverse=True,
            )
            merged_this_generation = False
            for outcome in accepted:
                if not merge_accepted or merged_this_generation:
                    self.db.update_scorecard(outcome.agent_name, True, outcome.mean_effective_delta)
                    continue
                if validation_list:
                    passed, validation = self._validate_candidate(
                        outcome,
                        validation_list,
                        policy,
                        session_id,
                        generation,
                        jobs=per_trial_jobs,
                        use_cache=use_cache,
                    )
                    if not passed:
                        self.db.update_scorecard(
                            outcome.agent_name, False, outcome.mean_effective_delta
                        )
                        self.db.finish_trial(
                            outcome.trial_id,
                            status="evaluated",
                            decision="validation_failed",
                            new_run_id=outcome.candidate_run_id,
                            summary={**(outcome.summary or {}), "validation": validation},
                        )
                        continue
                    outcome.summary = {**(outcome.summary or {}), "validation": validation}
                merged_hash = self._merge_candidate(
                    outcome.trial_id, outcome.candidate_name, outcome.candidate_run_id
                )
                self.db.update_scorecard(outcome.agent_name, True, outcome.mean_effective_delta)
                self.db.finish_trial(
                    outcome.trial_id,
                    status="evaluated",
                    decision="accepted",
                    new_run_id=outcome.candidate_run_id,
                    summary={**(outcome.summary or {}), "merged_source_hash": merged_hash},
                )
                baseline_run_id = outcome.candidate_run_id
                analysis = analyze_run(self.db, baseline_run_id)
                merged_count += 1
                merged_this_generation = True
            self._record_insights(generation, outcomes)
            if not merge_accepted:
                break
            if not merged_this_generation:
                break
        return AutopilotResult(
            session_id=session_id,
            baseline_run_id=initial_baseline_run_id,
            prompt_dir=prompt_dir,
            selected_agents=all_selected,
            status="completed" if adapter_command else "prompts_generated",
            final_baseline_run_id=baseline_run_id,
            merged_count=merged_count,
            generations_run=generations_run,
        )

    def _execute_trial(
        self,
        *,
        trial_id: int,
        agent_name: str,
        candidate_name: str,
        workspace_path: str,
        prompt_path: Path,
        adapter_command: str,
        baseline_run_id: int,
        seed_list: list[int],
        policy: AcceptancePolicy,
        jobs: int,
        use_cache: bool,
        cascade: bool,
        session_id: int,
    ) -> TrialOutcome:
        """Run one trial in a worker thread with its own DB connection."""
        db = ExperimentDB(self.db.path)
        candidate_root = Path(workspace_path)
        try:
            try:
                self._run_adapter(adapter_command, prompt_path, candidate_root, trial_id=trial_id)
                candidate_evaluator = Evaluator(candidate_root, self.problem, db)
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
                comparison = compare_runs(db, baseline_run_id, candidate_run_id)
                verdict = evaluate_acceptance(comparison, policy)
                decision = verdict["decision"]
                mean_delta = float(comparison.get("mean_effective_delta") or 0.0)
                if decision == "rejected":
                    db.update_scorecard(agent_name, False, mean_delta)
                summary: dict[str, Any] = {**comparison, "acceptance": verdict}
                if cascade_info is not None:
                    summary["cascade"] = cascade_info
                db.finish_trial(
                    trial_id,
                    status="evaluated",
                    decision=decision,
                    new_run_id=candidate_run_id,
                    summary=summary,
                )
                return TrialOutcome(
                    trial_id=trial_id,
                    agent_name=agent_name,
                    candidate_name=candidate_name,
                    workspace_path=workspace_path,
                    decision=decision,
                    candidate_run_id=candidate_run_id,
                    mean_effective_delta=mean_delta,
                    summary=summary,
                )
            except Exception as exc:
                db.finish_trial(
                    trial_id,
                    status="failed",
                    decision="error",
                    summary={"error": str(exc)},
                )
                return TrialOutcome(
                    trial_id=trial_id,
                    agent_name=agent_name,
                    candidate_name=candidate_name,
                    workspace_path=workspace_path,
                    decision="error",
                    summary={"error": str(exc)},
                )
        finally:
            db.close()

    def _validate_candidate(
        self,
        outcome: TrialOutcome,
        validation_list: list[int],
        policy: AcceptancePolicy,
        session_id: int,
        generation: int,
        *,
        jobs: int | None,
        use_cache: bool,
    ) -> tuple[bool, dict[str, Any]]:
        """Confirm an accepted candidate on held-out seeds before merging.

        The candidate must not be rejected by the same gate on the validation
        seeds; `neutral` passes, since validation is a non-regression check.
        """
        try:
            baseline_val = Evaluator(self.root, self.problem, self.db).evaluate(
                validation_list,
                tag=f"autopilot_s{session_id}_g{generation}_validation_baseline",
                run_type="validation",
                jobs=jobs,
                use_cache=use_cache,
            )
            candidate_val = Evaluator(Path(outcome.workspace_path), self.problem, self.db).evaluate(
                validation_list,
                tag=f"autopilot_s{session_id}_{outcome.candidate_name}_validation",
                run_type="validation",
                jobs=jobs,
                use_cache=use_cache,
                parent_run_id=outcome.candidate_run_id,
            )
            comparison = compare_runs(self.db, baseline_val, candidate_val)
            verdict = evaluate_acceptance(comparison, policy)
        except Exception as exc:
            return False, {"error": str(exc)}
        passed = verdict["decision"] != "rejected"
        return passed, {
            "baseline_run_id": baseline_val,
            "candidate_run_id": candidate_val,
            "acceptance": verdict,
        }

    def _recent_accepted_diffs(self, limit: int = 2) -> str:
        """Unified diffs of the latest merged candidates, for prompt inspiration."""
        sections: list[str] = []
        for patch in self.db.list_merged_patches(limit):
            if not patch.get("base_run_id") or not patch.get("new_run_id"):
                continue
            base_hash = self.db.get_run(patch["base_run_id"]).get("source_hash")
            new_hash = self.db.get_run(patch["new_run_id"]).get("source_hash")
            if not base_hash or not new_hash:
                continue
            diff = diff_snapshots(store_dir_for(self.db), base_hash, new_hash, limit_chars=2500)
            if diff:
                sections.append(
                    f"### {patch['role']} ({patch['candidate_name']})\n```diff\n{diff}\n```"
                )
        return "\n\n".join(sections)

    def _record_insights(self, generation: int, outcomes: list[TrialOutcome]) -> None:
        """Append structured trial results to the problem knowledge notes.

        These entries feed future prompts through knowledge search, so failed
        hypotheses are not retried and accepted directions are reinforced.
        """
        if not outcomes:
            return
        knowledge_dir = self.root / "knowledge"
        knowledge_dir.mkdir(exist_ok=True)
        path = knowledge_dir / f"{self.problem}_autopilot.md"
        lines: list[str] = []
        if not path.exists():
            lines.append(f"# {self.problem} autopilot insights\n\nAuto-generated per generation.\n")
        stamp = time.strftime("%Y-%m-%d %H:%M")
        for outcome in sorted(outcomes, key=lambda o: o.trial_id):
            trial = self.db.get_trial(outcome.trial_id)
            summary = outcome.summary or {}
            acceptance = summary.get("acceptance") or {}
            cascade = summary.get("cascade") or {}
            lines.append(f"\n## {stamp} g{generation} {trial['role']} -> {trial['decision']}\n")
            if summary.get("mean_effective_delta") is not None:
                lines.append(
                    f"- mean effective delta {summary['mean_effective_delta']}, "
                    f"confidence {summary.get('improve_confidence')}, "
                    f"wins/ties/losses {summary.get('wins')}/{summary.get('ties')}/{summary.get('losses')}\n"
                )
            reasons = acceptance.get("reasons") or []
            if summary.get("error"):
                reasons = [summary["error"], *reasons]
            if reasons:
                lines.append(f"- verdict: {'; '.join(str(r) for r in reasons if r)}\n")
            if cascade.get("pruned"):
                lines.append(f"- pruned early: {cascade.get('prune_reason')}\n")
            agent_note = self._adapter_log_tail(outcome.trial_id)
            if agent_note:
                lines.append(f"- agent summary tail: {agent_note}\n")
        with path.open("a", encoding="utf-8") as fh:
            fh.write("".join(lines))

    def _adapter_log_tail(self, trial_id: int, limit: int = 400) -> str:
        path = self.root / "experiments" / "adapter_logs" / f"trial_{trial_id}.stdout.txt"
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        return " ".join(text[-limit:].split())

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

    def _run_adapter(
        self, command: str, prompt_path: Path, cwd: Path, trial_id: int | None = None
    ) -> None:
        rendered = command.format(prompt=shlex.quote(str(prompt_path)), cwd=shlex.quote(str(cwd)))
        env_cmd = os.path.expandvars(rendered)
        timeout = float(os.environ.get("AHC_ADAPTER_TIMEOUT_SEC", "600"))
        result = run_shell(env_cmd, cwd, timeout=timeout)
        log_dir = self.root / "experiments" / "adapter_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stem = f"trial_{trial_id}" if trial_id is not None else str(int(time.time()))
        (log_dir / f"{stem}.stdout.txt").write_text(result.stdout, encoding="utf-8")
        (log_dir / f"{stem}.stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(
                f"adapter failed with exit code {result.returncode}; "
                f"see {log_dir / f'{stem}.stderr.txt'}"
            )
