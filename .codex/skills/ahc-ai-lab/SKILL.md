---
name: ahc-ai-lab
description: Autonomous AtCoder Heuristic Contest improvement workflow for Codex. Use when improving, analyzing, testing, tuning, or reviewing AHC-style C++20 solvers; when running multi-agent autopilot loops; when comparing seed-based scores; when using analysis/evaluation/workspace/knowledge MCP tools; or when deciding whether heuristic solver changes should be accepted.
---

# AHC AI Lab

Use this skill to behave like a disciplined AHC specialist team. Prefer measured
score improvements over plausible ideas. Do not accept a solver change without
seed evaluation.

## Required Workflow

1. Establish or read the baseline run.
2. Use MCP or CLI analysis before proposing changes.
3. Choose specialists based on the bottleneck.
4. Keep each implementation candidate scoped to one hypothesis.
5. Review correctness before expensive evaluation.
6. Evaluate smoke seeds, then small seeds, then large seeds.
7. Accept only changes that pass the numeric policy.
8. Record rejected candidates as learning material.

## MCP First

Use project MCP tools when available:

- `analysis-mcp`: `analyze_run`, `compare_runs`, `evaluate_acceptance`, `find_bad_seeds`, `make_ai_brief`.
- `evaluation-mcp`: `build_solver`, `evaluate_seeds`.
- `experiment-db-mcp`: `list_runs`, `get_run`, `get_agent_scorecard`.
- `workspace-mcp`: `create_candidate_workspace`, `list_candidate_workspaces`.
- `knowledge-mcp`: `search_knowledge`.

If MCP is unavailable, use the equivalent `python tools/ahc.py ...` commands.
Do not hand-compute score summaries when the project tools can compute them.

## Specialist Selection

Use `ProblemAnalyst` first when the problem structure or current weakness is
unclear. Then select two to four specialists:

- `BeamSearchBuilder`: state ranking, pruning, transition generation, duplicate removal.
- `CutBuilder`: graph cuts, separators, bottlenecks, multi-edge doors, switch placement.
- `AnnealingBuilder`: neighborhoods, delta evaluation, rollback, schedule, time split.
- `GreedyConstructiveBuilder`: initial solution and constructive ordering.
- `StateDesignBuilder`: state representation, score decomposition, cache, undo/redo.
- `PerformanceBuilder`: hot paths, memory churn, TLE risk, random overhead.
- `ParameterTuner`: robust parameter search after an implementation is promising.

Use reviewers as gates:

- `CorrectnessReviewer` is mandatory before accepting any candidate.
- `PerformanceReviewer` is required for hot-path or memory-heavy changes.
- `SearchQualityReviewer` is required for new search strategies or evaluation functions.

## Acceptance Policy

Reject or hold a candidate unless all required checks pass:

- build succeeds;
- tester/scorer accepts every smoke seed;
- small seed set is non-worse overall;
- large seed set improves mean or median effective score;
- worsening seed count and max worsening stay within the problem config threshold;
- elapsed time stays below the contest limit;
- correctness review approves.

AI judgment is advisory. Numeric evaluation decides. Use the
`evaluate_acceptance` tool (or `python tools/ahc.py compare`) for the verdict:
it rejects candidates that fail or miss seeds the baseline solves, exceed the
worsening budget from the problem `acceptance` config, or exceed the elapsed
limit. A score-neutral candidate that passes every safety check is `neutral`,
not `rejected`; keep it as a performance-driven follow-up.

## References

Read these only when relevant:

- `references/problem-analysis.md`: extracting state, score, constraints, and risk.
- `references/solver-patterns.md`: beam, annealing, greedy, state, performance patterns.
- `references/experiment-loop.md`: seed sets, comparisons, acceptance, stopping.
- `references/multi-agent-roles.md`: role outputs and handoff contracts.
- `references/mcp-tools.md`: exact MCP/CLI tool mapping.
- `references/external-notes.md`: audited external AHC references.
