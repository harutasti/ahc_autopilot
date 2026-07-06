from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import ProblemConfig


@dataclass(frozen=True)
class AcceptancePolicy:
    """Numeric gate that decides whether a candidate run may replace its baseline.

    Defaults keep the historical conservative behavior: no worsening seeds,
    no per-seed worsening budget, and the contest time limit as the elapsed cap.
    """

    max_worsening_seeds: int = 0
    max_worsening_delta: float = 0.0
    max_elapsed_sec: float | None = None
    neutral_speedup_ratio: float = 0.05

    @classmethod
    def from_config(cls, config: ProblemConfig) -> "AcceptancePolicy":
        raw = config.raw.get("acceptance")
        if not isinstance(raw, dict):
            raw = {}
        return cls(
            max_worsening_seeds=int(raw.get("max_worsening_seeds", 0)),
            max_worsening_delta=float(raw.get("max_worsening_delta", 0.0)),
            max_elapsed_sec=float(raw.get("max_elapsed_sec", config.time_limit_sec)),
            neutral_speedup_ratio=float(raw.get("neutral_speedup_ratio", 0.05)),
        )

    def describe(self) -> dict[str, Any]:
        return {
            "max_worsening_seeds": self.max_worsening_seeds,
            "max_worsening_delta": self.max_worsening_delta,
            "max_elapsed_sec": self.max_elapsed_sec,
            "neutral_speedup_ratio": self.neutral_speedup_ratio,
        }


def evaluate_acceptance(
    comparison: dict[str, Any], policy: AcceptancePolicy
) -> dict[str, Any]:
    """Apply the acceptance gate to a `compare_runs` summary.

    Returns a verdict dict with `decision` (accepted / neutral / rejected),
    human-readable `reasons`, and the applied `policy`. `neutral` means the
    candidate passed every safety check but showed no measurable gain; it is
    held for review instead of being labeled worse than the baseline.
    """
    reasons: list[str] = []
    common = int(comparison.get("common_seed_count") or 0)
    failures = int(comparison.get("candidate_failure_count") or 0)
    fixed = int(comparison.get("fixed_seed_count") or 0)
    wins = int(comparison.get("wins") or 0)
    losses = int(comparison.get("losses") or 0)
    mean_delta = comparison.get("mean_effective_delta")
    median_delta = comparison.get("median_effective_delta")
    min_delta = comparison.get("min_effective_delta")
    base_elapsed = comparison.get("base_max_elapsed_sec")
    new_elapsed = comparison.get("new_max_elapsed_sec")

    if common == 0:
        reasons.append("no common ok seeds to compare")
    if failures > 0:
        reasons.append(f"candidate fails or misses {failures} seed(s) the baseline solves")
    if losses > policy.max_worsening_seeds:
        reasons.append(
            f"{losses} worsening seed(s) exceed the budget of {policy.max_worsening_seeds}"
        )
    if losses > 0 and min_delta is not None:
        worst = max(0.0, -float(min_delta))
        if worst > policy.max_worsening_delta:
            reasons.append(
                f"worst per-seed worsening {worst} exceeds the budget of {policy.max_worsening_delta}"
            )
    if median_delta is not None and float(median_delta) < 0.0:
        reasons.append("median effective delta is negative")
    if (
        policy.max_elapsed_sec is not None
        and new_elapsed is not None
        and float(new_elapsed) > policy.max_elapsed_sec
    ):
        reasons.append(
            f"max elapsed {float(new_elapsed):.3f}s exceeds the limit of {policy.max_elapsed_sec}s"
        )
    if reasons:
        return _verdict("rejected", reasons, policy)

    mean = float(mean_delta) if mean_delta is not None else 0.0
    if fixed > 0 and mean >= 0.0:
        reasons.append(f"fixes {fixed} previously failing seed(s) without score regression")
        return _verdict("accepted", reasons, policy)
    if mean > 0.0:
        reasons.append(
            f"mean effective delta {mean} is positive with {wins} win(s), {losses} loss(es)"
        )
        return _verdict("accepted", reasons, policy)
    if wins == 0 and losses == 0:
        if (
            base_elapsed is not None
            and new_elapsed is not None
            and float(base_elapsed) > 0.0
            and float(new_elapsed) <= float(base_elapsed) * (1.0 - policy.neutral_speedup_ratio)
        ):
            reasons.append(
                f"score-neutral but max elapsed improves {float(base_elapsed):.3f}s -> {float(new_elapsed):.3f}s"
            )
            return _verdict("accepted", reasons, policy)
        reasons.append("score-neutral: no wins, no losses, and no measurable speedup")
        return _verdict("neutral", reasons, policy)
    reasons.append(f"mean effective delta {mean} is not positive")
    return _verdict("rejected", reasons, policy)


def _verdict(
    decision: str, reasons: list[str], policy: AcceptancePolicy
) -> dict[str, Any]:
    return {"decision": decision, "reasons": reasons, "policy": policy.describe()}
