from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .analysis import analyze_run, compare_runs, find_bad_seeds, make_ai_brief
from .config import project_root_from
from .db import ExperimentDB
from .evaluator import Evaluator
from .knowledge import search_knowledge
from .seeds import parse_seed_spec
from .workspace import create_candidate_workspace, list_candidate_workspaces


ToolFn = Callable[[Path, dict[str, Any]], Any]


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]
    server: str
    fn: ToolFn


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def tool_defs() -> dict[str, ToolDef]:
    defs = [
        ToolDef(
            "analyze_run",
            "Analyze a recorded run and return score/time/status summary.",
            _schema({"run_id": {"type": "integer"}}, ["run_id"]),
            "analysis",
            lambda root, a: analyze_run(ExperimentDB.default(root), int(a["run_id"])),
        ),
        ToolDef(
            "compare_runs",
            "Compare two runs seed-by-seed and return improvement statistics.",
            _schema(
                {"base_run_id": {"type": "integer"}, "new_run_id": {"type": "integer"}},
                ["base_run_id", "new_run_id"],
            ),
            "analysis",
            lambda root, a: compare_runs(
                ExperimentDB.default(root), int(a["base_run_id"]), int(a["new_run_id"])
            ),
        ),
        ToolDef(
            "find_bad_seeds",
            "Return the worst seeds for a run.",
            _schema(
                {"run_id": {"type": "integer"}, "limit": {"type": "integer", "default": 10}},
                ["run_id"],
            ),
            "analysis",
            lambda root, a: find_bad_seeds(
                ExperimentDB.default(root), int(a["run_id"]), int(a.get("limit", 10))
            ),
        ),
        ToolDef(
            "make_ai_brief",
            "Generate a compact AI-readable run brief.",
            _schema({"run_id": {"type": "integer"}}, ["run_id"]),
            "analysis",
            lambda root, a: {"brief": make_ai_brief(ExperimentDB.default(root), int(a["run_id"]))},
        ),
        ToolDef(
            "build_solver",
            "Build the solver for a problem.",
            _schema({"problem": {"type": "string"}}, ["problem"]),
            "evaluation",
            _build_solver,
        ),
        ToolDef(
            "evaluate_seeds",
            "Build and evaluate a problem on a seed spec.",
            _schema(
                {
                    "problem": {"type": "string"},
                    "seeds": {"type": "string", "default": "0-9"},
                    "tag": {"type": "string", "default": "mcp"},
                },
                ["problem"],
            ),
            "evaluation",
            _evaluate_seeds,
        ),
        ToolDef(
            "list_runs",
            "List recent experiment runs.",
            _schema(
                {"problem": {"type": "string"}, "limit": {"type": "integer", "default": 20}},
                [],
            ),
            "experiment-db",
            lambda root, a: ExperimentDB.default(root).list_runs(
                a.get("problem"), int(a.get("limit", 20))
            ),
        ),
        ToolDef(
            "get_run",
            "Read one experiment run record.",
            _schema({"run_id": {"type": "integer"}}, ["run_id"]),
            "experiment-db",
            lambda root, a: ExperimentDB.default(root).get_run(int(a["run_id"])),
        ),
        ToolDef(
            "get_agent_scorecard",
            "Read specialist agent scorecards.",
            _schema({"agent_name": {"type": "string"}}, []),
            "experiment-db",
            lambda root, a: ExperimentDB.default(root).get_scorecard(a.get("agent_name")),
        ),
        ToolDef(
            "create_candidate_workspace",
            "Create an isolated candidate workspace for an autopilot session.",
            _schema(
                {
                    "session_id": {"type": "integer"},
                    "candidate_name": {"type": "string"},
                },
                ["session_id", "candidate_name"],
            ),
            "workspace",
            lambda root, a: create_candidate_workspace(
                root, session_id=int(a["session_id"]), candidate_name=str(a["candidate_name"])
            ),
        ),
        ToolDef(
            "list_candidate_workspaces",
            "List isolated candidate workspaces.",
            _schema({}, []),
            "workspace",
            lambda root, a: list_candidate_workspaces(root),
        ),
        ToolDef(
            "search_knowledge",
            "Search local AHC knowledge and ahc-ai-lab references.",
            _schema(
                {"query": {"type": "string"}, "limit": {"type": "integer", "default": 5}},
                ["query"],
            ),
            "knowledge",
            lambda root, a: search_knowledge(root, str(a["query"]), int(a.get("limit", 5))),
        ),
    ]
    return {tool.name: tool for tool in defs}


def _build_solver(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    result = Evaluator(root, str(args["problem"])).build()
    return {
        "ok": result.ok,
        "command": result.command,
        "solver_path": str(result.solver_path),
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed_sec": result.elapsed_sec,
    }


def _evaluate_seeds(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    db = ExperimentDB.default(root)
    run_id = Evaluator(root, str(args["problem"]), db).evaluate(
        parse_seed_spec(str(args.get("seeds", "0-9"))),
        tag=str(args.get("tag", "mcp")),
        run_type="mcp",
    )
    return {"run_id": run_id, "analysis": analyze_run(db, run_id)}


def _filter_tools(server: str) -> dict[str, ToolDef]:
    defs = tool_defs()
    if server == "all":
        return defs
    return {name: tool for name, tool in defs.items() if tool.server == server}


def run_server_cli(root: Path, server: str, list_tools: bool = False) -> int:
    defs = _filter_tools(server)
    if list_tools:
        print(json.dumps([_tool_json(tool) for tool in defs.values()], indent=2))
        return 0
    serve_stdio(root, defs)
    return 0


def _tool_json(tool: ToolDef) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": tool.input_schema,
    }


def serve_stdio(root: Path, defs: dict[str, ToolDef]) -> None:
    while True:
        message = _read_message()
        if message is None:
            break
        if "id" not in message:
            continue
        response = _handle_message(root, defs, message)
        _write_message(response)


def _handle_message(root: Path, defs: dict[str, ToolDef], message: dict[str, Any]) -> dict[str, Any]:
    msg_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    try:
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ahc-ai-lab", "version": "0.1.0"},
            }
        elif method == "tools/list":
            result = {"tools": [_tool_json(tool) for tool in defs.values()]}
        elif method == "tools/call":
            name = params["name"]
            args = params.get("arguments") or {}
            if name not in defs:
                raise KeyError(f"unknown tool: {name}")
            payload = defs[name].fn(root, args)
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, indent=2, sort_keys=True),
                    }
                ],
                "isError": False,
            }
        else:
            raise KeyError(f"unsupported method: {method}")
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    except Exception as exc:  # MCP clients need structured errors.
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32000, "message": str(exc)},
        }


def _read_message() -> dict[str, Any] | None:
    first = sys.stdin.buffer.readline()
    if not first:
        return None
    if first.lower().startswith(b"content-length:"):
        length = int(first.split(b":", 1)[1].strip())
        while True:
            header = sys.stdin.buffer.readline()
            if header in {b"\r\n", b"\n", b""}:
                break
        raw = sys.stdin.buffer.read(length)
        return json.loads(raw.decode("utf-8"))
    return json.loads(first.decode("utf-8"))


def _write_message(message: dict[str, Any]) -> None:
    raw = json.dumps(message, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ahc-mcp")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--server", default="all")
    parser.add_argument("--list-tools", action="store_true")
    args = parser.parse_args(argv)
    root = project_root_from(args.root or Path.cwd())
    return run_server_cli(root, args.server, args.list_tools)


if __name__ == "__main__":
    raise SystemExit(main())

