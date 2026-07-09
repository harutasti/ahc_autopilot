from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


CPP_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".inl"}


def normalize_cpp(text: str) -> str:
    """Strip comments and collapse whitespace so cosmetic edits hash the same.

    String and character literals are preserved (a `//` inside a string is not
    a comment). Raw string literals are not fully parsed; a mis-parse can only
    make two genuinely different sources look identical, which costs one
    skipped candidate, never a wrong evaluation.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_literal: str | None = None
    while i < n:
        ch = text[i]
        if in_literal:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == in_literal:
                in_literal = None
            i += 1
            continue
        if ch in ('"', "'"):
            in_literal = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            out.append(" ")  # a block comment can separate two tokens
            continue
        out.append(ch)
        i += 1
    lines: list[str] = []
    for line in "".join(out).splitlines():
        collapsed = " ".join(line.split())
        if collapsed:
            lines.append(collapsed)
    return "\n".join(lines)


def normalized_source_fingerprint(source_dir: Path) -> str | None:
    """Hash of a solver source tree that ignores comments and whitespace.

    Works both on a workspace's `solver/` directory and on a content-addressed
    snapshot directory (which stores the solver files at its top level).
    """
    if not source_dir.is_dir():
        return None
    files = [
        path
        for path in sorted(source_dir.rglob("*"))
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(source_dir).parts)
    ]
    if not files:
        return None
    digest = hashlib.sha256()
    for path in files:
        rel = path.relative_to(source_dir).as_posix()
        if path.suffix.lower() in CPP_SUFFIXES:
            data = normalize_cpp(
                path.read_text(encoding="utf-8", errors="replace")
            ).encode("utf-8")
        else:
            data = path.read_bytes()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
    return digest.hexdigest()


def known_source_index(db: Any, store_dir: Path, problem: str) -> dict[str, dict[str, int]]:
    """Exact and normalized hashes of every already-evaluated source of a problem.

    Normalized fingerprints are computed from the snapshot store once per
    source and memoized in the DB, so building the index stays cheap as the
    run history grows. Each hash maps to the earliest run that evaluated it.
    """
    exact: dict[str, int] = {}
    normalized: dict[str, int] = {}
    for row in db.list_evaluated_sources(problem):
        source_hash = row["source_hash"]
        run_id = int(row["run_id"])
        exact.setdefault(source_hash, run_id)
        norm = db.get_normalized_hash(source_hash)
        if norm is None:
            norm = normalized_source_fingerprint(store_dir / source_hash)
            if norm is None:
                continue
            db.save_normalized_hash(source_hash, norm)
        normalized.setdefault(norm, run_id)
    return {"exact": exact, "normalized": normalized}


def find_duplicate(
    novelty_index: dict[str, dict[str, int]] | None,
    exact_hash: str | None,
    norm_hash: str | None,
) -> dict[str, Any] | None:
    """Match a candidate's hashes against the known-source index."""
    if not novelty_index or exact_hash is None:
        return None
    run_id = novelty_index["exact"].get(exact_hash)
    if run_id is not None:
        return {
            "match": "exact",
            "run_id": run_id,
            "reason": f"candidate source is identical to already-evaluated run {run_id}",
        }
    if norm_hash is not None:
        run_id = novelty_index["normalized"].get(norm_hash)
        if run_id is not None:
            return {
                "match": "normalized",
                "run_id": run_id,
                "reason": (
                    "candidate differs only in comments/whitespace from "
                    f"already-evaluated run {run_id}"
                ),
            }
    return None
