from __future__ import annotations

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
