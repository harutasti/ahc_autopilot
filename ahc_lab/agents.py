from __future__ import annotations

import json
import os
import random
import re
import shlex
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analysis import analyze_run, compare_runs, make_ai_brief, write_analysis_markdown
from .archive import ArchiveEntry, build_archive, sample_parents
from .config import load_problem_config
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


class AdapterFatalError(RuntimeError):
    """Adapter failure that retrying cannot fix (rate limit, auth, misconfig)."""


class AdapterTimeoutError(RuntimeError):
    """Adapter was killed at the configured timeout; partial work may remain."""


# Output patterns whose failures cannot be fixed by retrying the same call.
_FATAL_ADAPTER_PATTERNS: list[tuple[str, str]] = [
    ("rate_limit", r"session limit|rate.?limit|usage limit|quota exceeded"),
    ("auth", r"authentication|unauthorized|invalid.?api.?key|not logged in|credential"),
    ("config", r"cannot be used with root|unknown option|unrecognized argument|command not found"),
]


def classify_adapter_failure(output: str) -> str | None:
    """Classify adapter output as a fatal failure kind, or None if retryable."""
    lowered = output.lower()
    for kind, pattern in _FATAL_ADAPTER_PATTERNS:
        if re.search(pattern, lowered):
            return kind
    return None


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
    archive_note: str = "",
    responsibility: str | None = None,
) -> str:
    responsibility = (
        responsibility
        or SPECIALISTS.get(agent_name)
        or REVIEWERS.get(agent_name)
        or "Improve the solver."
    )
    brief = make_ai_brief(ExperimentDB.default(root), run_id)
    problem_context = read_problem_context(root, problem)
    focus_section = focus.strip() if focus and focus.strip() else "(no explicit focus; infer one scoped improvement from the analysis)"
    archive_section = f"\nLineage:\n{archive_note}\n" if archive_note else ""
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
{archive_section}
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


def build_orchestrator_prompt(
    *,
    root: Path,
    problem: str,
    run_id: int,
    analysis: dict[str, Any],
    knowledge_hits: list[dict[str, Any]],
    agent_count: int,
    focus: str | None = None,
    recent_changes: str = "",
) -> str:
    """Prompt asking the adapter LLM to design this generation's trial roles."""
    brief = make_ai_brief(ExperimentDB.default(root), run_id)
    problem_context = read_problem_context(root, problem)
    catalog = "\n".join(f"- {name}: {desc}" for name, desc in SPECIALISTS.items())
    focus_line = focus.strip() if focus and focus.strip() else "(none)"
    return f"""You are the Orchestrator of an autonomous AtCoder Heuristic Contest improvement lab.

Task: design the specialist roles for the next generation of solver-improvement
trials. Study the analysis below, decide which distinct improvement directions
are most promising right now, and write them to a file named `roles.json` in
your current working directory.

Output format (a JSON array with at most {agent_count} entries):
[
  {{"role": "ShortCamelCaseName",
    "focus": "one scoped, testable improvement hypothesis",
    "responsibility": "one sentence describing what this specialist owns"}}
]

Rules:
- Each role must target a different improvement direction; no near-duplicates.
- Focus statements must be concrete and scoped to one change, grounded in the
  analysis (bad seeds, timing, score distribution), not generic advice.
- Each focus must be completable by ONE agent invocation (a bounded coding
  session). If a promising direction is too large, assign only its smallest
  independently-evaluable first stage and note the follow-up in the focus.
- If the knowledge section records repeated timeouts or failures for a
  direction, do not re-assign it unchanged; either stage it down or drop it.
- Role names may use letters, digits, and underscore only.
- Write only `roles.json`; do not modify any solver source.

Operator focus constraint (every role must stay compatible with it):
{focus_line}

Problem: {problem}

Problem context:
{problem_context}

Current run brief:
{brief}

Structured analysis:
{analysis}

Relevant knowledge:
{knowledge_hits}

Recent accepted changes:
{recent_changes or "(none yet)"}

Example role catalog (for inspiration; reuse these or invent better ones):
{catalog}
"""


def parse_roles_file(path: Path, limit: int) -> list[dict[str, str]]:
    """Parse and sanitize the orchestrator's `roles.json`.

    Role names are restricted to ``[A-Za-z0-9_]`` because they become part of
    workspace and prompt file names. Entries without a usable role or focus
    are dropped; raises ValueError when nothing valid remains, so the caller
    can fall back to the static specialist catalog.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("roles.json must be a JSON array")
    specs: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        role = re.sub(r"[^A-Za-z0-9_]", "", str(entry.get("role") or ""))[:40]
        focus = str(entry.get("focus") or "").strip()[:2000]
        if not role or not focus or role in seen:
            continue
        seen.add(role)
        specs.append(
            {
                "role": role,
                "focus": focus,
                "responsibility": str(entry.get("responsibility") or "").strip()[:500],
            }
        )
        if len(specs) >= max(1, limit):
            break
    if not specs:
        raise ValueError("roles.json contained no valid role entries")
    return specs


def _elapsed_only_rejection(verdict: dict[str, Any]) -> bool:
    """True when every rejection reason is the max-elapsed gate."""
    reasons = verdict.get("reasons") or []
    return bool(reasons) and all(str(r).startswith("max elapsed") for r in reasons)


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
        # Source lives at <root>/<solver_rel> (per-problem, defaults to solver/).
        self.solver_rel = load_problem_config(self.root, problem).solver_rel

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
        archive_parents: bool = False,
        archive_rng: random.Random | None = None,
        dynamic_roles: bool = False,
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
                archive_parents=archive_parents,
                archive_rng=archive_rng,
                dynamic_roles=dynamic_roles,
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
        archive_parents: bool,
        archive_rng: random.Random | None,
        dynamic_roles: bool,
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
        adapter_fatal = False
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
            trial_specs = self._select_trial_specs(
                session_id=session_id,
                generation=generation,
                adapter_command=adapter_command,
                dynamic_roles=dynamic_roles,
                roles=roles,
                analysis=analysis,
                knowledge_hits=knowledge_hits,
                recent_changes=recent_changes,
                agent_count=agent_count,
                focus=focus,
                baseline_run_id=baseline_run_id,
                prompt_dir=prompt_dir,
            )
            for spec in trial_specs:
                if spec["role"] not in all_selected:
                    all_selected.append(spec["role"])
            parents: list[ArchiveEntry] = []
            if archive_parents and adapter_command:
                session_archive = build_archive(
                    self.db, self.problem, f"autopilot_s{session_id}_"
                )
                parents = sample_parents(
                    session_archive, min(len(trial_specs), max_trials), rng=archive_rng
                )
            trials: list[dict[str, Any]] = []
            for idx, spec in enumerate(trial_specs[:max_trials], start=1):
                agent_name = spec["role"]
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
                parent = parents[idx - 1] if idx <= len(parents) else None
                prompt_run_id = baseline_run_id
                prompt_analysis = analysis
                archive_note = ""
                archive_parent_info: dict[str, Any] | None = None
                if parent is not None:
                    archive_parent_info = {
                        "run_id": parent.run_id,
                        "source_hash": parent.source_hash,
                        "mean_score": parent.mean_score,
                        "children": parent.children,
                    }
                    if parent.run_id != baseline_run_id:
                        restore_solver_source(
                            store_dir_for(self.db),
                            parent.source_hash,
                            Path(workspace["path"]) / self.solver_rel,
                        )
                        prompt_run_id = parent.run_id
                        prompt_analysis = analyze_run(self.db, parent.run_id)
                    archive_note = (
                        f"Your workspace starts from archive parent run {parent.run_id} "
                        f"(mean score {parent.mean_score}), sampled from the lineage tree "
                        "by fitness and novelty. It may differ from the current mainline. "
                        "The acceptance gate still compares your candidate against "
                        f"mainline baseline run {baseline_run_id}."
                    )
                prompt = build_agent_prompt(
                    root=self.root,
                    problem=self.problem,
                    agent_name=agent_name,
                    run_id=prompt_run_id,
                    analysis=prompt_analysis,
                    knowledge_hits=knowledge_hits,
                    focus=spec["focus"] or focus,
                    recent_changes=recent_changes,
                    archive_note=archive_note,
                    responsibility=spec["responsibility"],
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
                        "lineage_parent_run_id": parent.run_id if parent else None,
                        "archive_parent": archive_parent_info,
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
            fatal = next(
                (o for o in outcomes if (o.summary or {}).get("fatal")), None
            )
            if fatal is not None:
                # The adapter cannot succeed until the operator intervenes
                # (rate limit, auth, misconfiguration); further generations
                # would only repeat the failure.
                adapter_fatal = True
                self.db.record_agent_message(
                    session_id,
                    "Autopilot",
                    "fatal",
                    str((fatal.summary or {}).get("error") or "adapter fatal failure"),
                )
                break
            if not merge_accepted:
                break
            if not merged_this_generation:
                break
        if adapter_command:
            status = "adapter_fatal" if adapter_fatal else "completed"
        else:
            status = "prompts_generated"
        return AutopilotResult(
            session_id=session_id,
            baseline_run_id=initial_baseline_run_id,
            prompt_dir=prompt_dir,
            selected_agents=all_selected,
            status=status,
            final_baseline_run_id=baseline_run_id,
            merged_count=merged_count,
            generations_run=generations_run,
        )

    def _select_trial_specs(
        self,
        *,
        session_id: int,
        generation: int,
        adapter_command: str | None,
        dynamic_roles: bool,
        roles: list[str] | None,
        analysis: dict[str, Any],
        knowledge_hits: list[dict[str, Any]],
        recent_changes: str,
        agent_count: int,
        focus: str | None,
        baseline_run_id: int,
        prompt_dir: Path,
    ) -> list[dict[str, Any]]:
        """Pick this generation's trial roles: explicit > generated > catalog.

        A spec is {"role", "focus", "responsibility"}; focus/responsibility are
        None for catalog roles so the operator's global focus applies.
        """
        if roles:
            return [{"role": role, "focus": None, "responsibility": None} for role in roles]
        if dynamic_roles and adapter_command:
            generated = self._generate_roles(
                session_id=session_id,
                generation=generation,
                adapter_command=adapter_command,
                analysis=analysis,
                knowledge_hits=knowledge_hits,
                recent_changes=recent_changes,
                agent_count=agent_count,
                focus=focus,
                baseline_run_id=baseline_run_id,
                prompt_dir=prompt_dir,
            )
            if generated:
                return [
                    {
                        "role": spec["role"],
                        "focus": spec["focus"],
                        "responsibility": spec["responsibility"] or None,
                    }
                    for spec in generated
                ]
        return [
            {"role": role, "focus": None, "responsibility": None}
            for role in select_specialists(analysis, agent_count)
        ]

    def _generate_roles(
        self,
        *,
        session_id: int,
        generation: int,
        adapter_command: str,
        analysis: dict[str, Any],
        knowledge_hits: list[dict[str, Any]],
        recent_changes: str,
        agent_count: int,
        focus: str | None,
        baseline_run_id: int,
        prompt_dir: Path,
    ) -> list[dict[str, str]] | None:
        """Ask the adapter LLM to design roles; None means fall back to the catalog."""
        scratch = prompt_dir.parent / "orchestrator" / f"g{generation}"
        scratch.mkdir(parents=True, exist_ok=True)
        prompt = build_orchestrator_prompt(
            root=self.root,
            problem=self.problem,
            run_id=baseline_run_id,
            analysis=analysis,
            knowledge_hits=knowledge_hits,
            agent_count=agent_count,
            focus=focus,
            recent_changes=recent_changes,
        )
        prompt_path = scratch / "orchestrator_prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        self.db.record_agent_message(session_id, "Orchestrator", "prompt", prompt)
        try:
            self._run_adapter(
                adapter_command,
                prompt_path,
                scratch,
                log_stem=f"orchestrator_s{session_id}_g{generation}",
            )
            specs = parse_roles_file(scratch / "roles.json", agent_count)
        except Exception as exc:
            self.db.record_agent_message(session_id, "Orchestrator", "error", str(exc))
            return None
        self.db.record_agent_message(
            session_id, "Orchestrator", "roles", json.dumps(specs, ensure_ascii=False)
        )
        return specs

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
        lineage_parent_run_id: int | None = None,
        archive_parent: dict[str, Any] | None = None,
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
        candidate_solver = candidate_root / self.solver_rel
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
                    start_hash = snapshot_solver_source(candidate_solver, store_dir_for(db))
                    adapter_timeout: str | None = None
                    try:
                        self._run_adapter(
                            adapter_command,
                            active_prompt_path,
                            candidate_root,
                            trial_id=trial_id,
                            log_suffix=log_suffix,
                        )
                    except AdapterTimeoutError as exc:
                        # Salvage: a timed-out agent may still have left a
                        # complete-enough candidate in the workspace.
                        adapter_timeout = str(exc)
                    exact_hash = snapshot_solver_source(candidate_solver, store_dir_for(db))
                    if adapter_timeout is not None and (
                        exact_hash is None or exact_hash == start_hash
                    ):
                        raise RuntimeError(adapter_timeout)
                    norm_hash = (
                        normalized_source_fingerprint(candidate_solver)
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
                        if candidate_run_id is not None:
                            parent_run_id = candidate_run_id
                        elif lineage_parent_run_id is not None:
                            parent_run_id = lineage_parent_run_id
                        else:
                            parent_run_id = baseline_run_id
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
                        remeasure_info: dict[str, Any] | None = None
                        if decision == "rejected" and _elapsed_only_rejection(verdict):
                            remeasure_info = self._remeasure_elapsed(
                                candidate_evaluator, db, run_id, policy
                            )
                            if remeasure_info is not None and remeasure_info["passed"]:
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
                        if remeasure_info is not None:
                            summary["timing_remeasure"] = remeasure_info
                        if adapter_timeout is not None:
                            summary["adapter_timeout"] = True
                        if decision != "rejected" or attempt == max_attempts:
                            break
                except AdapterFatalError as exc:
                    # Retrying the same call cannot succeed; abort this trial
                    # and let the session react (no repair prompt).
                    attempts.append(
                        {
                            "attempt": attempt,
                            "decision": "error",
                            "reasons": [str(exc)],
                            "fatal": True,
                        }
                    )
                    raise
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
            if archive_parent is not None:
                summary["archive_parent"] = archive_parent
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
            if isinstance(exc, AdapterFatalError):
                error_summary["fatal"] = True
            if archive_parent is not None:
                error_summary["archive_parent"] = archive_parent
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

    def _remeasure_elapsed(
        self,
        evaluator: Evaluator,
        db: ExperimentDB,
        run_id: int,
        policy: AcceptancePolicy,
    ) -> dict[str, Any] | None:
        """Serially re-measure over-limit seeds before rejecting on elapsed time.

        Parallel trials contend for cores, which inflates the elapsed times of
        time-budgeted solvers. A candidate whose only gate violation is the
        elapsed cap gets its over-limit seeds re-run one at a time; the serial
        measurement is authoritative, so each passing re-run replaces the
        case row. Returns None when there is nothing to re-measure, else
        {"passed": bool, "seeds": {seed: elapsed}}.
        """
        limit = policy.max_elapsed_sec
        if limit is None:
            return None
        over = [
            case
            for case in db.list_cases(run_id)
            if case["status"] == "ok"
            and case["score"] is not None
            and float(case["elapsed_sec"]) > limit
        ]
        if not over:
            return None
        remeasure_dir = evaluator.root / "experiments" / "remeasure" / f"run_{run_id}"
        remeasure_dir.mkdir(parents=True, exist_ok=True)
        seeds: dict[str, float] = {}
        passed = True
        for case in over:
            outcome = evaluator._evaluate_seed(int(case["seed"]), remeasure_dir)
            seeds[str(outcome.seed)] = outcome.elapsed_sec
            if (
                outcome.status != "ok"
                or outcome.score is None
                or outcome.elapsed_sec > limit
            ):
                passed = False
                break
            db.insert_case(
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
        return {"passed": passed, "seeds": seeds}

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
        mainline_solver = self.root / self.solver_rel
        restore_solver_source(store_dir_for(self.db), source_hash, mainline_solver)
        self.db.record_patch(
            trial_id=trial_id,
            candidate_name=candidate_name,
            path=str(mainline_solver),
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
        log_stem: str | None = None,
    ) -> None:
        rendered = command.format(prompt=shlex.quote(str(prompt_path)), cwd=shlex.quote(str(cwd)))
        env_cmd = os.path.expandvars(rendered)
        timeout = float(os.environ.get("AHC_ADAPTER_TIMEOUT_SEC", "600"))
        result = run_shell(env_cmd, cwd, timeout=timeout)
        log_dir = self.root / "experiments" / "adapter_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if log_stem is not None:
            stem = log_stem
        elif trial_id is not None:
            stem = f"trial_{trial_id}{log_suffix}"
        else:
            stem = str(int(time.time()))
        (log_dir / f"{stem}.stdout.txt").write_text(result.stdout, encoding="utf-8")
        (log_dir / f"{stem}.stderr.txt").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            message = (
                f"adapter failed with exit code {result.returncode}; "
                f"see {log_dir / f'{stem}.stderr.txt'}"
            )
            fatal_kind = classify_adapter_failure(result.stdout + "\n" + result.stderr)
            if fatal_kind is not None:
                detail = " ".join((result.stdout + " " + result.stderr).split())[:300]
                raise AdapterFatalError(
                    f"unrecoverable adapter failure ({fatal_kind}): {detail}"
                )
            if result.timed_out:
                raise AdapterTimeoutError(message)
            raise RuntimeError(message)
