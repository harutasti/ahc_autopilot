from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ahc_lab.agents import Autopilot, build_agent_prompt, select_specialists
from ahc_lab.analysis import analyze_run, compare_runs
from ahc_lab.cli import _init_problem, _parse_roles
from ahc_lab.config import load_problem_config
from ahc_lab.db import ExperimentDB
from ahc_lab.evaluator import Evaluator
from ahc_lab.knowledge import search_knowledge
from ahc_lab.policy import AcceptancePolicy, evaluate_acceptance
from ahc_lab.seeds import parse_seed_spec
from ahc_lab.snapshots import restore_solver_source, snapshot_solver_source, store_dir_for


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


ROOT = Path(__file__).resolve().parents[1]


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

    def _run_autopilot(self, tmp: Path, adapter: Path, *, merge_accepted: bool):
        db = ExperimentDB.default(tmp)
        result = Autopilot(tmp, "dummy", db).run(
            budget_sec=None,
            max_trials=1,
            agent_count=1,
            seeds="0-2",
            adapter_command=f"{sys.executable} {adapter} {{cwd}}",
            roles=["AnnealingBuilder"],
            merge_accepted=merge_accepted,
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
