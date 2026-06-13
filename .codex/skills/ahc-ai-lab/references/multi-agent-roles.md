# Multi-Agent Roles

## Orchestrator

Read analysis, knowledge hits, and scorecards. Select two to four specialists.
Keep tasks independent and scoped. Never accept a change only because a specialist
argues for it.

## Builders

Return:

- hypothesis;
- files changed;
- expected improvement mechanism;
- risk;
- evaluation request.

Keep changes narrow. Do not mix algorithm strategy, parameter tuning, and
performance refactoring in one candidate unless explicitly assigned.

## Reviewers

Return one verdict:

- `approve`;
- `request_changes`;
- `reject`.

Correctness review checks validity and reproducibility. Performance review checks
TLE and memory. Search quality review checks whether the proposed strategy can
actually improve the measured bad cases.

## ParameterTuner

Tune only after an implementation has positive signal. Store parameter sets and
seed sets. Prefer robust median improvement over a fragile high mean.

