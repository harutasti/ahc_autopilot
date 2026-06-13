# MCP Tools

Use MCP tools for repeatable operations.

## Analysis

- `analyze_run(run_id)`: score, time, status, and bad seed summary.
- `compare_runs(base_run_id, new_run_id)`: seed-by-seed deltas and win/loss counts.
- `find_bad_seeds(run_id, limit)`: worst seeds for focused visual inspection.
- `make_ai_brief(run_id)`: compact prompt input.

CLI fallback:

```bash
python tools/ahc.py analyze --run RUN_ID
python tools/ahc.py compare --base BASE --new NEW
python tools/ahc.py bad-seeds --run RUN_ID
python tools/ahc.py ai-brief --run RUN_ID
```

## Evaluation

- `build_solver(problem)`;
- `evaluate_seeds(problem, seeds, tag)`.

CLI fallback:

```bash
python tools/ahc.py build --problem PROBLEM
python tools/ahc.py run --problem PROBLEM --seeds 0-99 --tag LABEL
```

## Workspace / Knowledge

- `create_candidate_workspace(session_id, candidate_name)`;
- `list_candidate_workspaces()`;
- `search_knowledge(query, limit)`.

Use workspace tools before candidate implementation so the main tree remains
clean.

