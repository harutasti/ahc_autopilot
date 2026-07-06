from __future__ import annotations

import difflib
import hashlib
import shutil
from pathlib import Path
from typing import Any


def store_dir_for(db: Any) -> Path:
    """Return the content-addressed source store next to the experiment DB."""
    return db.path.parent / "sources"


def _source_files(root: Path) -> list[Path]:
    solver_dir = root / "solver"
    if not solver_dir.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(solver_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(solver_dir)
        if any(part.startswith(".") for part in rel.parts):
            continue
        files.append(path)
    return files


def snapshot_solver_source(root: Path, store_dir: Path) -> str | None:
    """Store a content-addressed copy of `root/solver` and return its hash.

    Identical sources share one snapshot, so calling this on every run is
    cheap. Returns None when there is no solver source to snapshot.
    """
    solver_dir = root / "solver"
    files = _source_files(root)
    if not files:
        return None
    digest = hashlib.sha256()
    for path in files:
        rel = path.relative_to(solver_dir).as_posix()
        data = path.read_bytes()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
    source_hash = digest.hexdigest()
    dest = store_dir / source_hash
    if not dest.exists():
        for path in files:
            rel = path.relative_to(solver_dir)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    return source_hash


def diff_snapshots(
    store_dir: Path, base_hash: str, new_hash: str, limit_chars: int = 4000
) -> str:
    """Unified diff between two stored source snapshots, truncated for prompts."""
    base_dir = store_dir / base_hash
    new_dir = store_dir / new_hash
    if not base_dir.is_dir() or not new_dir.is_dir():
        return ""
    names = sorted(
        {p.relative_to(new_dir).as_posix() for p in new_dir.rglob("*") if p.is_file()}
        | {p.relative_to(base_dir).as_posix() for p in base_dir.rglob("*") if p.is_file()}
    )
    chunks: list[str] = []
    for name in names:
        base_file = base_dir / name
        new_file = new_dir / name
        base_lines = (
            base_file.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            if base_file.exists()
            else []
        )
        new_lines = (
            new_file.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            if new_file.exists()
            else []
        )
        diff = "".join(
            difflib.unified_diff(base_lines, new_lines, fromfile=f"a/{name}", tofile=f"b/{name}")
        )
        if diff:
            chunks.append(diff)
    joined = "".join(chunks)
    if len(joined) > limit_chars:
        return joined[:limit_chars] + "\n...[diff truncated]...\n"
    return joined


def restore_solver_source(store_dir: Path, source_hash: str, root: Path) -> Path:
    """Copy a stored snapshot back into `root/solver` and return that path."""
    snapshot = store_dir / source_hash
    if not snapshot.is_dir():
        raise FileNotFoundError(f"missing source snapshot: {snapshot}")
    solver_dir = root / "solver"
    for path in sorted(snapshot.rglob("*")):
        if not path.is_file():
            continue
        target = solver_dir / path.relative_to(snapshot)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return solver_dir
