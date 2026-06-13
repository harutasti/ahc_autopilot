from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from .agents import Autopilot
from .analysis import analyze_run, compare_runs, find_bad_seeds, make_ai_brief, write_analysis_markdown
from .config import project_root_from
from .db import ExperimentDB
from .evaluator import Evaluator
from .knowledge import search_knowledge
from .mcp_server import run_server_cli
from .seeds import parse_seed_spec


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
            parse_seed_spec(args.seeds), tag=args.tag
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
        )
        print(json.dumps({
            "session_id": result.session_id,
            "baseline_run_id": result.baseline_run_id,
            "prompt_dir": str(result.prompt_dir),
            "selected_agents": result.selected_agents,
            "status": result.status,
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


def _init_problem(root: Path, name: str) -> None:
    src = root / "problems" / "dummy"
    dest = root / "problems" / name
    if dest.exists():
        raise FileExistsError(dest)
    shutil.copytree(src, dest)
    config = dest / "config.yaml"
    text = config.read_text(encoding="utf-8").replace("name: dummy", f"name: {name}")
    config.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

