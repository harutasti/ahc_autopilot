# AHC AI Lab

Autonomous AtCoder Heuristic Contest development template.

The project is built around a fast C++20 solver and a Python control plane that
records experiments, evaluates candidates, exposes MCP tools, and prepares
multi-agent AI work.

Development phases — done and planned — are tracked in [ROADMAP.md](ROADMAP.md).

## Quick start

```bash
python3 tools/ahc.py build --problem dummy
python3 tools/ahc.py run --problem dummy --seeds 0-9 --tag baseline
python3 tools/ahc.py analyze --run 1
python3 tools/ahc.py autopilot --problem dummy --max-trials 1 --agents 2
```

The default `dummy` problem is intentionally tiny. It verifies the build,
evaluation, SQLite, analysis, prompt generation, and MCP-facing APIs before a
real AHC problem is added.

## Real problem layout

Create a problem under `problems/<contest>/`:

```text
problems/<contest>/
  statement.md
  problem_brief.md        # optional AI-oriented summary
  config.yaml
  tools/
    generator.py or official generator
    scorer.py or official tester/scorer
    visualizer...
```

Put the contest statement in `statement.md` before running autonomous
improvement. It can be the full statement, but an AI-friendly version is better:

```md
# AHC001

## Goal

## Input

## Output

## Constraints

## Score

## Validity

## Notes
```

`autopilot` uses the problem config and generated run analysis today. Keep
`statement.md` next to the config so specialist agents and future fetch/brief
commands have a stable source of problem truth.

`config.yaml` defines commands with placeholders:

```yaml
name: ahc999
score_direction: max
time_limit_sec: 2
build_command: g++ -std=c++20 -O2 -pipe -Wall -Wextra -o {build_dir}/solver {problem_dir}/solver/main.cpp
solver_dir: problems/ahc999/solver
generator_command: python3 {problem_dir}/tools/generator.py --seed {seed} > {input}
run_command: "{solver} < {input} > {output}"
score_command: python3 {problem_dir}/tools/scorer.py {input} {output}
score_regex: SCORE:\s*([-+0-9.eE]+)
```

Each problem owns its solver source under `problems/<name>/solver/`, so
problems never share one mainline slot. `ahc init-problem <name>` scaffolds
this directory with a starter `main.cpp`.

### `config.yaml` fields

Required fields:

- `name`: Problem name. Usually the same as the directory name, such as `ahc001`.
- `score_direction`: Use `max` when a larger score is better, or `min` when a smaller score is better.
- `time_limit_sec`: Solver timeout in seconds. Set this to the contest time limit.
- `build_command`: Command that builds `{problem_dir}/solver/main.cpp` into `{solver}`.
- `solver_dir` (optional): Source directory for this problem's solver, relative to the repo root. Defaults to `solver`; real problems set `problems/<name>/solver` so each owns its source.
- `generator_command`: Command that creates one input file from `{seed}` and writes it to `{input}`.
- `run_command`: Command that runs the built solver with `{input}` and writes `{output}`.
- `score_command`: Command that reads `{input}` and `{output}` and prints a score.
- `score_regex`: Regular expression used to extract the numeric score from `score_command` output.

Optional tool timeouts (heavy Python scorers can exceed the defaults under
parallel load):

- `generator_timeout_sec`: generator kill timeout (default 30).
- `score_timeout_sec`: scorer kill timeout (default 60).

Supported placeholders:

- `{root}`: Project root.
- `{problem}`: Problem name from `config.yaml`.
- `{problem_dir}`: `problems/<contest>`.
- `{build_dir}`: Build output directory under `.ahc_lab/build/<contest>`.
- `{solver}`: Built solver path, normally `{build_dir}/solver`.
- `{seed}`: Current seed.
- `{run_dir}`: Current experiment run directory.
- `{input}`: Current seed input file.
- `{output}`: Current seed solver output file.

`score_command` must print text that matches `score_regex`. The default expects:

```text
SCORE: 123456
```

If an official tool prints a different format, change `score_regex`. For example:

```yaml
score_regex: "Score =\\s*([-+0-9.eE]+)"
```

Keep the config simple: this project reads a small YAML subset intended for
plain key-value settings. Avoid lists, anchors, multiline strings, and complex
YAML features in `config.yaml`.

### Acceptance gate

`autopilot` and `compare` judge a candidate run against its baseline with a
numeric gate. A candidate is rejected when any safety check fails:

- it fails or misses a seed the baseline solves;
- more seeds worsen than `max_worsening_seeds` allows;
- the worst per-seed effective worsening exceeds `max_worsening_delta`;
- the median effective delta is negative;
- any case exceeds `max_elapsed_sec`.

Otherwise the decision is `accepted` when the mean effective delta is positive,
when the candidate fixes previously failing seeds without score regression, or
when a score-neutral candidate is measurably faster. A score-neutral candidate
with no measurable speedup is recorded as `neutral` (held for review) instead
of `rejected`, and does not count against the agent scorecard.

Override the defaults with an optional `acceptance` block in `config.yaml`:

```yaml
acceptance:
  max_worsening_seeds: 0      # seeds allowed to get worse (default 0)
  max_worsening_delta: 0      # largest allowed per-seed effective worsening (default 0)
  max_elapsed_sec: 1.9        # elapsed cap; defaults to time_limit_sec
  neutral_speedup_ratio: 0.05 # max-elapsed speedup needed to accept a score-neutral candidate
```

The verdict (decision plus reasons) is included in `compare` output, stored in
each autopilot trial summary, and exposed as the `evaluate_acceptance` MCP tool.

For a real contest such as AHC001, the minimum human setup is:

```bash
python3 tools/ahc.py init-problem ahc001
```

Then:

- place the problem statement at `problems/ahc001/statement.md`;
- place official generator/tester/visualizer tools under `problems/ahc001/tools/`;
- update `problems/ahc001/config.yaml` so the commands call those tools;
- replace `problems/ahc001/solver/main.cpp` with a valid baseline solver.

A valid baseline does not need to be strong. It only needs to compile, respect
the output format, and pass the official tester/scorer so autopilot has a safe
starting point.

## Autopilot targeting

By default, `autopilot` selects specialists from the latest baseline analysis.
For a known hypothesis, pin the role and pass a narrow focus:

```bash
python3 tools/ahc.py autopilot \
  --problem ahc067 \
  --seeds 62,63,36,53 \
  --max-trials 1 \
  --agents 1 \
  --roles CutBuilder \
  --focus "Add BFS distance-layer s-t cut candidates: create multi-edge closed-door cuts with one switch, keep the current bridge portfolio as fallback, and accept only if low-score seeds improve without losses."
```

Useful roles include `ProblemAnalyst`, `CutBuilder`, `AnnealingBuilder`,
`StateDesignBuilder`, `PerformanceBuilder`, and `ParameterTuner`. `--roles`
overrides automatic specialist selection; `--focus` is injected into every agent
prompt so candidates stay scoped.

## Generation loop

`autopilot` can run multiple improvement generations:

```bash
python3 tools/ahc.py autopilot --problem ahc067 --seeds 0-99 \
  --generations 5 --agents 3 --validation-seeds 100-129
```

Each generation selects specialists from the latest analysis, runs all trials
**in parallel** (each in its own workspace, adapter, and DB connection; limit
concurrency with `--trial-jobs`), and then merges only the **best accepted
candidate** into the mainline. The next generation branches from the merged
solver and compares against the new baseline run. The loop stops early when a
generation merges nothing (stagnation) or the `--budget` is exhausted.

With `--validation-seeds`, an accepted candidate must also pass the acceptance
gate on the held-out seeds before merging; this catches candidates that
overfit the evaluation seed set. A candidate that wins on evaluation seeds but
is rejected on validation seeds is recorded as `validation_failed`, and the
next-best accepted candidate is tried instead.

`compare` also reports `improve_confidence`: the paired-bootstrap probability
that the mean improvement is real rather than seed noise. Set
`acceptance.min_improve_confidence` (e.g. `0.9`) in `config.yaml` to make the
gate reject low-confidence improvements.

Each generation also feeds the next ones:

- **Lineage**: every run records `parent_run_id`, so the solution tree
  (baseline → candidates → merged baselines) can be reconstructed from the DB.
- **Insights**: trial results (decision, deltas, verdict reasons, prune info,
  and the tail of the agent's own summary) are appended to
  `knowledge/<problem>_autopilot.md`, which knowledge search feeds back into
  future agent prompts — failed hypotheses are not silently retried.
- **Inspirations**: prompts include unified diffs of the most recently merged
  candidates so agents build on accepted changes instead of rediscovering or
  undoing them.

## Repair loop

With `--repair-attempts N`, a rejected candidate is not discarded immediately.
The gate's verdict — rejection reasons, per-seed numbers (failures, worst
worsening seeds, deltas, confidence), and elapsed times — is written as a
repair prompt and fed back to the **same workspace agent**, up to N extra
attempts per trial. The agent's previous change is still applied, so it can
fix the diagnosis instead of the hypothesis being retried blind. Attempts that
fail before scoring (build or adapter errors) are fed back the same way.

The loop stops early when a repair leaves the source unchanged, since the
verdict cannot change. Each attempt's run chains `parent_run_id` to the
previous attempt, the attempt history is stored in the trial summary under
`repair.attempts`, and the repair path (e.g. `rejected -> accepted`) is
recorded in the insights file.

```bash
python3 tools/ahc.py autopilot --problem ahc067 --seeds 0-99 \
  --generations 5 --agents 3 --repair-attempts 1
```

## Novelty filter

Before a candidate is evaluated, its source is checked against every source
already evaluated for the problem — both the exact content hash and a
normalized fingerprint that ignores C++ comments and whitespace. A duplicate
is skipped without spending any seed evaluations and recorded with decision
`duplicate` (status `skipped`), including which run it duplicates. With
`--repair-attempts`, the duplicate match is fed back to the agent ("your
change is not novel — propose a materially different one") instead of ending
the trial.

The known-source index is snapshotted once per generation, so parallel trials
that independently produce the same diff are still both evaluated rather than
one being nondeterministically skipped. A repair attempt that leaves the
source effectively unchanged (identical up to comments/whitespace) still stops
the retry loop immediately. Disable the filter with `--no-novelty-filter`.

## Archive evolution

By default every trial branches from the current mainline. With `--archive`,
each trial instead samples its **starting source** from the session's lineage
archive: every fully evaluated baseline/candidate run of the session, one
entry per distinct source. Sampling weight is `(1 + fitness rank) / (1 +
children)` — better-scoring sources are preferred, but parents that already
spawned many branches are discounted so unexplored lineage branches get their
turn (the adaptive parent sampling idea from ShinkaEvolve/AlphaEvolve).

The sampled parent's snapshot is restored into the trial workspace, the prompt
briefs the agent on the parent run and notes the lineage, and the candidate's
`parent_run_id` chains to the parent. The **acceptance gate is unchanged**: a
candidate must still beat the mainline baseline to merge, so branching from a
weaker parent can explore differently but never lowers the bar. The chosen
parent is recorded in the trial summary under `archive_parent`.

```bash
python3 tools/ahc.py autopilot --problem ahc067 --seeds 0-99 \
  --generations 8 --agents 3 --archive --repair-attempts 1
```

## Dynamic roles

By default each generation's trial roles come from a static specialist catalog
(`select_specialists`). With `--dynamic-roles`, an **orchestrator LLM** —
invoked through the same adapter command as the trials, so no extra API setup
— studies the latest analysis, knowledge hits, and recent accepted diffs, and
designs the roles itself: it writes a `roles.json`
(`[{"role", "focus", "responsibility"}, ...]`) into a scratch directory, which
is validated (name sanitizing, focus required, count capped, duplicates
dropped) before use. Each generated focus and responsibility is injected into
that trial's agent prompt.

On any failure — adapter error, missing or malformed JSON — the generation
falls back to the static catalog and the error is recorded in
`agent_messages`, so a broken orchestrator never stalls the loop. An explicit
`--roles` list always takes precedence and skips the orchestrator entirely.

The orchestrator prompt also includes the **role track record** — each role's
accepted/attempts count and mean delta from the `agent_scorecards` table,
accumulated across all past sessions — so role selection is weighted by what
has actually worked on this problem, with an explicit nudge not to starve
exploration of untested or newly invented roles.

```bash
python3 tools/ahc.py autopilot --problem ahc067 --seeds 0-99 \
  --generations 8 --agents 3 --dynamic-roles --archive --repair-attempts 1
```

## Parameter tuning

`ahc tune` searches a solver's parameter space systematically instead of an
agent hand-picking a few settings. Parameters reach the solver as
`AHC_PARAM_<NAME>` environment variables (prefixed onto `run_command`), so the
solver opts in by reading them with its current constants as defaults:

```cpp
const char* env = std::getenv("AHC_PARAM_T_HILL_END");
double t_hill_end = env ? std::atof(env) : 0.70;  // default = current constant
```

Declare the search space in the problem's `config.yaml`:

```yaml
tuning:
  t_hill_end:
    type: float
    low: 0.4
    high: 0.9
  beam_width:
    type: int
    low: 4
    high: 64
  strategy:
    type: categorical
    choices: greedy|anneal
```

Then run:

```bash
python3 tools/ahc.py tune --problem ahc067 --seeds 0-59 --trials 50
```

Every setting is evaluated through the normal evaluator — runs, the
`(source, seed, params)` cache, and `tuning_trials` records all apply — and
the objective is the mean score over the seeds (settings with any failing
seed are discarded). A defaults run is evaluated first as the reference; the
result (best params, best/baseline values, run ids) is printed and written to
`experiments/tuning/<problem>_best.json`.

With [Optuna](https://optuna.org) installed (`pip install .[tuning]`), the
search uses TPE; without it, a built-in random search runs — so the tool
works with zero dependencies. The intended loop with autopilot: an agent
parameterizes the constants (reading `AHC_PARAM_*` with current defaults, a
no-op change that passes the gate as score-neutral), `ahc tune` finds the
best setting, and an agent bakes the winners in as a normal candidate that
must pass the acceptance gate.

## Session reports

`ahc report` renders a markdown retrospective of an autopilot session straight
from the experiments DB — no SQL required:

```bash
python3 tools/ahc.py report                 # latest session
python3 tools/ahc.py report --session 8     # specific session
python3 tools/ahc.py report --markdown out.md
```

The report contains a trial table (decision, mean delta, W/T/L, confidence,
validation verdict, and the repair path such as `rejected → accepted`), the
final gate reasons for every non-accepted trial, the run lineage tree
(baseline → candidates → repairs → validations, with per-run mean scores),
and the mainline trajectory across merged generations.

## Evaluation performance

Seed evaluation is parallel and cached:

- **Parallel seeds**: `run` and `autopilot` evaluate seeds on `cpu count - 1`
  workers by default. Override with `--jobs N`. Use `--jobs 1` when you need
  precise elapsed-time measurements (parallel runs contend for cores, which
  adds timing noise and can slightly reduce scores of time-budgeted solvers).
- **Source-hash cache**: a seed already scored for the same solver source and
  params is reused instead of re-run; the copied case records its origin in
  `source_case_id`. Disable with `--no-cache`. Re-evaluating an unchanged
  solver costs almost nothing.
- **Cascade with early pruning**: autopilot candidates are evaluated in stages
  (5 seeds, then 30, then the rest). If a stage produces an unrecoverable gate
  failure — a failed seed, an over-limit case, or more worsening seeds than the
  acceptance budget — the remaining seeds are skipped and the run is marked
  `pruned`. Disable with `--no-cascade`.
- **Relative scores**: `analyze` and `compare` report `mean_relative_score`
  against the best known score per seed across all runs (virtual best), which
  is scale-independent and matches AHC relative scoring. Failed cases count
  as 0.

## Source snapshots and the merge loop

Every evaluated run stores a content-addressed snapshot of `solver/` under
`experiments/sources/<hash>/` and records the hash on the run row, so any
historical run's exact source can be inspected or restored:

```bash
python3 tools/ahc.py source --run 42             # show the snapshot location
python3 tools/ahc.py source --run 42 --checkout  # restore it into solver/
```

`--checkout` snapshots the current `solver/` first, so the pre-checkout source
is always recoverable via the printed `previous_source_hash`.

When autopilot trials are `accepted`, the best one per generation (highest
mean effective delta, validation-checked if `--validation-seeds` is set) is
automatically merged back into the mainline `solver/`, the merge is recorded
in the `patches` table, and the next generation compares against the new
baseline run. Pass `--no-merge` to keep the record-only behavior. Because the merged source
was already snapshotted by the candidate evaluation, a merge can never lose
code: the previous mainline source stays available from the baseline run.

## MCP servers

All MCP tools share the same Python core as the CLI.

```bash
python3 -m ahc_lab.mcp_server --server all
python3 -m ahc_lab.mcp_server --server analysis --list-tools
```

Available server scopes:

- `analysis`: run analysis, comparisons, bad seed discovery, AI briefs.
- `evaluation`: build and evaluate seed sets.
- `experiment-db`: inspect runs and agent scorecards.
- `workspace`: create isolated candidate workspaces.
- `knowledge`: search AHC notes and skill references.
- `all`: expose every tool.

## Skill installation

The skill source lives in `.codex/skills/ahc-ai-lab`. To make it discoverable by
Codex, sync it into your Codex skills directory and restart Codex:

```bash
python3 tools/sync_skill.py
```
