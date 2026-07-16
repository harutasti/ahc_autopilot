from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import ExperimentDB
from .evaluator import Evaluator


def run_preflight(
    root: Path,
    problem: str,
    *,
    seeds: list[int],
    db: ExperimentDB | None = None,
    safety_ratio: float = 0.95,
    use_cache: bool = False,
) -> dict[str, Any]:
    """Pre-submission safety check: every seed must finish inside the margin.

    Contest rules judge the whole submission TLE when a single case exceeds
    the limit, so the check is strict: seeds are evaluated SERIALLY (jobs=1,
    contention-free timing, cache off by default so times are freshly
    measured), every case must be ok, and the slowest case must stay under
    `time_limit_sec * safety_ratio` to leave headroom for judge-machine
    variance. Returns a verdict dict; `passed` gates submission.
    """
    db = db or ExperimentDB.default(root)
    evaluator = Evaluator(root, problem, db)
    limit = evaluator.config.time_limit_sec
    threshold = limit * safety_ratio
    run_id = evaluator.evaluate(
        seeds,
        tag="preflight",
        run_type="preflight",
        jobs=1,
        use_cache=use_cache,
    )
    cases = db.list_cases(run_id)
    failures = [
        {"seed": c["seed"], "status": c["status"]} for c in cases if c["status"] != "ok"
    ]
    over_margin = sorted(
        (
            {"seed": c["seed"], "elapsed_sec": float(c["elapsed_sec"])}
            for c in cases
            if c["status"] == "ok" and float(c["elapsed_sec"]) > threshold
        ),
        key=lambda row: -row["elapsed_sec"],
    )
    elapsed = [float(c["elapsed_sec"]) for c in cases]
    return {
        "problem": problem,
        "run_id": run_id,
        "seed_count": len(cases),
        "time_limit_sec": limit,
        "safety_ratio": safety_ratio,
        "threshold_sec": threshold,
        "max_elapsed_sec": max(elapsed) if elapsed else None,
        "failures": failures,
        "over_margin": over_margin,
        "passed": not failures and not over_margin and len(cases) == len(seeds),
    }
