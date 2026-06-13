# AHC AI Lab

Autonomous AtCoder Heuristic Contest development template.

The project is built around a fast C++20 solver and a Python control plane that
records experiments, evaluates candidates, exposes MCP tools, and prepares
multi-agent AI work.

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
build_command: g++ -std=c++20 -O2 -pipe -Wall -Wextra -o {build_dir}/solver solver/main.cpp
generator_command: python3 {problem_dir}/tools/generator.py --seed {seed} > {input}
run_command: "{solver} < {input} > {output}"
score_command: python3 {problem_dir}/tools/scorer.py {input} {output}
score_regex: SCORE:\s*([-+0-9.eE]+)
```

For a real contest such as AHC001, the minimum human setup is:

```bash
python3 tools/ahc.py init-problem ahc001
```

Then:

- place the problem statement at `problems/ahc001/statement.md`;
- place official generator/tester/visualizer tools under `problems/ahc001/tools/`;
- update `problems/ahc001/config.yaml` so the commands call those tools;
- replace `solver/main.cpp` with a valid baseline solver.

A valid baseline does not need to be strong. It only needs to compile, respect
the output format, and pass the official tester/scorer so autopilot has a safe
starting point.

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
