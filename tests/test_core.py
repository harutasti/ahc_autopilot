from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ahc_lab.agents import (
    SPECIALISTS,
    Autopilot,
    build_agent_prompt,
    classify_adapter_failure,
    parse_roles_file,
    select_specialists,
)
from ahc_lab.analysis import analyze_run, compare_runs
from ahc_lab.archive import build_archive, sample_parents
from ahc_lab.cli import _init_problem, _parse_roles, main as cli_main
from ahc_lab.config import load_problem_config
from ahc_lab.db import ExperimentDB
from ahc_lab.evaluator import Evaluator
from ahc_lab.knowledge import search_knowledge
from ahc_lab.novelty import normalize_cpp, normalized_source_fingerprint
from ahc_lab.policy import AcceptancePolicy, evaluate_acceptance
from ahc_lab.runner import run_shell
from ahc_lab.seeds import parse_seed_spec
from ahc_lab.snapshots import (
    diff_snapshots,
    restore_solver_source,
    snapshot_solver_source,
    store_dir_for,
)
from ahc_lab.tuning import load_param_space, optuna_module, run_tuning


ECHO_MINUS_TEN_SOLVER = """
#include <iostream>
int main() {
    long long target;
    if (!(std::cin >> target)) return 0;
    std::cout << target - 10 << '\\n';
    return 0;
}
"""

ECHO_SOLVER = """
#include <iostream>
int main() {
    long long target;
    if (!(std::cin >> target)) return 0;
    std::cout << target << '\\n';
    return 0;
}
"""

ECHO_MINUS_FIVE_SOLVER = """
#include <iostream>
int main() {
    long long target;
    if (!(std::cin >> target)) return 0;
    std::cout << target - 5 << '\\n';
    return 0;
}
"""

CRASHY_LARGE_TARGET_SOLVER = """
#include <iostream>
int main() {
    long long target;
    if (!(std::cin >> target)) return 0;
    if (target > 500000) return 1;
    std::cout << target << '\\n';
    return 0;
}
"""

ECHO_MINUS_TWENTY_SOLVER = """
#include <iostream>
int main() {
    long long target;
    if (!(std::cin >> target)) return 0;
    std::cout << target - 20 << '\\n';
    return 0;
}
"""

BROKEN_SOLVER = """
#include <iostream>
int main() { this does not compile
"""

PARAM_OFFSET_SOLVER = """
#include <cstdlib>
#include <iostream>
int main() {
    long long target;
    if (!(std::cin >> target)) return 0;
    const char* env = std::getenv("AHC_PARAM_OFFSET");
    long long offset = env ? std::atoll(env) : 10;
    std::cout << target - offset << '\\n';
    return 0;
}
"""

ECHO_MINUS_TEN_COMMENTED_SOLVER = """
// cosmetic edit: comments and whitespace only
#include <iostream>
/* block comment */
int main() {
    long long target;   // read the target
    if (!(std::cin >> target))    return 0;
    std::cout << target - 10 << '\\n';
    return 0;
}
"""


ROOT = Path(__file__).resolve().parents[1]


class RunnerTest(unittest.TestCase):
    def test_timeout_kills_process_group(self) -> None:
        marker = f"ahc_orphan_{os.getpid()}"
        command = f"python3 -c \"import time; time.sleep(60) # {marker}\" && true"
        result = run_shell(command, Path(tempfile.gettempdir()), timeout=1.0)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.returncode, 124)
        time.sleep(0.3)
        leftover = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
        self.assertEqual(leftover.stdout.strip(), "", "hung child survived the timeout kill")


class SeedSpecTest(unittest.TestCase):
    def test_parse_seed_spec(self) -> None:
        self.assertEqual(parse_seed_spec("0-3"), [0, 1, 2, 3])
        self.assertEqual(parse_seed_spec("1,3,5"), [1, 3, 5])
        self.assertEqual(parse_seed_spec("0-10:5"), [0, 5, 10])
        self.assertEqual(parse_seed_spec("1,1,2"), [1, 2])


class EvaluationTest(unittest.TestCase):
    def test_dummy_evaluation_and_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            shutil.copytree(ROOT / "solver", tmp / "solver")
            shutil.copytree(ROOT / "problems", tmp / "problems")
            (tmp / "solver" / "main.cpp").write_text(
                """
#include <iostream>
int main() {
    long long target;
    if (!(std::cin >> target)) return 0;
    std::cout << target << '\\n';
    return 0;
}
""",
                encoding="utf-8",
            )
            db = ExperimentDB.default(tmp)
            run_id = Evaluator(tmp, "dummy", db).evaluate([0, 1, 2], tag="test")
            summary = analyze_run(db, run_id)
            self.assertEqual(summary["ok_count"], 3)
            self.assertEqual(summary["mean_score"], 1_000_000.0)
            run_id_2 = Evaluator(tmp, "dummy", db).evaluate([0, 1, 2], tag="test2")
            comparison = compare_runs(db, run_id, run_id_2)
            self.assertEqual(comparison["common_seed_count"], 3)
            self.assertEqual(comparison["losses"], 0)

    def test_config_tool_timeouts(self) -> None:
        config = load_problem_config(ROOT, "dummy")
        self.assertEqual(config.generator_timeout_sec, 30.0)
        self.assertEqual(config.score_timeout_sec, 60.0)

    def test_init_problem_creates_statement_templates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            shutil.copytree(ROOT / "problems", tmp / "problems")
            _init_problem(tmp, "ahc999")
            self.assertTrue((tmp / "problems" / "ahc999" / "statement.md").exists())
            self.assertTrue((tmp / "problems" / "ahc999" / "problem_brief.md").exists())
            statement = (tmp / "problems" / "ahc999" / "statement.md").read_text(
                encoding="utf-8"
            )
            config_text = (tmp / "problems" / "ahc999" / "config.yaml").read_text(
                encoding="utf-8"
            )
            self.assertIn("# ahc999", statement)
            self.assertIn("# Problem name.", config_text)
            self.assertIn("name: ahc999", config_text)
            config = load_problem_config(tmp, "ahc999")
            self.assertEqual(config.name, "ahc999")


def _make_dummy_root(tmp: Path, solver_source: str) -> Path:
    (tmp / "solver").mkdir()
    (tmp / "solver" / "main.cpp").write_text(solver_source, encoding="utf-8")
    shutil.copytree(ROOT / "problems", tmp / "problems")
    return tmp


class ParallelAndCacheTest(unittest.TestCase):
    def test_parallel_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_dummy_root(Path(tmp_s), ECHO_SOLVER)
            db = ExperimentDB.default(tmp)
            run_id = Evaluator(tmp, "dummy", db).evaluate(
                list(range(8)), tag="parallel", jobs=4
            )
            summary = analyze_run(db, run_id)
            self.assertEqual(summary["ok_count"], 8)
            self.assertEqual(summary["mean_score"], 1_000_000.0)

    def test_cache_reuses_previous_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_dummy_root(Path(tmp_s), ECHO_SOLVER)
            db = ExperimentDB.default(tmp)
            evaluator = Evaluator(tmp, "dummy", db)
            first = evaluator.evaluate([0, 1, 2], tag="first")
            first_cases = {c["seed"]: c for c in db.list_cases(first)}
            self.assertTrue(all(c["source_case_id"] is None for c in first_cases.values()))
            second = evaluator.evaluate([0, 1, 2], tag="second")
            second_cases = {c["seed"]: c for c in db.list_cases(second)}
            for seed, case in second_cases.items():
                self.assertEqual(case["source_case_id"], first_cases[seed]["id"])
                self.assertEqual(case["score"], first_cases[seed]["score"])
            no_cache = evaluator.evaluate([0, 1, 2], tag="no_cache", use_cache=False)
            self.assertTrue(
                all(c["source_case_id"] is None for c in db.list_cases(no_cache))
            )
            (tmp / "solver" / "main.cpp").write_text(ECHO_MINUS_TEN_SOLVER, encoding="utf-8")
            changed = evaluator.evaluate([0, 1, 2], tag="changed")
            self.assertTrue(
                all(c["source_case_id"] is None for c in db.list_cases(changed))
            )


class CascadeTest(unittest.TestCase):
    def test_cascade_prunes_worsening_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_dummy_root(Path(tmp_s), ECHO_SOLVER)
            db = ExperimentDB.default(tmp)
            evaluator = Evaluator(tmp, "dummy", db)
            baseline = evaluator.evaluate(list(range(10)), tag="base")
            (tmp / "solver" / "main.cpp").write_text(ECHO_MINUS_TEN_SOLVER, encoding="utf-8")
            run_id, cascade = evaluator.evaluate_cascade(
                list(range(10)),
                tag="candidate",
                baseline_run_id=baseline,
                policy=AcceptancePolicy(),
                stage_sizes=(3, 3),
            )
            self.assertTrue(cascade["pruned"])
            self.assertEqual(cascade["evaluated_seed_count"], 3)
            self.assertIn("worsening", cascade["prune_reason"])
            self.assertEqual(db.get_run(run_id)["status"], "pruned")

    def test_cascade_completes_for_equal_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_dummy_root(Path(tmp_s), ECHO_SOLVER)
            db = ExperimentDB.default(tmp)
            evaluator = Evaluator(tmp, "dummy", db)
            baseline = evaluator.evaluate(list(range(10)), tag="base")
            run_id, cascade = evaluator.evaluate_cascade(
                list(range(10)),
                tag="candidate",
                baseline_run_id=baseline,
                policy=AcceptancePolicy(),
                stage_sizes=(3, 3),
            )
            self.assertFalse(cascade["pruned"])
            self.assertEqual(cascade["evaluated_seed_count"], 10)
            self.assertEqual(db.get_run(run_id)["status"], "done")


class RelativeScoreTest(unittest.TestCase):
    def test_relative_scores_against_best_known(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_dummy_root(Path(tmp_s), ECHO_SOLVER)
            db = ExperimentDB.default(tmp)
            evaluator = Evaluator(tmp, "dummy", db)
            good = evaluator.evaluate([0, 1, 2], tag="good")
            (tmp / "solver" / "main.cpp").write_text(ECHO_MINUS_TEN_SOLVER, encoding="utf-8")
            bad = evaluator.evaluate([0, 1, 2], tag="bad")
            good_summary = analyze_run(db, good)
            bad_summary = analyze_run(db, bad)
            self.assertAlmostEqual(good_summary["mean_relative_score"], 1.0)
            self.assertLess(bad_summary["mean_relative_score"], 1.0)
            self.assertGreater(bad_summary["mean_relative_score"], 0.99)
            comparison = compare_runs(db, good, bad)
            self.assertAlmostEqual(comparison["base_mean_relative_score"], 1.0)
            self.assertLess(comparison["new_mean_relative_score"], 1.0)


class SnapshotTest(unittest.TestCase):
    def test_runs_record_and_restore_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            (tmp / "solver").mkdir()
            (tmp / "solver" / "main.cpp").write_text(ECHO_SOLVER, encoding="utf-8")
            shutil.copytree(ROOT / "problems", tmp / "problems")
            db = ExperimentDB.default(tmp)
            run_id = Evaluator(tmp, "dummy", db).evaluate([0], tag="snap")
            source_hash = db.get_run(run_id)["source_hash"]
            self.assertTrue(source_hash)
            snapshot = store_dir_for(db) / source_hash / "main.cpp"
            self.assertEqual(snapshot.read_text(encoding="utf-8"), ECHO_SOLVER)
            run_id_2 = Evaluator(tmp, "dummy", db).evaluate([0], tag="snap2")
            self.assertEqual(db.get_run(run_id_2)["source_hash"], source_hash)
            with tempfile.TemporaryDirectory() as other_s:
                other = Path(other_s)
                restore_solver_source(store_dir_for(db), source_hash, other)
                restored = (other / "solver" / "main.cpp").read_text(encoding="utf-8")
                self.assertEqual(restored, ECHO_SOLVER)

    def test_diff_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            (tmp / "solver").mkdir()
            store = tmp / "store"
            (tmp / "solver" / "main.cpp").write_text(ECHO_MINUS_TEN_SOLVER, encoding="utf-8")
            base_hash = snapshot_solver_source(tmp, store)
            (tmp / "solver" / "main.cpp").write_text(ECHO_SOLVER, encoding="utf-8")
            new_hash = snapshot_solver_source(tmp, store)
            diff = diff_snapshots(store, base_hash, new_hash)
            self.assertIn("-    std::cout << target - 10 << '\\n';", diff)
            self.assertIn("+    std::cout << target << '\\n';", diff)

    def test_snapshot_dedup_and_change_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            (tmp / "solver").mkdir()
            (tmp / "solver" / "main.cpp").write_text(ECHO_SOLVER, encoding="utf-8")
            store = tmp / "store"
            first = snapshot_solver_source(tmp, store)
            self.assertEqual(snapshot_solver_source(tmp, store), first)
            (tmp / "solver" / "main.cpp").write_text(ECHO_MINUS_TEN_SOLVER, encoding="utf-8")
            self.assertNotEqual(snapshot_solver_source(tmp, store), first)


class AutopilotMergeTest(unittest.TestCase):
    def _setup_root(self, tmp: Path) -> Path:
        (tmp / "solver").mkdir()
        (tmp / "solver" / "main.cpp").write_text(ECHO_MINUS_TEN_SOLVER, encoding="utf-8")
        shutil.copytree(ROOT / "problems", tmp / "problems")
        adapter = tmp / "adapter.py"
        adapter.write_text(
            f"""import pathlib, sys
target = pathlib.Path(sys.argv[1]) / "solver" / "main.cpp"
target.write_text({ECHO_SOLVER!r}, encoding="utf-8")
""",
            encoding="utf-8",
        )
        return adapter

    def _run_autopilot(self, tmp: Path, adapter: Path, *, merge_accepted: bool, **kwargs):
        db = ExperimentDB.default(tmp)
        result = Autopilot(tmp, "dummy", db).run(
            budget_sec=None,
            max_trials=kwargs.pop("max_trials", 1),
            agent_count=kwargs.pop("agent_count", 1),
            seeds=kwargs.pop("seeds", "0-2"),
            adapter_command=f"{sys.executable} {adapter} {{cwd}}",
            roles=kwargs.pop("roles", ["AnnealingBuilder"]),
            merge_accepted=merge_accepted,
            **kwargs,
        )
        return db, result

    def test_accepted_candidate_is_merged_into_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            adapter = self._setup_root(tmp)
            db, result = self._run_autopilot(tmp, adapter, merge_accepted=True)
            trial = db.conn.execute(
                "select * from autopilot_trials where session_id = ?", (result.session_id,)
            ).fetchone()
            self.assertEqual(trial["decision"], "accepted")
            self.assertEqual(result.merged_count, 1)
            self.assertEqual(result.status, "completed")
            self.assertNotEqual(result.final_baseline_run_id, result.baseline_run_id)
            merged = (tmp / "solver" / "main.cpp").read_text(encoding="utf-8")
            self.assertEqual(merged, ECHO_SOLVER)
            patch = db.conn.execute(
                "select * from patches where trial_id = ?", (trial["id"],)
            ).fetchone()
            self.assertIsNotNone(patch)
            self.assertEqual(patch["status"], "merged")
            session = db.conn.execute(
                "select status from autopilot_sessions where id = ?", (result.session_id,)
            ).fetchone()
            self.assertEqual(session["status"], "completed")
            candidate_run = db.get_run(trial["new_run_id"])
            self.assertEqual(candidate_run["parent_run_id"], result.baseline_run_id)
            insights = (tmp / "knowledge" / "dummy_autopilot.md").read_text(encoding="utf-8")
            self.assertIn("accepted", insights)
            diffs = Autopilot(tmp, "dummy", db)._recent_accepted_diffs()
            self.assertIn("target - 10", diffs)

    def test_no_merge_leaves_root_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            adapter = self._setup_root(tmp)
            db, result = self._run_autopilot(tmp, adapter, merge_accepted=False)
            trial = db.conn.execute(
                "select * from autopilot_trials where session_id = ?", (result.session_id,)
            ).fetchone()
            self.assertEqual(trial["decision"], "accepted")
            self.assertEqual(result.merged_count, 0)
            self.assertEqual(result.final_baseline_run_id, result.baseline_run_id)
            untouched = (tmp / "solver" / "main.cpp").read_text(encoding="utf-8")
            self.assertEqual(untouched, ECHO_MINUS_TEN_SOLVER)


class AutopilotGenerationTest(unittest.TestCase):
    def test_generation_loop_improves_until_stagnation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_dummy_root(Path(tmp_s), ECHO_MINUS_TEN_SOLVER)
            adapter = tmp / "adapter.py"
            adapter.write_text(
                f"""import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "solver" / "main.cpp"
text = p.read_text(encoding="utf-8")
if "target - 10" in text:
    p.write_text({ECHO_MINUS_FIVE_SOLVER!r}, encoding="utf-8")
elif "target - 5" in text:
    p.write_text({ECHO_SOLVER!r}, encoding="utf-8")
""",
                encoding="utf-8",
            )
            db = ExperimentDB.default(tmp)
            result = Autopilot(tmp, "dummy", db).run(
                budget_sec=None,
                max_trials=1,
                agent_count=1,
                seeds="0-2",
                adapter_command=f"{sys.executable} {adapter} {{cwd}}",
                roles=["AnnealingBuilder"],
                generations=5,
            )
            self.assertEqual(result.merged_count, 2)
            self.assertEqual(result.generations_run, 3)
            self.assertEqual(
                (tmp / "solver" / "main.cpp").read_text(encoding="utf-8"), ECHO_SOLVER
            )
            self.assertNotEqual(result.final_baseline_run_id, result.baseline_run_id)
            decisions = [
                row["decision"]
                for row in db.conn.execute(
                    "select decision from autopilot_trials where session_id = ? order by id",
                    (result.session_id,),
                )
            ]
            # Generation 3 leaves the solver unchanged, so the novelty filter
            # skips it as a duplicate of the merged source instead of
            # re-evaluating it to a neutral verdict.
            self.assertEqual(decisions, ["accepted", "accepted", "duplicate"])

    def test_parallel_trials_merge_best_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_dummy_root(Path(tmp_s), ECHO_MINUS_TEN_SOLVER)
            adapter = tmp / "adapter.py"
            adapter.write_text(
                f"""import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "solver" / "main.cpp"
p.write_text({ECHO_SOLVER!r}, encoding="utf-8")
""",
                encoding="utf-8",
            )
            db = ExperimentDB.default(tmp)
            result = Autopilot(tmp, "dummy", db).run(
                budget_sec=None,
                max_trials=2,
                agent_count=2,
                seeds="0-2",
                adapter_command=f"{sys.executable} {adapter} {{cwd}}",
                roles=["AnnealingBuilder", "StateDesignBuilder"],
            )
            decisions = [
                row["decision"]
                for row in db.conn.execute(
                    "select decision from autopilot_trials where session_id = ?",
                    (result.session_id,),
                )
            ]
            self.assertEqual(sorted(decisions), ["accepted", "accepted"])
            self.assertEqual(result.merged_count, 1)
            merges = db.conn.execute(
                "select count(*) as n from patches where status = 'merged'"
            ).fetchone()["n"]
            self.assertEqual(merges, 1)
            self.assertEqual(
                (tmp / "solver" / "main.cpp").read_text(encoding="utf-8"), ECHO_SOLVER
            )

    def test_validation_gate_blocks_candidate(self) -> None:
        targets = {s: random.Random(s).randint(0, 1_000_000) for s in range(60)}
        eval_seeds = [s for s in range(60) if targets[s] <= 400_000][:3]
        val_seeds = [s for s in range(60) if targets[s] >= 600_000][:2]
        self.assertEqual(len(eval_seeds), 3)
        self.assertEqual(len(val_seeds), 2)
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_dummy_root(Path(tmp_s), ECHO_MINUS_TEN_SOLVER)
            adapter = tmp / "adapter.py"
            adapter.write_text(
                f"""import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "solver" / "main.cpp"
p.write_text({CRASHY_LARGE_TARGET_SOLVER!r}, encoding="utf-8")
""",
                encoding="utf-8",
            )
            db = ExperimentDB.default(tmp)
            result = Autopilot(tmp, "dummy", db).run(
                budget_sec=None,
                max_trials=1,
                agent_count=1,
                seeds=",".join(str(s) for s in eval_seeds),
                validation_seeds=",".join(str(s) for s in val_seeds),
                adapter_command=f"{sys.executable} {adapter} {{cwd}}",
                roles=["AnnealingBuilder"],
            )
            trial = db.conn.execute(
                "select * from autopilot_trials where session_id = ?", (result.session_id,)
            ).fetchone()
            self.assertEqual(trial["decision"], "validation_failed")
            self.assertEqual(result.merged_count, 0)
            self.assertEqual(
                (tmp / "solver" / "main.cpp").read_text(encoding="utf-8"),
                ECHO_MINUS_TEN_SOLVER,
            )


class AutopilotRepairTest(unittest.TestCase):
    def _run(self, tmp: Path, adapter_body: str, *, repair_attempts: int, **kwargs):
        tmp = _make_dummy_root(tmp, ECHO_MINUS_TEN_SOLVER)
        adapter = tmp / "adapter.py"
        adapter.write_text(adapter_body, encoding="utf-8")
        db = ExperimentDB.default(tmp)
        result = Autopilot(tmp, "dummy", db).run(
            budget_sec=None,
            max_trials=1,
            agent_count=1,
            seeds=kwargs.pop("seeds", "0-2"),
            adapter_command=f"{sys.executable} {adapter} {{cwd}}",
            roles=["AnnealingBuilder"],
            repair_attempts=repair_attempts,
            **kwargs,
        )
        trial = db.conn.execute(
            "select * from autopilot_trials where session_id = ?", (result.session_id,)
        ).fetchone()
        return db, result, trial, json.loads(trial["summary_json"])

    def test_rejected_candidate_is_repaired_after_feedback(self) -> None:
        adapter_body = f"""import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "solver" / "main.cpp"
if "target - 20" in p.read_text(encoding="utf-8"):
    p.write_text({ECHO_SOLVER!r}, encoding="utf-8")
else:
    p.write_text({ECHO_MINUS_TWENTY_SOLVER!r}, encoding="utf-8")
"""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            db, result, trial, summary = self._run(tmp, adapter_body, repair_attempts=1)
            self.assertEqual(trial["decision"], "accepted")
            self.assertEqual(result.merged_count, 1)
            self.assertEqual(
                (tmp / "solver" / "main.cpp").read_text(encoding="utf-8"), ECHO_SOLVER
            )
            attempts = summary["repair"]["attempts"]
            self.assertEqual([a["decision"] for a in attempts], ["rejected", "accepted"])
            first_run = db.get_run(attempts[0]["run_id"])
            second_run = db.get_run(attempts[1]["run_id"])
            self.assertEqual(first_run["parent_run_id"], result.baseline_run_id)
            self.assertEqual(second_run["parent_run_id"], attempts[0]["run_id"])
            repair_prompt = (
                result.prompt_dir / "g1_candidate_1_AnnealingBuilder_repair1.md"
            ).read_text(encoding="utf-8")
            self.assertIn("acceptance gate rejected", repair_prompt)
            self.assertIn("Measured numbers", repair_prompt)
            self.assertIn("Original task prompt", repair_prompt)
            messages = db.conn.execute(
                "select count(*) as n from agent_messages where role = 'repair_prompt'"
            ).fetchone()["n"]
            self.assertEqual(messages, 1)
            insights = (tmp / "knowledge" / "dummy_autopilot.md").read_text(encoding="utf-8")
            self.assertIn("repair attempts: 2 (rejected -> accepted)", insights)

    def test_repair_stops_when_agent_makes_no_change(self) -> None:
        adapter_body = f"""import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "solver" / "main.cpp"
p.write_text({ECHO_MINUS_TWENTY_SOLVER!r}, encoding="utf-8")
"""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            db, result, trial, summary = self._run(tmp, adapter_body, repair_attempts=3)
            self.assertEqual(trial["decision"], "rejected")
            self.assertEqual(result.merged_count, 0)
            attempts = summary["repair"]["attempts"]
            # The unchanged repair is detected before evaluation, so the second
            # attempt is recorded as a duplicate and the trial keeps the
            # rejected verdict of the first attempt.
            self.assertEqual([a["decision"] for a in attempts], ["rejected", "duplicate"])
            self.assertTrue(attempts[1]["no_source_change"])
            self.assertTrue(
                (result.prompt_dir / "g1_candidate_1_AnnealingBuilder_repair1.md").exists()
            )
            self.assertFalse(
                (result.prompt_dir / "g1_candidate_1_AnnealingBuilder_repair2.md").exists()
            )

    def test_failed_attempt_feeds_error_back_and_recovers(self) -> None:
        adapter_body = f"""import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "solver" / "main.cpp"
if "does not compile" in p.read_text(encoding="utf-8"):
    p.write_text({ECHO_SOLVER!r}, encoding="utf-8")
else:
    p.write_text({BROKEN_SOLVER!r}, encoding="utf-8")
"""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            db, result, trial, summary = self._run(tmp, adapter_body, repair_attempts=1)
            self.assertEqual(trial["decision"], "accepted")
            self.assertEqual(result.merged_count, 1)
            attempts = summary["repair"]["attempts"]
            self.assertEqual([a["decision"] for a in attempts], ["error", "accepted"])
            repair_prompt = (
                result.prompt_dir / "g1_candidate_1_AnnealingBuilder_repair1.md"
            ).read_text(encoding="utf-8")
            self.assertIn("failed before it could be scored", repair_prompt)
            self.assertIn("build failed", repair_prompt)

    def test_repair_disabled_by_default_keeps_single_attempt(self) -> None:
        adapter_body = f"""import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "solver" / "main.cpp"
p.write_text({ECHO_MINUS_TWENTY_SOLVER!r}, encoding="utf-8")
"""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            db, result, trial, summary = self._run(tmp, adapter_body, repair_attempts=0)
            self.assertEqual(trial["decision"], "rejected")
            self.assertNotIn("repair", summary)
            self.assertFalse(
                (result.prompt_dir / "g1_candidate_1_AnnealingBuilder_repair1.md").exists()
            )

    def test_pruned_rejection_feedback_notes_unevaluated_seeds(self) -> None:
        adapter_body = f"""import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "solver" / "main.cpp"
if "target - 20" in p.read_text(encoding="utf-8"):
    p.write_text({ECHO_SOLVER!r}, encoding="utf-8")
else:
    p.write_text({ECHO_MINUS_TWENTY_SOLVER!r}, encoding="utf-8")
"""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            db, result, trial, summary = self._run(
                tmp, adapter_body, repair_attempts=1, seeds="0-6"
            )
            self.assertEqual(trial["decision"], "accepted")
            repair_prompt = (
                result.prompt_dir / "g1_candidate_1_AnnealingBuilder_repair1.md"
            ).read_text(encoding="utf-8")
            self.assertIn("pruned early after 5 of 7 seeds", repair_prompt)
            self.assertIn("were never evaluated because of the pruning", repair_prompt)

    def test_repair_no_change_after_error_fails_trial(self) -> None:
        adapter_body = f"""import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "solver" / "main.cpp"
p.write_text({BROKEN_SOLVER!r}, encoding="utf-8")
"""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            db, result, trial, summary = self._run(tmp, adapter_body, repair_attempts=1)
            self.assertEqual(trial["decision"], "error")
            self.assertEqual(trial["status"], "failed")
            attempts = summary["repair"]["attempts"]
            self.assertEqual([a["decision"] for a in attempts], ["error", "duplicate"])
            self.assertTrue(attempts[1]["no_source_change"])
            self.assertIn("build failed", summary["error"])


def _make_tuning_root(tmp: Path) -> Path:
    root = _make_dummy_root(tmp, PARAM_OFFSET_SOLVER)
    config = root / "problems" / "dummy" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "\ntuning:\n  offset:\n    type: int\n    low: 0\n    high: 10\n",
        encoding="utf-8",
    )
    return root


class TuningTest(unittest.TestCase):
    def test_params_reach_solver_via_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_tuning_root(Path(tmp_s))
            db = ExperimentDB.default(tmp)
            evaluator = Evaluator(tmp, "dummy", db)
            default_run = evaluator.evaluate([0, 1], tag="default")
            self.assertEqual(analyze_run(db, default_run)["mean_score"], 999_990.0)
            tuned_run = evaluator.evaluate([0, 1], tag="tuned", params={"offset": 3})
            self.assertEqual(analyze_run(db, tuned_run)["mean_score"], 999_997.0)
            # Same params hit the (source, seed, params) cache.
            cached_run = evaluator.evaluate([0, 1], tag="again", params={"offset": 3})
            reused = db.conn.execute(
                "select count(*) as n from cases where run_id = ? and source_case_id is not null",
                (cached_run,),
            ).fetchone()["n"]
            self.assertEqual(reused, 2)

    def test_load_param_space_parses_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_tuning_root(Path(tmp_s))
            config = load_problem_config(tmp, "dummy")
            specs = load_param_space(config)
            self.assertEqual(len(specs), 1)
            self.assertEqual((specs[0].name, specs[0].type), ("offset", "int"))
            self.assertEqual((specs[0].low, specs[0].high), (0.0, 10.0))
            bad = tmp / "problems" / "dummy" / "config.yaml"
            bad.write_text(
                bad.read_text(encoding="utf-8").replace("type: int", "type: mystery"),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_param_space(load_problem_config(tmp, "dummy"))

    def test_random_tuning_finds_best_offset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_tuning_root(Path(tmp_s))
            db = ExperimentDB.default(tmp)
            result = run_tuning(
                tmp,
                "dummy",
                seeds=[0, 1],
                n_trials=30,
                db=db,
                sampler="random",
                rng=random.Random(7),
            )
            self.assertEqual(result["sampler"], "random")
            self.assertEqual(result["best_params"], {"offset": 0})
            self.assertEqual(result["best_value"], 1_000_000.0)
            self.assertEqual(result["baseline_value"], 999_990.0)
            self.assertTrue(result["improved"])
            # The 11-value space is exhausted, then sampling stops.
            self.assertEqual(result["trials_run"], 11)
            rows = db.conn.execute(
                "select count(*) as n from tuning_trials where trial_id = 0"
            ).fetchone()["n"]
            self.assertEqual(rows, 11)
            best_file = tmp / "experiments" / "tuning" / "dummy_best.json"
            self.assertTrue(best_file.exists())

    def test_tune_cli_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_tuning_root(Path(tmp_s))
            code = cli_main(
                [
                    "--root", str(tmp),
                    "tune", "--problem", "dummy",
                    "--seeds", "0-1", "--trials", "3", "--sampler", "random",
                ]
            )
            self.assertEqual(code, 0)

    @unittest.skipUnless(optuna_module() is not None, "optuna not installed")
    def test_optuna_sampler_runs_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_tuning_root(Path(tmp_s))
            result = run_tuning(
                tmp,
                "dummy",
                seeds=[0, 1],
                n_trials=8,
                sampler="optuna",
                rng=random.Random(7),
            )
            self.assertEqual(result["sampler"], "optuna-tpe")
            self.assertIsNotNone(result["best_params"])
            self.assertGreaterEqual(result["best_value"], 999_990.0)


class AdapterFatalTest(unittest.TestCase):
    def test_classify_adapter_failure(self) -> None:
        self.assertEqual(
            classify_adapter_failure("You've hit your session limit · resets 3am (UTC)"),
            "rate_limit",
        )
        self.assertEqual(
            classify_adapter_failure(
                "--dangerously-skip-permissions cannot be used with root/sudo privileges"
            ),
            "config",
        )
        self.assertEqual(classify_adapter_failure("Invalid API key provided"), "auth")
        self.assertIsNone(classify_adapter_failure("g++: error: main.cpp: No such file"))
        self.assertIsNone(classify_adapter_failure(""))

    def test_fatal_adapter_failure_skips_repair_and_aborts_session(self) -> None:
        adapter_body = """import sys
print("You've hit your session limit · resets 3am (UTC)")
sys.exit(1)
"""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_dummy_root(Path(tmp_s), ECHO_MINUS_TEN_SOLVER)
            adapter = tmp / "adapter.py"
            adapter.write_text(adapter_body, encoding="utf-8")
            db = ExperimentDB.default(tmp)
            result = Autopilot(tmp, "dummy", db).run(
                budget_sec=None,
                max_trials=1,
                agent_count=1,
                seeds="0-2",
                adapter_command=f"{sys.executable} {adapter} {{cwd}}",
                roles=["AnnealingBuilder"],
                repair_attempts=3,
                generations=4,
            )
            self.assertEqual(result.status, "adapter_fatal")
            self.assertEqual(result.generations_run, 1)
            self.assertEqual(result.merged_count, 0)
            trial = db.conn.execute(
                "select * from autopilot_trials where session_id = ?", (result.session_id,)
            ).fetchone()
            summary = json.loads(trial["summary_json"])
            self.assertEqual(trial["decision"], "error")
            self.assertTrue(summary["fatal"])
            self.assertIn("rate_limit", summary["error"])
            # No repair retry happened despite repair_attempts=3.
            self.assertNotIn("repair", summary)
            self.assertFalse(
                (result.prompt_dir / "g1_candidate_1_AnnealingBuilder_repair1.md").exists()
            )
            session = db.conn.execute(
                "select status from autopilot_sessions where id = ?", (result.session_id,)
            ).fetchone()
            self.assertEqual(session["status"], "adapter_fatal")
            fatal_message = db.conn.execute(
                "select content from agent_messages where agent_name='Autopilot' and role='fatal'"
            ).fetchone()
            self.assertIsNotNone(fatal_message)


class ElapsedRemeasureTest(unittest.TestCase):
    def test_noisy_elapsed_is_remeasured_and_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_dummy_root(Path(tmp_s), ECHO_MINUS_TEN_SOLVER)
            db = ExperimentDB.default(tmp)
            base_run = Evaluator(tmp, "dummy", db).evaluate([0, 1, 2], tag="base")
            (tmp / "solver" / "main.cpp").write_text(ECHO_SOLVER, encoding="utf-8")
            evaluator = Evaluator(tmp, "dummy", db)
            cand_run = evaluator.evaluate([0, 1, 2], tag="cand")
            # Simulate contention noise: one seed's recorded elapsed exceeds
            # the 2.0s gate even though the solver is instant.
            db.conn.execute(
                "update cases set elapsed_sec = 5.0 where run_id = ? and seed = 1",
                (cand_run,),
            )
            db.conn.commit()
            policy = AcceptancePolicy.from_config(evaluator.config)
            comparison = compare_runs(db, base_run, cand_run)
            verdict = evaluate_acceptance(comparison, policy)
            self.assertEqual(verdict["decision"], "rejected")
            self.assertTrue(
                all(str(r).startswith("max elapsed") for r in verdict["reasons"])
            )
            info = Autopilot(tmp, "dummy", db)._remeasure_elapsed(
                evaluator, db, cand_run, policy
            )
            self.assertTrue(info["passed"])
            self.assertEqual(list(info["seeds"]), ["1"])
            comparison = compare_runs(db, base_run, cand_run)
            verdict = evaluate_acceptance(comparison, policy)
            self.assertEqual(verdict["decision"], "accepted")

    def test_remeasure_skips_when_no_seed_is_over_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_dummy_root(Path(tmp_s), ECHO_SOLVER)
            db = ExperimentDB.default(tmp)
            evaluator = Evaluator(tmp, "dummy", db)
            run_id = evaluator.evaluate([0, 1], tag="fast")
            policy = AcceptancePolicy.from_config(evaluator.config)
            self.assertIsNone(
                Autopilot(tmp, "dummy", db)._remeasure_elapsed(
                    evaluator, db, run_id, policy
                )
            )


class AdapterTimeoutSalvageTest(unittest.TestCase):
    def _run(self, tmp: Path, adapter_body: str):
        tmp = _make_dummy_root(tmp, ECHO_MINUS_TEN_SOLVER)
        adapter = tmp / "adapter.py"
        adapter.write_text(adapter_body, encoding="utf-8")
        db = ExperimentDB.default(tmp)
        with mock.patch.dict(os.environ, {"AHC_ADAPTER_TIMEOUT_SEC": "3"}):
            result = Autopilot(tmp, "dummy", db).run(
                budget_sec=None,
                max_trials=1,
                agent_count=1,
                seeds="0-2",
                adapter_command=f"{sys.executable} {adapter} {{cwd}}",
                roles=["AnnealingBuilder"],
            )
        trial = db.conn.execute(
            "select * from autopilot_trials where session_id = ?", (result.session_id,)
        ).fetchone()
        return db, result, trial, json.loads(trial["summary_json"])

    def test_timed_out_agent_work_is_salvaged_and_evaluated(self) -> None:
        adapter_body = f"""import pathlib, sys, time
p = pathlib.Path(sys.argv[1]) / "solver" / "main.cpp"
p.write_text({ECHO_SOLVER!r}, encoding="utf-8")
time.sleep(30)
"""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            db, result, trial, summary = self._run(tmp, adapter_body)
            self.assertEqual(trial["decision"], "accepted")
            self.assertTrue(summary["adapter_timeout"])
            self.assertEqual(result.merged_count, 1)
            self.assertEqual(
                (tmp / "solver" / "main.cpp").read_text(encoding="utf-8"), ECHO_SOLVER
            )

    def test_timed_out_agent_with_no_change_stays_an_error(self) -> None:
        adapter_body = """import time
time.sleep(30)
"""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            db, result, trial, summary = self._run(tmp, adapter_body)
            self.assertEqual(trial["decision"], "error")
            self.assertEqual(trial["status"], "failed")
            self.assertIn("adapter failed", summary["error"])


class NoveltyUnitTest(unittest.TestCase):
    def test_normalize_cpp_strips_comments_and_whitespace(self) -> None:
        self.assertEqual(
            normalize_cpp(ECHO_MINUS_TEN_COMMENTED_SOLVER),
            normalize_cpp(ECHO_MINUS_TEN_SOLVER),
        )

    def test_normalize_cpp_preserves_string_literals(self) -> None:
        code = 'const char *url = "http://example.com"; // real comment\n'
        normalized = normalize_cpp(code)
        self.assertIn("http://example.com", normalized)
        self.assertNotIn("real comment", normalized)

    def test_normalize_cpp_block_comment_separates_tokens(self) -> None:
        self.assertNotEqual(normalize_cpp("int a/*x*/b;"), normalize_cpp("int ab;"))

    def test_normalized_fingerprint_matches_cosmetic_variant_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            for name, source in [
                ("plain", ECHO_MINUS_TEN_SOLVER),
                ("commented", ECHO_MINUS_TEN_COMMENTED_SOLVER),
                ("different", ECHO_SOLVER),
            ]:
                (tmp / name).mkdir()
                (tmp / name / "main.cpp").write_text(source, encoding="utf-8")
            plain = normalized_source_fingerprint(tmp / "plain")
            self.assertEqual(plain, normalized_source_fingerprint(tmp / "commented"))
            self.assertNotEqual(plain, normalized_source_fingerprint(tmp / "different"))


class AutopilotNoveltyTest(unittest.TestCase):
    def _run(self, tmp: Path, adapter_body: str, **kwargs):
        tmp = _make_dummy_root(tmp, ECHO_MINUS_TEN_SOLVER)
        adapter = tmp / "adapter.py"
        adapter.write_text(adapter_body, encoding="utf-8")
        db = ExperimentDB.default(tmp)
        result = Autopilot(tmp, "dummy", db).run(
            budget_sec=None,
            max_trials=1,
            agent_count=1,
            seeds="0-2",
            adapter_command=f"{sys.executable} {adapter} {{cwd}}",
            roles=["AnnealingBuilder"],
            **kwargs,
        )
        trial = db.conn.execute(
            "select * from autopilot_trials where session_id = ?", (result.session_id,)
        ).fetchone()
        return db, result, trial, json.loads(trial["summary_json"])

    COSMETIC_ADAPTER = f"""import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "solver" / "main.cpp"
p.write_text({ECHO_MINUS_TEN_COMMENTED_SOLVER!r}, encoding="utf-8")
"""

    def test_cosmetic_duplicate_is_skipped_before_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            db, result, trial, summary = self._run(tmp, self.COSMETIC_ADAPTER)
            self.assertEqual(trial["decision"], "duplicate")
            self.assertEqual(trial["status"], "skipped")
            self.assertIsNone(trial["new_run_id"])
            self.assertEqual(summary["duplicate"]["match"], "normalized")
            self.assertEqual(summary["duplicate"]["run_id"], result.baseline_run_id)
            candidate_runs = db.conn.execute(
                "select count(*) as n from runs where run_type = 'candidate'"
            ).fetchone()["n"]
            self.assertEqual(candidate_runs, 0)

    def test_duplicate_feedback_leads_to_novel_candidate(self) -> None:
        adapter_body = f"""import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "solver" / "main.cpp"
if "// cosmetic" in p.read_text(encoding="utf-8"):
    p.write_text({ECHO_SOLVER!r}, encoding="utf-8")
else:
    p.write_text({ECHO_MINUS_TEN_COMMENTED_SOLVER!r}, encoding="utf-8")
"""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            db, result, trial, summary = self._run(tmp, adapter_body, repair_attempts=1)
            self.assertEqual(trial["decision"], "accepted")
            self.assertEqual(result.merged_count, 1)
            attempts = summary["repair"]["attempts"]
            self.assertEqual([a["decision"] for a in attempts], ["duplicate", "accepted"])
            repair_prompt = (
                result.prompt_dir / "g1_candidate_1_AnnealingBuilder_repair1.md"
            ).read_text(encoding="utf-8")
            self.assertIn("not novel", repair_prompt)
            self.assertIn("comments/whitespace", repair_prompt)

    def test_novelty_filter_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            db, result, trial, summary = self._run(
                tmp, self.COSMETIC_ADAPTER, novelty_filter=False
            )
            self.assertNotEqual(trial["decision"], "duplicate")
            self.assertIsNotNone(trial["new_run_id"])


class ArchiveUnitTest(unittest.TestCase):
    def _add_run(
        self,
        db: ExperimentDB,
        *,
        tag: str,
        source_hash: str,
        scores: list[float],
        run_type: str = "candidate",
        case_statuses: dict[int, str] | None = None,
        parent: int | None = None,
        status: str = "done",
    ) -> int:
        run_id = db.create_run(
            problem="dummy",
            tag=tag,
            run_type=run_type,
            score_direction="max",
            solver_path=Path("solver"),
            build_command="true",
            source_hash=source_hash,
            parent_run_id=parent,
        )
        for seed, score in enumerate(scores):
            db.insert_case(
                run_id=run_id,
                seed=seed,
                score=score,
                elapsed_sec=0.01,
                status=(case_statuses or {}).get(seed, "ok"),
                input_path=Path("i"),
                output_path=Path("o"),
                stderr_path=Path("e"),
            )
        db.finish_run(run_id, status=status)
        return run_id

    def test_build_archive_filters_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            db = ExperimentDB(Path(tmp_s) / "ahc.sqlite3")
            baseline = self._add_run(
                db, tag="autopilot_s1_baseline", run_type="baseline",
                source_hash="hash_a", scores=[10.0, 10.0],
            )
            best = self._add_run(
                db, tag="autopilot_s1_c1", source_hash="hash_b",
                scores=[20.0, 20.0], parent=baseline,
            )
            self._add_run(  # same source again: deduped, earliest kept
                db, tag="autopilot_s1_c2", source_hash="hash_b", scores=[20.0, 20.0],
            )
            self._add_run(  # pruned run: excluded
                db, tag="autopilot_s1_c3", source_hash="hash_c",
                scores=[30.0, 30.0], status="pruned",
            )
            self._add_run(  # failing case: excluded
                db, tag="autopilot_s1_c4", source_hash="hash_d",
                scores=[40.0, 40.0], case_statuses={1: "runtime_error"},
            )
            self._add_run(  # validation run type: excluded
                db, tag="autopilot_s1_v1", run_type="validation",
                source_hash="hash_e", scores=[50.0, 50.0],
            )
            self._add_run(  # other session: excluded
                db, tag="autopilot_s2_c1", source_hash="hash_f", scores=[60.0, 60.0],
            )
            archive = build_archive(db, "dummy", "autopilot_s1_")
            self.assertEqual(
                [(e.run_id, e.source_hash) for e in archive],
                [(baseline, "hash_a"), (best, "hash_b")],
            )
            by_id = {e.run_id: e for e in archive}
            self.assertEqual(by_id[best].fitness, 20.0)
            self.assertEqual(by_id[baseline].children, 1)
            self.assertEqual(by_id[best].children, 0)
            db.close()

    def test_sample_parents_prefers_fit_and_novel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            db = ExperimentDB(Path(tmp_s) / "ahc.sqlite3")
            weak = self._add_run(
                db, tag="autopilot_s1_baseline", run_type="baseline",
                source_hash="hash_a", scores=[10.0],
            )
            strong = self._add_run(
                db, tag="autopilot_s1_c1", source_hash="hash_b", scores=[20.0],
            )
            for i in range(4):  # the weak parent is already well explored
                self._add_run(
                    db, tag=f"autopilot_s1_child{i}", source_hash=f"hash_child{i}",
                    scores=[5.0], parent=weak, status="failed",
                )
            archive = build_archive(db, "dummy", "autopilot_s1_")
            rng = random.Random(42)
            picks = [sample_parents(archive, 1, rng=rng)[0].run_id for _ in range(300)]
            self.assertGreater(picks.count(strong), picks.count(weak) * 2)
            db.close()

    def test_sample_parents_distinct_while_archive_allows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            db = ExperimentDB(Path(tmp_s) / "ahc.sqlite3")
            for i in range(3):
                self._add_run(
                    db, tag=f"autopilot_s1_c{i}", source_hash=f"hash_{i}",
                    scores=[float(i)],
                )
            archive = build_archive(db, "dummy", "autopilot_s1_")
            picked = sample_parents(archive, 3, rng=random.Random(1))
            self.assertEqual(len({e.run_id for e in picked}), 3)
            picked_more = sample_parents(archive, 5, rng=random.Random(1))
            self.assertEqual(len(picked_more), 5)
            db.close()


class AutopilotArchiveTest(unittest.TestCase):
    def test_first_generation_archive_parent_is_baseline(self) -> None:
        adapter_body = f"""import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "solver" / "main.cpp"
p.write_text({ECHO_SOLVER!r}, encoding="utf-8")
"""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_dummy_root(Path(tmp_s), ECHO_MINUS_TEN_SOLVER)
            adapter = tmp / "adapter.py"
            adapter.write_text(adapter_body, encoding="utf-8")
            db = ExperimentDB.default(tmp)
            result = Autopilot(tmp, "dummy", db).run(
                budget_sec=None,
                max_trials=1,
                agent_count=1,
                seeds="0-2",
                adapter_command=f"{sys.executable} {adapter} {{cwd}}",
                roles=["AnnealingBuilder"],
                archive_parents=True,
                archive_rng=random.Random(0),
            )
            trial = db.conn.execute(
                "select * from autopilot_trials where session_id = ?", (result.session_id,)
            ).fetchone()
            summary = json.loads(trial["summary_json"])
            self.assertEqual(trial["decision"], "accepted")
            self.assertEqual(
                summary["archive_parent"]["run_id"], result.baseline_run_id
            )
            prompt = (result.prompt_dir / "g1_candidate_1_AnnealingBuilder.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("archive parent run", prompt)

    def test_archive_mode_branches_from_sampled_parent(self) -> None:
        # The adapter only produces the winning candidate when its workspace
        # starts from the seeded archive parent (echo-5), not the mainline.
        adapter_body = f"""import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "solver" / "main.cpp"
if "target - 5" in p.read_text(encoding="utf-8"):
    p.write_text({ECHO_SOLVER!r}, encoding="utf-8")
else:
    p.write_text({ECHO_MINUS_TWENTY_SOLVER!r}, encoding="utf-8")
"""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = _make_dummy_root(Path(tmp_s), ECHO_MINUS_TEN_SOLVER)
            adapter = tmp / "adapter.py"
            adapter.write_text(adapter_body, encoding="utf-8")
            db = ExperimentDB.default(tmp)
            store = store_dir_for(db)
            with tempfile.TemporaryDirectory() as seed_s:
                seed_root = Path(seed_s)
                (seed_root / "solver").mkdir()
                (seed_root / "solver" / "main.cpp").write_text(
                    ECHO_MINUS_FIVE_SOLVER, encoding="utf-8"
                )
                seeded_hash = snapshot_solver_source(seed_root, store)
            seeded_run = db.create_run(
                problem="dummy",
                tag="autopilot_s1_seeded",
                run_type="candidate",
                score_direction="max",
                solver_path=Path("solver"),
                build_command="true",
                source_hash=seeded_hash,
            )
            for seed in range(3):
                db.insert_case(
                    run_id=seeded_run,
                    seed=seed,
                    score=999_995.0,
                    elapsed_sec=0.01,
                    status="ok",
                    input_path=Path("i"),
                    output_path=Path("o"),
                    stderr_path=Path("e"),
                )
            db.finish_run(seeded_run, status="done")

            def pick_seeded(archive, count, rng=None):
                return [next(e for e in archive if e.run_id == seeded_run)] * count

            with mock.patch("ahc_lab.agents.sample_parents", pick_seeded):
                result = Autopilot(tmp, "dummy", db).run(
                    budget_sec=None,
                    max_trials=1,
                    agent_count=1,
                    seeds="0-2",
                    adapter_command=f"{sys.executable} {adapter} {{cwd}}",
                    roles=["AnnealingBuilder"],
                    archive_parents=True,
                )
            trial = db.conn.execute(
                "select * from autopilot_trials where session_id = ?", (result.session_id,)
            ).fetchone()
            summary = json.loads(trial["summary_json"])
            self.assertEqual(trial["decision"], "accepted")
            self.assertEqual(result.merged_count, 1)
            self.assertEqual(
                (tmp / "solver" / "main.cpp").read_text(encoding="utf-8"), ECHO_SOLVER
            )
            self.assertEqual(summary["archive_parent"]["run_id"], seeded_run)
            candidate_run = db.get_run(trial["new_run_id"])
            self.assertEqual(candidate_run["parent_run_id"], seeded_run)
            prompt = (result.prompt_dir / "g1_candidate_1_AnnealingBuilder.md").read_text(
                encoding="utf-8"
            )
            self.assertIn(f"archive parent run {seeded_run}", prompt)


class DynamicRolesTest(unittest.TestCase):
    def test_parse_roles_file_validates_and_sanitizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            path = Path(tmp_s) / "roles.json"
            path.write_text(
                json.dumps(
                    [
                        {"role": "Beam Search!", "focus": "widen the beam"},
                        {"role": "BeamSearch", "focus": "duplicate after sanitize"},
                        {"role": "NoFocus"},
                        {"role": "", "focus": "no role name"},
                        "not a dict",
                        {"role": "Tuner", "focus": "tune params", "responsibility": "owns params"},
                        {"role": "OneTooMany", "focus": "over the limit"},
                    ]
                ),
                encoding="utf-8",
            )
            specs = parse_roles_file(path, limit=2)
            self.assertEqual(
                specs,
                [
                    {"role": "BeamSearch", "focus": "widen the beam", "responsibility": ""},
                    {"role": "Tuner", "focus": "tune params", "responsibility": "owns params"},
                ],
            )
            path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_roles_file(path, limit=2)
            path.write_text(json.dumps([{"role": "X"}]), encoding="utf-8")
            with self.assertRaises(ValueError):
                parse_roles_file(path, limit=2)

    def _run(self, tmp: Path, adapter_body: str, **kwargs):
        tmp = _make_dummy_root(tmp, ECHO_MINUS_TEN_SOLVER)
        adapter = tmp / "adapter.py"
        adapter.write_text(adapter_body, encoding="utf-8")
        db = ExperimentDB.default(tmp)
        result = Autopilot(tmp, "dummy", db).run(
            budget_sec=None,
            max_trials=1,
            agent_count=1,
            seeds="0-2",
            adapter_command=f"{sys.executable} {adapter} {{cwd}} {{prompt}}",
            dynamic_roles=True,
            **kwargs,
        )
        trial = db.conn.execute(
            "select * from autopilot_trials where session_id = ?", (result.session_id,)
        ).fetchone()
        return db, result, trial

    def test_dynamic_roles_generates_trials_from_orchestrator(self) -> None:
        adapter_body = f"""import json, pathlib, sys
cwd = pathlib.Path(sys.argv[1])
prompt = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
if "roles.json" in prompt:
    (cwd / "roles.json").write_text(json.dumps([
        {{"role": "Offset Tuner!", "focus": "tune the output offset constant",
          "responsibility": "owns the output offset constant"}}
    ]), encoding="utf-8")
else:
    (cwd / "solver" / "main.cpp").write_text({ECHO_SOLVER!r}, encoding="utf-8")
"""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            db, result, trial = self._run(tmp, adapter_body, roles=None)
            self.assertEqual(trial["role"], "OffsetTuner")
            self.assertEqual(trial["decision"], "accepted")
            self.assertEqual(result.selected_agents, ["OffsetTuner"])
            self.assertEqual(result.merged_count, 1)
            prompt = (result.prompt_dir / "g1_candidate_1_OffsetTuner.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("tune the output offset constant", prompt)
            self.assertIn("owns the output offset constant", prompt)
            roles_message = db.conn.execute(
                "select content from agent_messages where agent_name = 'Orchestrator'"
                " and role = 'roles'"
            ).fetchone()
            self.assertIsNotNone(roles_message)
            self.assertIn("OffsetTuner", roles_message["content"])

    def test_dynamic_roles_falls_back_on_invalid_output(self) -> None:
        adapter_body = f"""import json, pathlib, sys
cwd = pathlib.Path(sys.argv[1])
prompt = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
if "roles.json" in prompt:
    (cwd / "roles.json").write_text(json.dumps({{"not": "a list"}}), encoding="utf-8")
else:
    (cwd / "solver" / "main.cpp").write_text({ECHO_SOLVER!r}, encoding="utf-8")
"""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            db, result, trial = self._run(tmp, adapter_body, roles=None)
            self.assertIn(trial["role"], SPECIALISTS)  # static catalog fallback
            self.assertEqual(trial["decision"], "accepted")
            error_message = db.conn.execute(
                "select content from agent_messages where agent_name = 'Orchestrator'"
                " and role = 'error'"
            ).fetchone()
            self.assertIsNotNone(error_message)
            self.assertIn("JSON array", error_message["content"])

    def test_explicit_roles_bypass_orchestrator(self) -> None:
        adapter_body = f"""import pathlib, sys
cwd = pathlib.Path(sys.argv[1])
prompt = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
assert "roles.json" not in prompt, "orchestrator must not run when --roles is given"
(cwd / "solver" / "main.cpp").write_text({ECHO_SOLVER!r}, encoding="utf-8")
"""
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            db, result, trial = self._run(tmp, adapter_body, roles=["StateDesignBuilder"])
            self.assertEqual(trial["role"], "StateDesignBuilder")
            self.assertEqual(trial["decision"], "accepted")
            self.assertFalse((result.prompt_dir.parent / "orchestrator").exists())


class AcceptanceGateTest(unittest.TestCase):
    def _run_with_cases(self, db: ExperimentDB, cases: list[dict]) -> int:
        run_id = db.create_run(
            problem="dummy",
            tag="gate",
            run_type="manual",
            score_direction="max",
            solver_path=Path("solver"),
            build_command="true",
        )
        for case in cases:
            db.insert_case(
                run_id=run_id,
                seed=case["seed"],
                score=case.get("score"),
                elapsed_sec=case.get("elapsed", 1.0),
                status=case.get("status", "ok"),
                input_path=Path("input.txt"),
                output_path=Path("output.txt"),
                stderr_path=Path("stderr.txt"),
            )
        db.finish_run(run_id, "done")
        return run_id

    def _compare(self, base_cases: list[dict], new_cases: list[dict]) -> dict:
        with tempfile.TemporaryDirectory() as tmp_s:
            db = ExperimentDB.default(Path(tmp_s))
            base = self._run_with_cases(db, base_cases)
            new = self._run_with_cases(db, new_cases)
            return compare_runs(db, base, new)

    def test_candidate_failure_counts_and_rejects(self) -> None:
        comparison = self._compare(
            [
                {"seed": 0, "score": 100},
                {"seed": 1, "score": 100},
                {"seed": 2, "score": 100},
            ],
            [
                {"seed": 0, "score": 200},
                {"seed": 1, "score": 100},
                {"seed": 2, "status": "runtime_error"},
            ],
        )
        self.assertEqual(comparison["candidate_failure_count"], 1)
        self.assertEqual(comparison["candidate_failures"][0]["seed"], 2)
        self.assertEqual(comparison["common_seed_count"], 2)
        verdict = evaluate_acceptance(comparison, AcceptancePolicy())
        self.assertEqual(verdict["decision"], "rejected")
        self.assertTrue(any("fails or misses" in reason for reason in verdict["reasons"]))

    def test_missing_seed_counts_as_failure(self) -> None:
        comparison = self._compare(
            [{"seed": 0, "score": 100}, {"seed": 1, "score": 100}],
            [{"seed": 0, "score": 300}],
        )
        self.assertEqual(comparison["candidate_failure_count"], 1)
        self.assertEqual(comparison["candidate_failures"][0]["status"], "missing")
        verdict = evaluate_acceptance(comparison, AcceptancePolicy())
        self.assertEqual(verdict["decision"], "rejected")

    def test_score_neutral_is_neutral_not_rejected(self) -> None:
        comparison = self._compare(
            [{"seed": 0, "score": 100}, {"seed": 1, "score": 100}],
            [{"seed": 0, "score": 100}, {"seed": 1, "score": 100}],
        )
        verdict = evaluate_acceptance(comparison, AcceptancePolicy())
        self.assertEqual(verdict["decision"], "neutral")

    def test_score_neutral_but_faster_is_accepted(self) -> None:
        comparison = self._compare(
            [{"seed": 0, "score": 100, "elapsed": 1.0}, {"seed": 1, "score": 100, "elapsed": 1.0}],
            [{"seed": 0, "score": 100, "elapsed": 0.5}, {"seed": 1, "score": 100, "elapsed": 0.5}],
        )
        verdict = evaluate_acceptance(comparison, AcceptancePolicy())
        self.assertEqual(verdict["decision"], "accepted")
        self.assertTrue(any("elapsed improves" in reason for reason in verdict["reasons"]))

    def test_worsening_budget(self) -> None:
        comparison = self._compare(
            [
                {"seed": 0, "score": 100},
                {"seed": 1, "score": 100},
                {"seed": 2, "score": 200},
            ],
            [
                {"seed": 0, "score": 400},
                {"seed": 1, "score": 100},
                {"seed": 2, "score": 160},
            ],
        )
        self.assertEqual(comparison["losses"], 1)
        strict = evaluate_acceptance(comparison, AcceptancePolicy())
        self.assertEqual(strict["decision"], "rejected")
        budget = AcceptancePolicy(max_worsening_seeds=1, max_worsening_delta=100.0)
        self.assertEqual(evaluate_acceptance(comparison, budget)["decision"], "accepted")
        tight = AcceptancePolicy(max_worsening_seeds=1, max_worsening_delta=10.0)
        self.assertEqual(evaluate_acceptance(comparison, tight)["decision"], "rejected")

    def test_improve_confidence_reported_and_gated(self) -> None:
        all_wins = self._compare(
            [{"seed": s, "score": 100} for s in range(3)],
            [{"seed": s, "score": 110} for s in range(3)],
        )
        self.assertEqual(all_wins["improve_confidence"], 1.0)
        confident = AcceptancePolicy(min_improve_confidence=0.9)
        self.assertEqual(evaluate_acceptance(all_wins, confident)["decision"], "accepted")
        mixed = self._compare(
            [{"seed": s, "score": 100} for s in range(4)],
            [
                {"seed": 0, "score": 110},
                {"seed": 1, "score": 110},
                {"seed": 2, "score": 91},
                {"seed": 3, "score": 91},
            ],
        )
        lenient = AcceptancePolicy(max_worsening_seeds=2, max_worsening_delta=100.0)
        self.assertEqual(evaluate_acceptance(mixed, lenient)["decision"], "accepted")
        strict = AcceptancePolicy(
            max_worsening_seeds=2, max_worsening_delta=100.0, min_improve_confidence=0.9
        )
        verdict = evaluate_acceptance(mixed, strict)
        self.assertEqual(verdict["decision"], "rejected")
        self.assertTrue(any("confidence" in reason for reason in verdict["reasons"]))

    def test_elapsed_over_limit_rejected(self) -> None:
        comparison = self._compare(
            [{"seed": 0, "score": 100, "elapsed": 1.0}],
            [{"seed": 0, "score": 500, "elapsed": 2.5}],
        )
        self.assertEqual(comparison["new_max_elapsed_sec"], 2.5)
        verdict = evaluate_acceptance(comparison, AcceptancePolicy(max_elapsed_sec=2.0))
        self.assertEqual(verdict["decision"], "rejected")
        self.assertTrue(any("elapsed" in reason for reason in verdict["reasons"]))

    def test_fixing_failed_seed_is_accepted(self) -> None:
        comparison = self._compare(
            [
                {"seed": 0, "score": 100},
                {"seed": 1, "status": "timeout"},
            ],
            [
                {"seed": 0, "score": 100},
                {"seed": 1, "score": 50},
            ],
        )
        self.assertEqual(comparison["fixed_seed_count"], 1)
        verdict = evaluate_acceptance(comparison, AcceptancePolicy())
        self.assertEqual(verdict["decision"], "accepted")
        self.assertTrue(any("previously failing" in reason for reason in verdict["reasons"]))

    def test_policy_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            problem_dir = tmp / "problems" / "gatecase"
            problem_dir.mkdir(parents=True)
            (problem_dir / "config.yaml").write_text(
                """
name: gatecase
score_direction: max
time_limit_sec: 3
build_command: "true"
run_command: "true"
acceptance:
  max_worsening_seeds: 2
  max_worsening_delta: 1500
  max_elapsed_sec: 2.8
  neutral_speedup_ratio: 0.1
""",
                encoding="utf-8",
            )
            policy = AcceptancePolicy.from_config(load_problem_config(tmp, "gatecase"))
            self.assertEqual(policy.max_worsening_seeds, 2)
            self.assertEqual(policy.max_worsening_delta, 1500.0)
            self.assertEqual(policy.max_elapsed_sec, 2.8)
            self.assertEqual(policy.neutral_speedup_ratio, 0.1)

    def test_policy_defaults_use_time_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            problem_dir = tmp / "problems" / "gatecase"
            problem_dir.mkdir(parents=True)
            (problem_dir / "config.yaml").write_text(
                """
name: gatecase
score_direction: max
time_limit_sec: 3
build_command: "true"
run_command: "true"
""",
                encoding="utf-8",
            )
            policy = AcceptancePolicy.from_config(load_problem_config(tmp, "gatecase"))
            self.assertEqual(policy.max_worsening_seeds, 0)
            self.assertEqual(policy.max_worsening_delta, 0.0)
            self.assertEqual(policy.max_elapsed_sec, 3.0)


class KnowledgeTest(unittest.TestCase):
    def test_search_knowledge(self) -> None:
        hits = search_knowledge(ROOT, "annealing rollback", limit=3)
        self.assertTrue(hits)


class AutopilotTest(unittest.TestCase):
    def test_select_specialists_includes_cut_builder_for_bad_seeds(self) -> None:
        selected = select_specialists(
            {"bad_seeds": [{"seed": 1, "score": 10}], "mean_elapsed_sec": 0.2},
            agent_count=3,
        )
        self.assertIn("CutBuilder", selected)

    def test_parse_roles_validates_names(self) -> None:
        self.assertEqual(_parse_roles("CutBuilder,ParameterTuner"), ["CutBuilder", "ParameterTuner"])
        with self.assertRaises(ValueError):
            _parse_roles("CutBilder")

    def test_build_agent_prompt_includes_focus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            shutil.copytree(ROOT / "problems", tmp / "problems")
            db = ExperimentDB.default(tmp)
            run_id = db.create_run(
                problem="dummy",
                tag="prompt-test",
                run_type="manual",
                score_direction="max",
                solver_path=tmp / "solver" / "main.cpp",
                build_command="true",
            )
            db.finish_run(run_id, "done")
            prompt = build_agent_prompt(
                root=tmp,
                problem="dummy",
                agent_name="CutBuilder",
                run_id=run_id,
                analysis={"bad_seeds": []},
                knowledge_hits=[],
                focus="Add BFS distance-layer cut candidates.",
            )
        self.assertIn("CutBuilder", prompt)
        self.assertIn("Add BFS distance-layer cut candidates.", prompt)


class CliTest(unittest.TestCase):
    def test_mcp_list_tools(self) -> None:
        proc = subprocess.run(
            [sys.executable, "tools/ahc.py", "mcp", "--server", "analysis", "--list-tools"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.assertIn("analyze_run", proc.stdout)


if __name__ == "__main__":
    unittest.main()
