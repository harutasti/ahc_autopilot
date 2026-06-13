# Solver Patterns

## Greedy / Constructive

Use when a valid solution is hard or the initial solution dominates final score.
Change one ordering rule at a time. Log which constraint or score term the rule
targets.

## Local Search / Annealing

Use when small edits can be evaluated incrementally.

Required implementation properties:

- delta score calculation;
- reversible apply/rollback;
- timer-checked loop;
- reproducible RNG;
- schedule parameters in one place;
- stderr summary for accepted moves and best score.

## Beam Search

Use when solution is naturally built step by step and states can be ranked.

Required implementation properties:

- compact state;
- deterministic transition order when possible;
- duplicate suppression key;
- beam score separate from final score if needed;
- memory estimate before large beam widths.

## State Design

Prefer explicit state invariants over scattered mutable globals. Cache only values
that have clear invalidation rules. If undo is fragile, build a small mutation log.

## Performance

Profile by reasoning first: hot loops, allocation, copying states, scoring, random
calls, and priority queues. Avoid broad refactors without a measurable hypothesis.

