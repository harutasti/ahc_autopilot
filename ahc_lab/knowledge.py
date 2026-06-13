from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _tokens(query: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9_+-]+", query)}


def search_knowledge(root: Path, query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search skill references and local AHC notes with a tiny BM25-like score."""
    roots = [
        root / ".codex" / "skills" / "ahc-ai-lab" / "references",
        root / "knowledge",
    ]
    q = _tokens(query)
    if not q:
        return []
    results: list[dict[str, Any]] = []
    for base in roots:
        if not base.exists():
            continue
        for path in base.rglob("*.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            lower = text.lower()
            score = sum(lower.count(token) for token in q)
            if score == 0:
                continue
            snippet = _snippet(text, q)
            results.append(
                {
                    "path": str(path.relative_to(root)),
                    "score": score,
                    "snippet": snippet,
                }
            )
    results.sort(key=lambda row: (-row["score"], row["path"]))
    return results[:limit]


def _snippet(text: str, tokens: set[str], radius: int = 240) -> str:
    lower = text.lower()
    positions = [lower.find(token) for token in tokens if lower.find(token) >= 0]
    if not positions:
        return text[:radius]
    pos = min(positions)
    start = max(0, pos - radius // 2)
    end = min(len(text), pos + radius // 2)
    return text[start:end].replace("\n", " ").strip()

