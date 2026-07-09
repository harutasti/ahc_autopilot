from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ArchiveEntry:
    run_id: int
    source_hash: str
    mean_score: float
    fitness: float  # mean score, sign-adjusted so higher is always better
    children: int


def build_archive(db: Any, problem: str, tag_prefix: str) -> list[ArchiveEntry]:
    """Evaluated runs of one autopilot session, one entry per distinct source.

    Only runs sharing the session tag prefix are comparable: they were scored
    on the same seed list. Pruned or failed runs and runs with any failing
    case are excluded, so every fitness is a full-coverage mean. Duplicate
    sources keep their earliest run.
    """
    children = db.count_children()
    by_hash: dict[str, ArchiveEntry] = {}
    for row in db.list_archive_runs(problem, tag_prefix):
        if int(row["bad_cases"] or 0) > 0 or row["mean_score"] is None:
            continue
        maximize = str(row["score_direction"]).lower() != "min"
        mean_score = float(row["mean_score"])
        entry = ArchiveEntry(
            run_id=int(row["run_id"]),
            source_hash=str(row["source_hash"]),
            mean_score=mean_score,
            fitness=mean_score if maximize else -mean_score,
            children=children.get(int(row["run_id"]), 0),
        )
        existing = by_hash.get(entry.source_hash)
        if existing is None or entry.run_id < existing.run_id:
            by_hash[entry.source_hash] = entry
    return sorted(by_hash.values(), key=lambda entry: entry.run_id)


def sample_parents(
    archive: list[ArchiveEntry], count: int, rng: random.Random | None = None
) -> list[ArchiveEntry]:
    """Sample trial parents weighted by fitness rank and novelty.

    Fitness uses the rank within the pool (scale-free across problems) and
    novelty divides by the number of children a parent already has, so
    well-explored lineage branches make room for fresh ones — the adaptive
    parent sampling idea from ShinkaEvolve/AlphaEvolve. Parents are distinct
    while the archive is large enough, then the pool resets.
    """
    rng = rng or random.Random()
    if not archive or count <= 0:
        return []
    picked: list[ArchiveEntry] = []
    pool: list[ArchiveEntry] = []
    for _ in range(count):
        if not pool:
            pool = list(archive)
        choice = rng.choices(pool, weights=_weights(pool), k=1)[0]
        picked.append(choice)
        pool.remove(choice)
    return picked


def _weights(pool: list[ArchiveEntry]) -> list[float]:
    order = sorted(pool, key=lambda entry: entry.fitness)
    denominator = max(1, len(order) - 1)
    rank = {entry.run_id: index / denominator for index, entry in enumerate(order)}
    return [(1.0 + rank[entry.run_id]) / (1.0 + entry.children) for entry in pool]
