# Problem Analysis

Extract these facts before writing solver changes:

- objective direction and exact score formula;
- constraints that determine state size and transition count;
- whether output is constructive, interactive-like offline, ordering, packing, routing, graph, geometry, or scheduling;
- cheap validity checks and common invalid-output traps;
- obvious lower/upper bounds and baseline strategies;
- whether incremental score updates are easy or expensive;
- visualization signals that reveal bad local structure.

Produce:

- `state`: proposed state representation;
- `score_parts`: decomposed score terms;
- `move_candidates`: plausible local moves or transitions;
- `risk`: output validity, TLE, memory, or score-regression risks;
- `first_experiment`: smallest safe experiment to test the hypothesis.

