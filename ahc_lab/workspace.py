from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any


IGNORE_DIRS = {".git", ".ahc_lab", "experiments", "worktrees", "__pycache__", ".pytest_cache"}


def create_candidate_workspace(
    root: Path, *, session_id: int, candidate_name: str
) -> dict[str, Any]:
    """Create an isolated copy for one candidate implementation."""
    root = root.resolve()
    dest = root / "worktrees" / f"session_{session_id}" / candidate_name
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    def ignore(_: str, names: list[str]) -> set[str]:
        return {name for name in names if name in IGNORE_DIRS}

    shutil.copytree(root, dest, ignore=ignore)
    marker = dest / ".ahc_candidate"
    marker.write_text(
        f"session_id={session_id}\ncandidate_name={candidate_name}\ncreated_at={time.time()}\n",
        encoding="utf-8",
    )
    return {"path": str(dest), "candidate_name": candidate_name, "session_id": session_id}


def list_candidate_workspaces(root: Path) -> list[dict[str, Any]]:
    base = root / "worktrees"
    if not base.exists():
        return []
    rows: list[dict[str, Any]] = []
    for marker in base.rglob(".ahc_candidate"):
        data: dict[str, Any] = {"path": str(marker.parent)}
        for line in marker.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                data[key] = value
        rows.append(data)
    rows.sort(key=lambda row: row["path"])
    return rows

