from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ahc_lab.analysis import analyze_run, compare_runs
from ahc_lab.db import ExperimentDB
from ahc_lab.evaluator import Evaluator
from ahc_lab.knowledge import search_knowledge
from ahc_lab.seeds import parse_seed_spec


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
            db = ExperimentDB.default(tmp)
            run_id = Evaluator(tmp, "dummy", db).evaluate([0, 1, 2], tag="test")
            summary = analyze_run(db, run_id)
            self.assertEqual(summary["ok_count"], 3)
            self.assertEqual(summary["mean_score"], 1_000_000.0)
            run_id_2 = Evaluator(tmp, "dummy", db).evaluate([0, 1, 2], tag="test2")
            comparison = compare_runs(db, run_id, run_id_2)
            self.assertEqual(comparison["common_seed_count"], 3)
            self.assertEqual(comparison["losses"], 0)


class KnowledgeTest(unittest.TestCase):
    def test_search_knowledge(self) -> None:
        hits = search_knowledge(ROOT, "annealing rollback", limit=3)
        self.assertTrue(hits)


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

