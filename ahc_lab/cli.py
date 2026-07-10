from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from .agents import REVIEWERS, SPECIALISTS, Autopilot
from .analysis import analyze_run, compare_runs, find_bad_seeds, make_ai_brief, write_analysis_markdown
from .config import load_problem_config, project_root_from
from .db import ExperimentDB
from .evaluator import Evaluator
from .knowledge import search_knowledge
from .mcp_server import run_server_cli
from .policy import AcceptancePolicy, evaluate_acceptance
from .seeds import parse_seed_spec
from .snapshots import restore_solver_source, snapshot_solver_source, store_dir_for
from .tuning import run_tuning


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ahc", description="AHC AI Lab CLI")
    parser.add_argument("--root", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("build")
    p.add_argument("--problem", required=True)

    p = sub.add_parser("run")
    p.add_argument("--problem", required=True)
    p.add_argument("--seeds", default="0-9")
    p.add_argument("--tag", default="manual")
    p.add_argument("--jobs", type=int, default=None, help="parallel seed workers (default: cpu count - 1)")
    p.add_argument("--no-cache", action="store_true", help="re-evaluate even if this source was already scored")

    p = sub.add_parser("analyze")
    p.add_argument("--run", type=int, required=True)
    p.add_argument("--markdown", type=Path)

    p = sub.add_parser("compare")
    p.add_argument("--base", type=int, required=True)
    p.add_argument("--new", type=int, required=True)
    p.add_argument("--markdown", type=Path)

    p = sub.add_parser("bad-seeds")
    p.add_argument("--run", type=int, required=True)
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("ai-brief")
    p.add_argument("--run", type=int, required=True)

    p = sub.add_parser("ai-suggest")
    p.add_argument("--run", type=int, required=True)
    p.add_argument("--out", type=Path)

    p = sub.add_parser("autopilot")
    p.add_argument("--problem", required=True)
    p.add_argument("--budget", default=None, help="seconds, or suffix m/h")
    p.add_argument("--max-trials", type=int, default=4)
    p.add_argument("--agents", type=int, default=4)
    p.add_argument("--seeds", default="0-9")
    p.add_argument("--adapter", default=None, help="shell command, defaults to AHC_AI_COMMAND")
    p.add_argument(
        "--roles",
        default=None,
        help="comma-separated specialist/reviewer names; overrides automatic selection",
    )
    p.add_argument(
        "--focus",
        default=None,
        help="scoped improvement hypothesis injected into every agent prompt",
    )
    p.add_argument(
        "--no-merge",
        action="store_true",
        help="do not copy accepted candidate sources back into solver/",
    )
    p.add_argument("--jobs", type=int, default=None, help="parallel seed workers (default: cpu count - 1)")
    p.add_argument("--no-cache", action="store_true", help="re-evaluate even if a source was already scored")
    p.add_argument(
        "--no-cascade",
        action="store_true",
        help="evaluate candidates on all seeds instead of staged evaluation with early pruning",
    )
    p.add_argument(
        "--generations",
        type=int,
        default=1,
        help="improvement generations; each merges its best accepted candidate before the next (stops early on stagnation)",
    )
    p.add_argument(
        "--validation-seeds",
        default=None,
        help="held-out seed spec; accepted candidates must also pass the gate here before merging",
    )
    p.add_argument(
        "--trial-jobs",
        type=int,
        default=None,
        help="how many trials run concurrently per generation (default: all)",
    )
    p.add_argument(
        "--repair-attempts",
        type=int,
        default=0,
        help=(
            "on rejection or failure, feed the gate reasons and numbers back to the "
            "same workspace agent and retry up to N times per trial (default: 0)"
        ),
    )
    p.add_argument(
        "--no-novelty-filter",
        action="store_true",
        help=(
            "evaluate candidates even when their source is identical (exactly or up to "
            "comments/whitespace) to an already-evaluated run"
        ),
    )
    p.add_argument(
        "--archive",
        action="store_true",
        help=(
            "sample each trial's starting source from the session's lineage archive "
            "(weighted by fitness and novelty) instead of always branching from the mainline"
        ),
    )
    p.add_argument(
        "--dynamic-roles",
        action="store_true",
        help=(
            "let an orchestrator LLM (via the adapter) design each generation's roles and "
            "focus from the analysis; falls back to the static catalog on failure. "
            "--roles takes precedence"
        ),
    )

    p = sub.add_parser("tune")
    p.add_argument("--problem", required=True)
    p.add_argument("--seeds", default="0-29")
    p.add_argument("--trials", type=int, default=30, help="parameter settings to evaluate")
    p.add_argument(
        "--sampler",
        choices=["auto", "optuna", "random"],
        default="auto",
        help="auto uses Optuna TPE when installed, else built-in random search",
    )
    p.add_argument("--jobs", type=int, default=None, help="parallel seed workers per evaluation")
    p.add_argument("--no-cache", action="store_true")

    p = sub.add_parser("source")
    p.add_argument("--run", type=int, required=True)
    p.add_argument(
        "--checkout",
        action="store_true",
        help="restore the run's solver source into solver/ (current source is snapshotted first)",
    )

    p = sub.add_parser("knowledge")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=5)

    p = sub.add_parser("init-problem")
    p.add_argument("name")

    p = sub.add_parser("mcp")
    p.add_argument("--server", default="all")
    p.add_argument("--list-tools", action="store_true")

    args = parser.parse_args(argv)
    root = project_root_from(args.root or Path.cwd())
    db = ExperimentDB.default(root)
    if args.cmd == "build":
        result = Evaluator(root, args.problem, db).build()
        print(json.dumps(result.__dict__ | {"solver_path": str(result.solver_path)}, indent=2))
        return 0 if result.ok else 1
    if args.cmd == "run":
        run_id = Evaluator(root, args.problem, db).evaluate(
            parse_seed_spec(args.seeds),
            tag=args.tag,
            jobs=args.jobs,
            use_cache=not args.no_cache,
        )
        print(json.dumps({"run_id": run_id}, indent=2))
        return 0
    if args.cmd == "analyze":
        data = analyze_run(db, args.run)
        if args.markdown:
            write_analysis_markdown(args.markdown, f"Run {args.run} Analysis", data)
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    if args.cmd == "compare":
        data = compare_runs(db, args.base, args.new)
        try:
            config = load_problem_config(root, data["problem"])
        except FileNotFoundError:
            pass
        else:
            data["acceptance"] = evaluate_acceptance(data, AcceptancePolicy.from_config(config))
        if args.markdown:
            write_analysis_markdown(args.markdown, "Run Comparison", data)
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    if args.cmd == "bad-seeds":
        print(json.dumps(find_bad_seeds(db, args.run, args.limit), indent=2))
        return 0
    if args.cmd == "ai-brief":
        print(make_ai_brief(db, args.run))
        return 0
    if args.cmd == "ai-suggest":
        brief = make_ai_brief(db, args.run)
        text = brief + "\nAsk ahc-ai-lab specialists for one scoped improvement candidate.\n"
        out = args.out or root / "experiments" / f"run_{args.run}_ai_prompt.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(str(out))
        return 0
    if args.cmd == "autopilot":
        result = Autopilot(root, args.problem, db).run(
            budget_sec=_parse_budget(args.budget),
            max_trials=args.max_trials,
            agent_count=args.agents,
            seeds=args.seeds,
            adapter_command=args.adapter or os.environ.get("AHC_AI_COMMAND"),
            roles=_parse_roles(args.roles),
            focus=args.focus,
            merge_accepted=not args.no_merge,
            jobs=args.jobs,
            use_cache=not args.no_cache,
            cascade=not args.no_cascade,
            generations=args.generations,
            validation_seeds=args.validation_seeds,
            trial_jobs=args.trial_jobs,
            repair_attempts=args.repair_attempts,
            novelty_filter=not args.no_novelty_filter,
            archive_parents=args.archive,
            dynamic_roles=args.dynamic_roles,
        )
        print(json.dumps({
            "session_id": result.session_id,
            "baseline_run_id": result.baseline_run_id,
            "prompt_dir": str(result.prompt_dir),
            "selected_agents": result.selected_agents,
            "status": result.status,
            "final_baseline_run_id": result.final_baseline_run_id,
            "merged_count": result.merged_count,
            "generations_run": result.generations_run,
        }, indent=2))
        return 0
    if args.cmd == "tune":
        result = run_tuning(
            root,
            args.problem,
            seeds=parse_seed_spec(args.seeds),
            n_trials=args.trials,
            db=db,
            sampler=args.sampler,
            jobs=args.jobs,
            use_cache=not args.no_cache,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.cmd == "source":
        run = db.get_run(args.run)
        source_hash = run.get("source_hash")
        if not source_hash:
            print(json.dumps({"run_id": args.run, "error": "run has no source snapshot"}))
            return 1
        store = store_dir_for(db)
        previous_hash = None
        if args.checkout:
            previous_hash = snapshot_solver_source(root, store)
            restore_solver_source(store, source_hash, root)
        print(json.dumps({
            "run_id": args.run,
            "source_hash": source_hash,
            "snapshot_dir": str(store / source_hash),
            "checked_out": bool(args.checkout),
            "previous_source_hash": previous_hash,
        }, indent=2))
        return 0
    if args.cmd == "knowledge":
        print(json.dumps(search_knowledge(root, args.query, args.limit), indent=2))
        return 0
    if args.cmd == "init-problem":
        _init_problem(root, args.name)
        print(f"created problems/{args.name}")
        return 0
    if args.cmd == "mcp":
        return run_server_cli(root, args.server, args.list_tools)
    raise AssertionError(args.cmd)


def _parse_budget(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip().lower()
    if value.endswith("h"):
        return float(value[:-1]) * 3600
    if value.endswith("m"):
        return float(value[:-1]) * 60
    return float(value)


def _parse_roles(value: str | None) -> list[str] | None:
    if value is None or not value.strip():
        return None
    valid = set(SPECIALISTS) | set(REVIEWERS)
    roles = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [role for role in roles if role not in valid]
    if unknown:
        raise ValueError(f"unknown role(s): {', '.join(unknown)}; valid roles: {', '.join(sorted(valid))}")
    return roles


def _init_problem(root: Path, name: str) -> None:
    src = root / "problems" / "dummy"
    dest = root / "problems" / name
    if dest.exists():
        raise FileExistsError(dest)
    shutil.copytree(src, dest)
    config = dest / "config.yaml"
    config.write_text(_config_template(name), encoding="utf-8")
    (dest / "statement.md").write_text(_statement_template(name), encoding="utf-8")
    (dest / "problem_brief.md").write_text(_problem_brief_template(name), encoding="utf-8")


def _config_template(name: str) -> str:
    return f"""# Problem name. Usually the same as this directory name.
name: {name}

# Use `max` when larger scores are better, or `min` when smaller scores are better.
score_direction: max

# Solver timeout in seconds. Set this to the contest time limit.
time_limit_sec: 2

# Build solver/main.cpp into the {{solver}} path.
build_command: g++ -std=c++20 -O2 -pipe -Wall -Wextra -o {{build_dir}}/solver solver/main.cpp

# Generate one input for {{seed}} and write it to {{input}}.
# Replace this with the official generator command for real contests.
generator_command: python3 {{problem_dir}}/tools/generator.py --seed {{seed}} > {{input}}

# Run the built solver with {{input}} and write solver output to {{output}}.
run_command: "{{solver}} < {{input}} > {{output}}"

# Score one {{input}} / {{output}} pair.
# Replace this with the official tester/scorer/visualizer command.
score_command: python3 {{problem_dir}}/tools/scorer.py {{input}} {{output}}

# Regex used to extract a numeric score from score_command output.
# The default expects output like: SCORE: 123456
score_regex: SCORE:\\s*([-+0-9.eE]+)

# Optional acceptance gate overrides used by autopilot and `compare`.
# Defaults: no worsening seed allowed, max elapsed = time_limit_sec.
# acceptance:
#   max_worsening_seeds: 0
#   max_worsening_delta: 0
#   max_elapsed_sec: 1.9
#   neutral_speedup_ratio: 0.05
#   min_improve_confidence: 0.9
"""


def _statement_template(name: str) -> str:
    return f"""# {name}

## Goal

## Input

## Output

## Constraints

## Score

## Validity

## Notes
"""


def _problem_brief_template(name: str) -> str:
    return f"""# {name} Brief

Fill this after reading `statement.md`.

- State:
- Score parts:
- Valid output constraints:
- Baseline idea:
- Likely strategies:
- Known risks:
"""


if __name__ == "__main__":
    raise SystemExit(main())
