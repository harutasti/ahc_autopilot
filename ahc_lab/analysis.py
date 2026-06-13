from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from .db import ExperimentDB


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


def analyze_run(db: ExperimentDB, run_id: int) -> dict[str, Any]:
    run = db.get_run(run_id)
    cases = db.list_cases(run_id)
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
        "bad_seeds": find_bad_seeds(db, run_id, limit=10),
    }
    return summary


def compare_runs(db: ExperimentDB, base_run_id: int, new_run_id: int) -> dict[str, Any]:
    base = db.get_run(base_run_id)
    new = db.get_run(new_run_id)
    if base["problem"] != new["problem"]:
        raise ValueError("cannot compare runs from different problems")
    maximize = new["score_direction"] != "min"
    base_cases = {
        c["seed"]: c
        for c in db.list_cases(base_run_id)
        if c["status"] == "ok" and c["score"] is not None
    }
    new_cases = {
        c["seed"]: c
        for c in db.list_cases(new_run_id)
        if c["status"] == "ok" and c["score"] is not None
    }
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
        "worsening_seeds": worsening[:20],
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
        f"- p10/p90: {summary['p10_score']} / {summary['p90_score']}\n"
        f"- mean elapsed sec: {summary['mean_elapsed_sec']}\n"
        f"- bad seeds: {json.dumps(summary['bad_seeds'], ensure_ascii=False)}\n"
    )


def write_analysis_markdown(path: Path, title: str, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", "", "```json", json.dumps(data, indent=2, sort_keys=True), "```", ""]
    path.write_text("\n".join(lines), encoding="utf-8")

