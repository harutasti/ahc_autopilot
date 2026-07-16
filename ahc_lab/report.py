from __future__ import annotations

import json
import re
import time
from typing import Any

from .db import ExperimentDB


def build_session_report(db: ExperimentDB, session_id: int) -> str:
    """Markdown retrospective for one autopilot session.

    Everything comes from the experiments DB, so this works for any past
    session: a trial table (decision, gate numbers, repair path, validation),
    the lineage tree of every run the session touched, and the mainline score
    trajectory across merged generations.
    """
    session = db.get_session(session_id)
    trials = db.list_trials(session_id)
    lines: list[str] = []
    lines.append(f"# Session {session_id} — {session['problem']} ({session['status']})")
    lines.append("")
    duration = ""
    if session.get("finished_at") and session.get("created_at"):
        minutes = (session["finished_at"] - session["created_at"]) / 60
        duration = f", {minutes:.0f} min"
    started = time.strftime("%Y-%m-%d %H:%M", time.localtime(session["created_at"]))
    merged = sum(1 for t in trials if t["decision"] == "accepted" and _merged(db, t))
    lines.append(
        f"- started {started}{duration}; trials {len(trials)}, merged {merged}"
    )
    lines.append("")

    lines.append("## Trials")
    lines.append("")
    lines.append("| trial | gen | role | decision | mean Δ | W/T/L | conf | validation | repairs |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for trial in trials:
        summary = _summary(trial)
        gen = _generation(trial["candidate_name"])
        delta = summary.get("mean_effective_delta")
        wtl = (
            f"{summary.get('wins')}/{summary.get('ties')}/{summary.get('losses')}"
            if summary.get("wins") is not None
            else ""
        )
        conf = summary.get("improve_confidence")
        validation = (summary.get("validation") or {}).get("acceptance", {}).get("decision", "")
        repairs = " → ".join(
            str(a.get("decision")) for a in (summary.get("repair") or {}).get("attempts", [])
        )
        lines.append(
            f"| {trial['id']} | {gen} | {trial['role']} | {trial['decision']} "
            f"| {_num(delta)} | {wtl} | {_num(conf)} | {validation} | {repairs} |"
        )
    lines.append("")

    reasons = _final_reasons(trials)
    if reasons:
        lines.append("## Final gate reasons")
        lines.append("")
        for trial_id, reason in reasons:
            lines.append(f"- trial {trial_id}: {reason}")
        lines.append("")

    lines.append("## Run lineage")
    lines.append("")
    lines.append("```")
    lines.extend(_lineage_tree(db, trials))
    lines.append("```")
    lines.append("")

    trajectory = _trajectory(db, trials)
    if trajectory:
        lines.append("## Mainline trajectory")
        lines.append("")
        for entry in trajectory:
            lines.append(f"- {entry}")
        lines.append("")
    return "\n".join(lines)


def _summary(trial: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(trial.get("summary_json") or "{}")
    except json.JSONDecodeError:
        return {}


def _generation(candidate_name: str) -> str:
    match = re.match(r"g(\d+)_", candidate_name or "")
    return match.group(1) if match else ""


def _num(value: Any) -> str:
    if value is None:
        return ""
    return f"{value:,.0f}" if abs(float(value)) >= 100 else f"{float(value):.3f}"


def _merged(db: ExperimentDB, trial: dict[str, Any]) -> bool:
    row = db.conn.execute(
        "select 1 from patches where trial_id = ? and status = 'merged'", (trial["id"],)
    ).fetchone()
    return row is not None


def _final_reasons(trials: list[dict[str, Any]]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for trial in trials:
        summary = _summary(trial)
        if trial["decision"] in ("accepted", None):
            continue
        reasons = (summary.get("acceptance") or {}).get("reasons") or []
        if summary.get("error"):
            reasons = [summary["error"], *reasons]
        if reasons:
            out.append((trial["id"], "; ".join(str(r) for r in reasons)[:200]))
    return out


def _run_mean(db: ExperimentDB, run_id: int) -> float | None:
    row = db.conn.execute(
        "select avg(score) as mean from cases where run_id = ? and status = 'ok'",
        (run_id,),
    ).fetchone()
    return float(row["mean"]) if row and row["mean"] is not None else None


def _session_run_ids(db: ExperimentDB, trials: list[dict[str, Any]]) -> list[int]:
    ids: set[int] = set()
    for trial in trials:
        for key in ("base_run_id", "new_run_id"):
            if trial.get(key):
                ids.add(int(trial[key]))
        summary = _summary(trial)
        for attempt in (summary.get("repair") or {}).get("attempts", []):
            if attempt.get("run_id"):
                ids.add(int(attempt["run_id"]))
        validation = summary.get("validation") or {}
        for key in ("baseline_run_id", "candidate_run_id"):
            if validation.get(key):
                ids.add(int(validation[key]))
    return sorted(ids)


def _lineage_tree(db: ExperimentDB, trials: list[dict[str, Any]]) -> list[str]:
    run_ids = _session_run_ids(db, trials)
    runs = {rid: db.get_run(rid) for rid in run_ids}
    children: dict[int | None, list[int]] = {}
    for rid, run in runs.items():
        parent = run.get("parent_run_id")
        key = parent if parent in runs else None
        children.setdefault(key, []).append(rid)

    lines: list[str] = []

    def render(rid: int, depth: int) -> None:
        run = runs[rid]
        mean = _run_mean(db, rid)
        mean_text = f" mean {mean:,.0f}" if mean is not None else ""
        prefix = "   " * depth + ("└─ " if depth else "")
        lines.append(
            f"{prefix}run {rid} [{run['run_type']}/{run['status']}]{mean_text} {run['tag']}"
        )
        for child in sorted(children.get(rid, [])):
            render(child, depth + 1)

    for root in sorted(children.get(None, [])):
        render(root, 0)
    return lines or ["(no runs recorded)"]


def _trajectory(db: ExperimentDB, trials: list[dict[str, Any]]) -> list[str]:
    entries: list[str] = []
    baseline_ids = sorted(
        {int(t["base_run_id"]) for t in trials if t.get("base_run_id")}
    )
    if baseline_ids:
        first = baseline_ids[0]
        mean = _run_mean(db, first)
        if mean is not None:
            entries.append(f"baseline run {first}: mean {mean:,.0f}")
    for trial in trials:
        if trial["decision"] != "accepted" or not _merged(db, trial):
            continue
        mean = _run_mean(db, int(trial["new_run_id"]))
        summary = _summary(trial)
        delta = summary.get("mean_effective_delta")
        delta_text = f" ({float(delta):+,.0f})" if delta is not None else ""
        if mean is not None:
            entries.append(
                f"g{_generation(trial['candidate_name'])} {trial['role']} merged "
                f"run {trial['new_run_id']}: mean {mean:,.0f}{delta_text}"
            )
    return entries
