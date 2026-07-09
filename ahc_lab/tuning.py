from __future__ import annotations

import json
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analysis import analyze_run
from .config import ProblemConfig
from .db import ExperimentDB
from .evaluator import Evaluator


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str  # "int" | "float" | "categorical"
    low: float | None = None
    high: float | None = None
    log: bool = False
    choices: tuple[str, ...] = ()


def optuna_module():
    """Return the optuna module when installed, else None (it is optional)."""
    try:
        import optuna  # type: ignore

        return optuna
    except ImportError:
        return None


def load_param_space(config: ProblemConfig) -> list[ParamSpec]:
    """Parse the `tuning:` block of a problem config into parameter specs.

    Expected shape (the simple-YAML subset: nested blocks, scalar leaves;
    categorical choices are a pipe-separated string):

        tuning:
          offset:
            type: int
            low: 0
            high: 10
          cooling:
            type: float
            low: 0.001
            high: 1.0
            log: true
          strategy:
            type: categorical
            choices: greedy|anneal
    """
    raw = config.raw.get("tuning")
    if not isinstance(raw, dict) or not raw:
        raise ValueError(
            "config.yaml has no tuning: block; define the parameter search space first"
        )
    specs: list[ParamSpec] = []
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ValueError(f"tuning.{name} must be a mapping with type/low/high or choices")
        ptype = str(spec.get("type", "float")).lower()
        if ptype == "categorical":
            choices = tuple(
                choice.strip()
                for choice in str(spec.get("choices", "")).split("|")
                if choice.strip()
            )
            if not choices:
                raise ValueError(f"tuning.{name}: categorical needs pipe-separated choices")
            specs.append(ParamSpec(name=name, type="categorical", choices=choices))
            continue
        if ptype not in ("int", "float"):
            raise ValueError(f"tuning.{name}: unsupported type {ptype}")
        low = spec.get("low")
        high = spec.get("high")
        if low is None or high is None or float(low) > float(high):
            raise ValueError(f"tuning.{name}: needs low <= high")
        specs.append(
            ParamSpec(
                name=name,
                type=ptype,
                low=float(low),
                high=float(high),
                log=bool(spec.get("log", False)),
            )
        )
    return specs


def run_tuning(
    root: Path,
    problem: str,
    *,
    seeds: list[int],
    n_trials: int,
    db: ExperimentDB | None = None,
    sampler: str = "auto",
    jobs: int | None = None,
    use_cache: bool = True,
    rng: random.Random | None = None,
    autopilot_trial_id: int | None = None,
) -> dict[str, Any]:
    """Search the problem's parameter space and return the best setting found.

    Every sampled setting is evaluated through the normal evaluator (so runs,
    the source-hash+params cache, and lineage all apply) and recorded in the
    `tuning_trials` table. The objective is the mean score over `seeds`,
    sign-adjusted so higher is better; settings with any failing seed are
    discarded. A defaults run (no parameters) is evaluated first as the
    reference the best setting must beat.
    """
    db = db or ExperimentDB.default(root)
    evaluator = Evaluator(root, problem, db)
    specs = load_param_space(evaluator.config)
    maximize = evaluator.config.maximize
    stamp = int(time.time())

    def evaluate_params(index: int, params: dict[str, Any] | None) -> tuple[float | None, int]:
        run_id = evaluator.evaluate(
            seeds,
            tag=f"tune_{stamp}_t{index}",
            run_type="tuning",
            params=params,
            jobs=jobs,
            use_cache=use_cache,
        )
        summary = analyze_run(db, run_id)
        if summary["ok_count"] != summary["case_count"] or summary["mean_score"] is None:
            return None, run_id
        mean = float(summary["mean_score"])
        return (mean if maximize else -mean), run_id

    baseline_value, baseline_run_id = evaluate_params(0, None)
    trials: list[dict[str, Any]] = []

    def record(params: dict[str, Any], value: float | None, run_id: int) -> None:
        db.record_tuning_trial(
            autopilot_trial_id=autopilot_trial_id,
            params=params,
            score=value,
            status="done" if value is not None else "failed",
        )
        trials.append({"params": params, "value": value, "run_id": run_id})

    optuna = optuna_module() if sampler in ("auto", "optuna") else None
    if sampler == "optuna" and optuna is None:
        raise RuntimeError("sampler=optuna requires the optuna package (pip install optuna)")
    used_sampler = "optuna-tpe" if optuna is not None else "random"

    if optuna is not None:
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=rng.randint(0, 2**31) if rng else None),
        )

        def objective(trial: Any) -> float:
            params = _suggest_optuna(trial, specs)
            value, run_id = evaluate_params(len(trials) + 1, params)
            record(params, value, run_id)
            if value is None:
                raise optuna.TrialPruned("a seed failed with these parameters")
            return value

        study.optimize(objective, n_trials=n_trials, catch=())
    else:
        rng = rng or random.Random()
        seen: set[str] = set()
        for index in range(1, n_trials + 1):
            params = _sample_random(specs, rng, seen)
            if params is None:
                break  # the (small) space is exhausted
            seen.add(json.dumps(params, sort_keys=True))
            value, run_id = evaluate_params(index, params)
            record(params, value, run_id)

    best = max(
        (t for t in trials if t["value"] is not None),
        key=lambda t: t["value"],
        default=None,
    )
    result = {
        "problem": problem,
        "sampler": used_sampler,
        "seeds": len(seeds),
        "trials_run": len(trials),
        "baseline_value": baseline_value,
        "baseline_run_id": baseline_run_id,
        "best_params": best["params"] if best else None,
        "best_value": best["value"] if best else None,
        "best_run_id": best["run_id"] if best else None,
        "improved": bool(
            best is not None
            and baseline_value is not None
            and best["value"] > baseline_value
        ),
    }
    out_dir = root / "experiments" / "tuning"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{problem}_best.json").write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
    )
    return result


def _suggest_optuna(trial: Any, specs: list[ParamSpec]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for spec in specs:
        if spec.type == "categorical":
            params[spec.name] = trial.suggest_categorical(spec.name, list(spec.choices))
        elif spec.type == "int":
            params[spec.name] = trial.suggest_int(spec.name, int(spec.low), int(spec.high))
        else:
            params[spec.name] = trial.suggest_float(
                spec.name, spec.low, spec.high, log=spec.log
            )
    return params


def _sample_random(
    specs: list[ParamSpec], rng: random.Random, seen: set[str], attempts: int = 100
) -> dict[str, Any] | None:
    """Random parameter set not yet tried; None when the space looks exhausted."""
    for _ in range(attempts):
        params: dict[str, Any] = {}
        for spec in specs:
            if spec.type == "categorical":
                params[spec.name] = rng.choice(list(spec.choices))
            elif spec.type == "int":
                params[spec.name] = rng.randint(int(spec.low), int(spec.high))
            elif spec.log:
                params[spec.name] = math.exp(
                    rng.uniform(math.log(spec.low), math.log(spec.high))
                )
            else:
                params[spec.name] = rng.uniform(spec.low, spec.high)
        if json.dumps(params, sort_keys=True) not in seen:
            return params
    return None
