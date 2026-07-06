from __future__ import annotations

import json
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
from .novelty import find_duplicate, known_source_index, normalized_source_fingerprint
from .policy import AcceptancePolicy, evaluate_acceptance
from .runner import run_shell
from .seeds import parse_seed_spec
from .snapshots import (
    diff_snapshots,
    restore_solver_source,
    snapshot_solver_source,
    store_dir_for,
)
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


def build_repair_prompt(
    *,
    agent_name: str,
    candidate_name: str,
    attempt: int,
    verdict: dict[str, Any] | None,
    comparison: dict[str, Any] | None,
    error: str | None,
    original_prompt: str,
    duplicate: dict[str, Any] | None = None,
    cascade: dict[str, Any] | None = None,
) -> str:
    """Feedback prompt for one repair attempt on a rejected or failed candidate.

    The adapter is stateless, so the prompt embeds everything the agent needs:
    the gate verdict with its measured numbers (or the failure text, or the
    novelty-filter match) plus the original task prompt.
    """
    if error is not None:
        diagnosis = (
            "Your candidate failed before it could be scored:\n\n"
            f"```\n{error}\n```\n\n"
            "Fix the failure first (build error, tool crash, or timeout), then make\n"
            "sure the candidate still implements your hypothesis."
        )
    elif duplicate is not None:
        diagnosis = (
            "Your change is not novel:\n"
            f"- {duplicate['reason']}\n\n"
            "Duplicate candidates are skipped before evaluation. Propose a materially\n"
            "different change — a different hypothesis, neighborhood, or parameter\n"
            "setting — not a cosmetic edit of already-tried source."
        )
    else:
        reasons = "\n".join(f"- {r}" for r in (verdict or {}).get("reasons") or [])
        policy_desc = json.dumps((verdict or {}).get("policy") or {}, sort_keys=True)
        pruned_note = ""
        if cascade and cascade.get("pruned"):
            pruned_note = (
                "\n\nNote: evaluation was pruned early after "
                f"{cascade.get('evaluated_seed_count')} of {cascade.get('total_seed_count')} "
                f"seeds ({cascade.get('prune_reason')}). Seeds listed with status \"missing\"\n"
                "were never evaluated because of the pruning, not because the solver\n"
                "crashed on them."
            )
        diagnosis = (
            "The acceptance gate rejected your candidate for these reasons:\n"
            f"{reasons}\n\n"
            "Measured numbers (candidate vs baseline):\n"
            f"{_format_gate_numbers(comparison or {})}\n\n"
            f"Gate policy: {policy_desc}"
            f"{pruned_note}"
        )
    return f"""You are {agent_name}. Your candidate `{candidate_name}` (attempt {attempt}) was NOT accepted.

You are still in the same isolated workspace and your previous change is still
applied. Repair the candidate instead of starting over.

{diagnosis}

Repair rules:
- Diagnose the failure with the data above before editing.
- Prefer the minimal fix: repair the failing seeds, remove the regression, or
  keep only the part of your change that helps.
- If the hypothesis itself is wrong, revert toward the baseline behavior and
  try a smaller, safer variant.
- Never write to files outside your current working directory.
- Keep the C++20 solver valid for AtCoder submission; preserve output format,
  reproducibility, and timer safety.

Original task prompt (context only; the repair above takes priority):
---
{original_prompt}
"""


def _format_gate_numbers(comparison: dict[str, Any]) -> str:
    lines = [
        f"- mean effective delta: {comparison.get('mean_effective_delta')}",
        f"- median effective delta: {comparison.get('median_effective_delta')}",
        f"- wins/ties/losses: {comparison.get('wins')}/{comparison.get('ties')}/{comparison.get('losses')}",
        f"- improve confidence: {comparison.get('improve_confidence')}",
        f"- max elapsed sec: baseline {comparison.get('base_max_elapsed_sec')} -> "
        f"candidate {comparison.get('new_max_elapsed_sec')}",
    ]
    failures = comparison.get("candidate_failures") or []
    if failures:
        lines.append(f"- failing seeds: {json.dumps(failures[:10])}")
    worsening = comparison.get("worsening_seeds") or []
    if worsening:
        lines.append(f"- worst worsening seeds: {json.dumps(worsening[:10])}")
    return "\n".join(lines)


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
        repair_attempts: int = 0,
        novelty_filter: bool = True,
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
                repair_attempts=repair_attempts,
                novelty_filter=novelty_filter,
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
        repair_attempts: int,
        novelty_filter: bool,
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
            # Snapshot the known-source index once per generation, before trials
            # launch: parallel trials submitting the same diff race benignly (both
            # evaluate), instead of nondeterministically marking one a duplicate.
            novelty_index = (
                known_source_index(self.db, store_dir_for(self.db), self.problem)
                if novelty_filter
                else None
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
                        repair_attempts=repair_attempts,
                        novelty_index=novelty_index,
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
        repair_attempts: int = 0,
        novelty_index: dict[str, dict[str, int]] | None = None,
    ) -> TrialOutcome:
        """Run one trial in a worker thread with its own DB connection.

        Before evaluation, the candidate source is checked against the
        `novelty_index` of already-evaluated sources (exact and
        comment/whitespace-normalized hashes); duplicates are skipped without
        spending seed evaluations.

        With `repair_attempts` > 0, a rejected verdict, a duplicate match, or a
        failed attempt is fed back to the same workspace agent as a repair
        prompt: the gate reasons and measured numbers, so the agent fixes the
        candidate instead of the hypothesis being retried blind. Retrying
        stops early when a repair leaves the source effectively unchanged,
        since the outcome cannot change.
        """
        db = ExperimentDB(self.db.path)
        candidate_root = Path(workspace_path)
        candidate_tag = f"autopilot_s{session_id}_{candidate_name}"
        max_attempts = 1 + max(0, repair_attempts)
        original_prompt = prompt_path.read_text(encoding="utf-8")
        active_prompt_path = prompt_path
        attempts: list[dict[str, Any]] = []
        candidate_run_id: int | None = None
        summary: dict[str, Any] | None = None
        decision = "error"
        trial_status = "evaluated"
        mean_delta = 0.0
        own_exact: set[str] = set()
        own_norm: set[str] = set()
        try:
            for attempt in range(1, max_attempts + 1):
                log_suffix = "" if attempt == 1 else f"_repair{attempt - 1}"
                comparison: dict[str, Any] | None = None
                verdict: dict[str, Any] | None = None
                error_text: str | None = None
                duplicate_info: dict[str, Any] | None = None
                cascade_info: dict[str, Any] | None = None
                try:
                    self._run_adapter(
                        adapter_command,
                        active_prompt_path,
                        candidate_root,
                        trial_id=trial_id,
                        log_suffix=log_suffix,
                    )
                    exact_hash = snapshot_solver_source(candidate_root, store_dir_for(db))
                    norm_hash = (
                        normalized_source_fingerprint(candidate_root / "solver")
                        if exact_hash is not None
                        else None
                    )
                    no_change = exact_hash is not None and (
                        exact_hash in own_exact
                        or (norm_hash is not None and norm_hash in own_norm)
                    )
                    if no_change:
                        attempts.append(
                            {
                                "attempt": attempt,
                                "decision": "duplicate",
                                "reasons": ["repair produced no effective source change"],
                                "no_source_change": True,
                            }
                        )
                        if summary is None:
                            previous = attempts[-2]
                            reasons = "; ".join(str(r) for r in previous["reasons"])
                            if previous["decision"] == "duplicate":
                                decision = "duplicate"
                                trial_status = "skipped"
                                summary = {
                                    "acceptance": {
                                        "decision": "duplicate",
                                        "reasons": previous["reasons"],
                                    }
                                }
                            else:
                                decision = "error"
                                trial_status = "failed"
                                summary = {"error": reasons}
                        break
                    if exact_hash is not None:
                        own_exact.add(exact_hash)
                    if norm_hash is not None:
                        own_norm.add(norm_hash)
                    duplicate_info = find_duplicate(novelty_index, exact_hash, norm_hash)
                    if duplicate_info is not None:
                        attempts.append(
                            {
                                "attempt": attempt,
                                "decision": "duplicate",
                                "reasons": [duplicate_info["reason"]],
                                "duplicate_of_run_id": duplicate_info["run_id"],
                            }
                        )
                        if attempt == max_attempts:
                            decision = "duplicate"
                            trial_status = "skipped"
                            summary = {
                                "acceptance": {
                                    "decision": "duplicate",
                                    "reasons": [duplicate_info["reason"]],
                                },
                                "duplicate": {
                                    "match": duplicate_info["match"],
                                    "run_id": duplicate_info["run_id"],
                                },
                            }
                            break
                    else:
                        candidate_evaluator = Evaluator(candidate_root, self.problem, db)
                        parent_run_id = (
                            candidate_run_id if candidate_run_id is not None else baseline_run_id
                        )
                        if cascade:
                            run_id, cascade_info = candidate_evaluator.evaluate_cascade(
                                seed_list,
                                tag=candidate_tag + log_suffix,
                                baseline_run_id=baseline_run_id,
                                policy=policy,
                                run_type="candidate",
                                jobs=jobs,
                                use_cache=use_cache,
                                parent_run_id=parent_run_id,
                            )
                        else:
                            run_id = candidate_evaluator.evaluate(
                                seed_list,
                                tag=candidate_tag + log_suffix,
                                run_type="candidate",
                                jobs=jobs,
                                use_cache=use_cache,
                                parent_run_id=parent_run_id,
                            )
                        comparison = compare_runs(db, baseline_run_id, run_id)
                        verdict = evaluate_acceptance(comparison, policy)
                        decision = verdict["decision"]
                        attempts.append(
                            {
                                "attempt": attempt,
                                "run_id": run_id,
                                "decision": decision,
                                "reasons": verdict["reasons"],
                            }
                        )
                        candidate_run_id = run_id
                        mean_delta = float(comparison.get("mean_effective_delta") or 0.0)
                        summary = {**comparison, "acceptance": verdict}
                        if cascade_info is not None:
                            summary["cascade"] = cascade_info
                        if decision != "rejected" or attempt == max_attempts:
                            break
                except Exception as exc:
                    error_text = str(exc)
                    attempts.append(
                        {"attempt": attempt, "decision": "error", "reasons": [error_text]}
                    )
                    if attempt == max_attempts:
                        if summary is None:
                            raise
                        break
                repair_prompt = build_repair_prompt(
                    agent_name=agent_name,
                    candidate_name=candidate_name,
                    attempt=attempt,
                    verdict=verdict,
                    comparison=comparison,
                    error=error_text,
                    original_prompt=original_prompt,
                    duplicate=duplicate_info,
                    cascade=cascade_info,
                )
                active_prompt_path = prompt_path.with_name(
                    f"{candidate_name}_repair{attempt}.md"
                )
                active_prompt_path.write_text(repair_prompt, encoding="utf-8")
                db.record_agent_message(session_id, agent_name, "repair_prompt", repair_prompt)
            assert summary is not None  # loop always breaks with a summary or raises
            if len(attempts) > 1:
                summary["repair"] = {"attempts": attempts}
            if decision in ("rejected", "duplicate"):
                db.update_scorecard(agent_name, False, mean_delta)
            db.finish_trial(
                trial_id,
                status=trial_status,
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
            error_summary: dict[str, Any] = {"error": str(exc)}
            if len(attempts) > 1:
                error_summary["repair"] = {"attempts": attempts}
            db.finish_trial(
                trial_id,
                status="failed",
                decision="error",
                summary=error_summary,
            )
            return TrialOutcome(
                trial_id=trial_id,
                agent_name=agent_name,
                candidate_name=candidate_name,
                workspace_path=workspace_path,
                decision="error",
                summary=error_summary,
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
            repair_attempts = (summary.get("repair") or {}).get("attempts") or []
            if repair_attempts:
                path_desc = " -> ".join(str(a.get("decision")) for a in repair_attempts)
                lines.append(f"- repair attempts: {len(repair_attempts)} ({path_desc})\n")
            if cascade.get("pruned"):
                lines.append(f"- pruned early: {cascade.get('prune_reason')}\n")
            agent_note = self._adapter_log_tail(outcome.trial_id)
            if agent_note:
                lines.append(f"- agent summary tail: {agent_note}\n")
        with path.open("a", encoding="utf-8") as fh:
            fh.write("".join(lines))

    def _adapter_log_tail(self, trial_id: int, limit: int = 400) -> str:
        """Tail of the agent's stdout from the trial's latest (repair) attempt."""
        log_dir = self.root / "experiments" / "adapter_logs"
        path = log_dir / f"trial_{trial_id}.stdout.txt"
        if log_dir.exists():
            repairs = sorted(
                log_dir.glob(f"trial_{trial_id}_repair*.stdout.txt"),
                key=lambda p: int(p.name.split("_repair", 1)[1].split(".", 1)[0]),
            )
            if repairs:
                path = repairs[-1]
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
        self,
        command: str,
        prompt_path: Path,
        cwd: Path,
        trial_id: int | None = None,
        log_suffix: str = "",
    ) -> None:
        rendered = command.format(prompt=shlex.quote(str(prompt_path)), cwd=shlex.quote(str(cwd)))
        env_cmd = os.path.expandvars(rendered)
        timeout = float(os.environ.get("AHC_ADAPTER_TIMEOUT_SEC", "600"))
        result = run_shell(env_cmd, cwd, timeout=timeout)
        log_dir = self.root / "experiments" / "adapter_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stem = f"trial_{trial_id}{log_suffix}" if trial_id is not None else str(int(time.time()))
        (log_dir / f"{stem}.stdout.txt").write_text(result.stdout, encoding="utf-8")
        (log_dir / f"{stem}.stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(
                f"adapter failed with exit code {result.returncode}; "
                f"see {log_dir / f'{stem}.stderr.txt'}"
            )
