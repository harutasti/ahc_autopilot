from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _strip_comment(line: str) -> str:
    in_quote: str | None = None
    escaped = False
    out: list[str] = []
    for ch in line:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch in ("'", '"'):
            if in_quote == ch:
                in_quote = None
            elif in_quote is None:
                in_quote = ch
            out.append(ch)
            continue
        if ch == "#" and in_quote is None:
            break
        out.append(ch)
    return "".join(out).rstrip()


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "":
        return ""
    if (value[0], value[-1]) in {('"', '"'), ("'", "'")}:
        return value[1:-1]
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        if any(ch in value for ch in [".", "e", "E"]):
            return float(value)
        return int(value)
    except ValueError:
        return value


def read_simple_yaml(path: Path) -> dict[str, Any]:
    """Read the small YAML subset used by problem configs."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if ":" not in line:
            raise ValueError(f"invalid config line in {path}: {raw}")
        key, value = line.strip().split(":", 1)
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value.strip() == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(value)
    return root


@dataclass(frozen=True)
class ProblemConfig:
    name: str
    problem_dir: Path
    score_direction: str
    time_limit_sec: float
    build_command: str
    run_command: str
    generator_command: str | None
    score_command: str | None
    visualizer_command: str | None
    score_regex: str
    raw: dict[str, Any]

    @property
    def maximize(self) -> bool:
        return self.score_direction.lower() != "min"


def load_problem_config(root: Path, problem: str) -> ProblemConfig:
    problem_dir = root / "problems" / problem
    path = problem_dir / "config.yaml"
    if not path.exists():
        raise FileNotFoundError(f"missing problem config: {path}")
    data = read_simple_yaml(path)
    return ProblemConfig(
        name=str(data.get("name", problem)),
        problem_dir=problem_dir,
        score_direction=str(data.get("score_direction", "max")).lower(),
        time_limit_sec=float(data.get("time_limit_sec", 2.0)),
        build_command=str(data["build_command"]),
        run_command=str(data["run_command"]),
        generator_command=(
            str(data["generator_command"]) if data.get("generator_command") else None
        ),
        score_command=str(data["score_command"]) if data.get("score_command") else None,
        visualizer_command=(
            str(data["visualizer_command"]) if data.get("visualizer_command") else None
        ),
        score_regex=str(data.get("score_regex", r"SCORE:\s*([-+0-9.eE]+)")),
        raw=data,
    )


def project_root_from(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    for current in [start, *start.parents]:
        if (current / "pyproject.toml").exists() or (current / "problems").exists():
            return current
    return start

