from __future__ import annotations

import json
import random
import statistics
from pathlib import Path
from typing import Any

from .db import ExperimentDB


def _improve_confidence(
    deltas: list[float], iterations: int = 1000, seed: int = 0
) -> float | None:
    """Paired bootstrap: fraction of resampled means that are positive.

    Deterministic (fixed RNG seed) so repeated comparisons agree. None when
    there are too few paired seeds to resample meaningfully.
    """
    if len(deltas) < 2:
        return None
    rng = random.Random(seed)
    n = len(deltas)
    hits = 0
    for _ in range(iterations):
        total = 0.0
        for _ in range(n):
            total += deltas[rng.randrange(n)]
        if total > 0.0:
            hits += 1
    return hits / iterations


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    values = sorted(values)
    idx = (len(values) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(values) - 1)
    frac = idx - lo
    return values[lo] * (1 - frac) + values[hi] * frac


def _relative_mean(
    cases: list[dict[str, Any]], best: dict[int, float], maximize: bool
) -> float | None:
    """Mean AHC-style relative score against the best known score per seed.

    Failed cases count as 0. Seeds with no best-known score, or where the
    ratio is undefined (non-positive scores), are skipped.
    """
    values: list[float] = []
    for case in cases:
        seed = case["seed"]
        if seed not in best:
            continue
        top = best[seed]
        ok = case["status"] == "ok" and case["score"] is not None
        if not ok:
            values.append(0.0)
            continue
        score = float(case["score"])
        if maximize:
            if top <= 0:
                continue
            values.append(score / top)
        else:
            if score <= 0 or top <= 0:
                continue
            values.append(top / score)
    return statistics.fmean(values) if values else None


def analyze_run(db: ExperimentDB, run_id: int) -> dict[str, Any]:
    run = db.get_run(run_id)
    cases = db.list_cases(run_id)
    maximize = run["score_direction"] != "min"
    best_known = db.best_scores(run["problem"], maximize=maximize)
    ok_cases = [c for c in cases if c["status"] == "ok" and c["score"] is not None]
    scores = [float(c["score"]) for c in ok_cases]
    elapsed = [float(c["elapsed_sec"]) for c in cases]
    status_counts: dict[str, int] = {}
    for case in cases:
        status_counts[case["status"]] = status_counts.get(case["status"], 0) + 1
    summary = {
        "run_id": run_id,
        "problem": run["problem"],
        "tag": run["tag"],
        "status": run["status"],
        "case_count": len(cases),
        "ok_count": len(ok_cases),
        "status_counts": status_counts,
        "score_direction": run["score_direction"],
        "mean_score": statistics.fmean(scores) if scores else None,
        "median_score": statistics.median(scores) if scores else None,
        "min_score": min(scores) if scores else None,
        "max_score": max(scores) if scores else None,
        "p10_score": _percentile(scores, 0.10),
        "p90_score": _percentile(scores, 0.90),
        "mean_elapsed_sec": statistics.fmean(elapsed) if elapsed else None,
        "max_elapsed_sec": max(elapsed) if elapsed else None,
        "mean_relative_score": _relative_mean(cases, best_known, maximize),
        "bad_seeds": find_bad_seeds(db, run_id, limit=10),
    }
    return summary


def _case_ok(case: dict[str, Any] | None) -> bool:
    return case is not None and case["status"] == "ok" and case["score"] is not None


def compare_runs(db: ExperimentDB, base_run_id: int, new_run_id: int) -> dict[str, Any]:
    base = db.get_run(base_run_id)
    new = db.get_run(new_run_id)
    if base["problem"] != new["problem"]:
        raise ValueError("cannot compare runs from different problems")
    maximize = new["score_direction"] != "min"
    best_known = db.best_scores(new["problem"], maximize=maximize)
    base_all = {c["seed"]: c for c in db.list_cases(base_run_id)}
    new_all = {c["seed"]: c for c in db.list_cases(new_run_id)}
    base_cases = {seed: c for seed, c in base_all.items() if _case_ok(c)}
    new_cases = {seed: c for seed, c in new_all.items() if _case_ok(c)}
    candidate_failures: list[dict[str, Any]] = []
    fixed_seed_count = 0
    both_failed_count = 0
    for seed in sorted(base_all):
        b = base_all[seed]
        n = new_all.get(seed)
        if _case_ok(b) and not _case_ok(n):
            candidate_failures.append(
                {"seed": seed, "status": n["status"] if n is not None else "missing"}
            )
        elif not _case_ok(b) and _case_ok(n):
            fixed_seed_count += 1
        elif not _case_ok(b) and n is not None and not _case_ok(n):
            both_failed_count += 1
    base_elapsed = [float(c["elapsed_sec"]) for c in base_cases.values()]
    new_elapsed = [float(c["elapsed_sec"]) for c in new_cases.values()]
    common = sorted(set(base_cases) & set(new_cases))
    deltas: list[float] = []
    wins = ties = losses = 0
    per_seed: list[dict[str, Any]] = []
    for seed in common:
        b = float(base_cases[seed]["score"])
        n = float(new_cases[seed]["score"])
        raw_delta = n - b
        effective_delta = raw_delta if maximize else -raw_delta
        deltas.append(effective_delta)
        if effective_delta > 0:
            wins += 1
        elif effective_delta < 0:
            losses += 1
        else:
            ties += 1
        per_seed.append(
            {
                "seed": seed,
                "base_score": b,
                "new_score": n,
                "raw_delta": raw_delta,
                "effective_delta": effective_delta,
            }
        )
    worsening = sorted(
        [row for row in per_seed if row["effective_delta"] < 0],
        key=lambda row: row["effective_delta"],
    )
    summary = {
        "base_run_id": base_run_id,
        "new_run_id": new_run_id,
        "problem": new["problem"],
        "common_seed_count": len(common),
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "win_rate": wins / len(common) if common else None,
        "mean_effective_delta": statistics.fmean(deltas) if deltas else None,
        "median_effective_delta": statistics.median(deltas) if deltas else None,
        "min_effective_delta": min(deltas) if deltas else None,
        "max_effective_delta": max(deltas) if deltas else None,
        "improve_confidence": _improve_confidence(deltas),
        "worsening_seeds": worsening[:20],
        "candidate_failure_count": len(candidate_failures),
        "candidate_failures": candidate_failures[:20],
        "fixed_seed_count": fixed_seed_count,
        "both_failed_count": both_failed_count,
        "base_max_elapsed_sec": max(base_elapsed) if base_elapsed else None,
        "new_max_elapsed_sec": max(new_elapsed) if new_elapsed else None,
        "base_mean_relative_score": _relative_mean(list(base_all.values()), best_known, maximize),
        "new_mean_relative_score": _relative_mean(list(new_all.values()), best_known, maximize),
    }
    db.save_comparison(base_run_id, new_run_id, summary)
    return summary


def find_bad_seeds(db: ExperimentDB, run_id: int, limit: int = 10) -> list[dict[str, Any]]:
    run = db.get_run(run_id)
    maximize = run["score_direction"] != "min"
    cases = [c for c in db.list_cases(run_id) if c["score"] is not None]
    cases.sort(key=lambda c: float(c["score"]), reverse=not maximize)
    return [
        {
            "seed": c["seed"],
            "score": c["score"],
            "elapsed_sec": c["elapsed_sec"],
            "status": c["status"],
        }
        for c in cases[:limit]
    ]


def make_ai_brief(db: ExperimentDB, run_id: int) -> str:
    summary = analyze_run(db, run_id)
    return (
        f"AHC run brief\n"
        f"- problem: {summary['problem']}\n"
        f"- run: {run_id} ({summary['tag']})\n"
        f"- status: {summary['status']}\n"
        f"- ok/cases: {summary['ok_count']}/{summary['case_count']}\n"
        f"- mean: {summary['mean_score']}\n"
        f"- median: {summary['median_score']}\n"
        f"- mean relative (vs best known): {summary['mean_relative_score']}\n"
        f"- p10/p90: {summary['p10_score']} / {summary['p90_score']}\n"
        f"- mean elapsed sec: {summary['mean_elapsed_sec']}\n"
        f"- bad seeds: {json.dumps(summary['bad_seeds'], ensure_ascii=False)}\n"
    )


def write_analysis_markdown(path: Path, title: str, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", "", "```json", json.dumps(data, indent=2, sort_keys=True), "```", ""]
    path.write_text("\n".join(lines), encoding="utf-8")

