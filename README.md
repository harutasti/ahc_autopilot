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
  config.yaml
  tools/
    generator.py or official generator
    scorer.py or official tester/scorer
    visualizer...
```

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
