"""Phase 0 tests: integrity, re-derivation, hand arithmetic, truth firewall, G01 seed.

    python -m unittest discover -s tests -t . -v
"""
from __future__ import annotations

import ast
import csv
import json
import math
import unittest

from evaluation import fixture_io as io
from evaluation import oracle
from evaluation.reference_cases import CASES

TOL = 1e-9


def assert_close(tc: unittest.TestCase, got, want, path: str = "$") -> None:
    if isinstance(want, float) or isinstance(got, float):
        tc.assertTrue(math.isclose(got, want, rel_tol=TOL, abs_tol=TOL), f"{path}: {got} != {want}")
    elif isinstance(want, dict):
        tc.assertEqual(set(got), set(want), f"{path}: keys differ")
        for k in want:
            assert_close(tc, got[k], want[k], f"{path}.{k}")
    elif isinstance(want, list):
        tc.assertEqual(len(got), len(want), f"{path}: length differs")
        for i, (g, w) in enumerate(zip(got, want)):
            assert_close(tc, g, w, f"{path}[{i}]")
    else:
        tc.assertEqual(got, want, path)


def frozen(case_id: str) -> dict:
    return json.loads((io.FIXTURE_DIR / f"{case_id}.json").read_text())


class Integrity(unittest.TestCase):
    def test_design_corpus_unchanged(self):
        self.assertTrue(io.verify_baseline_corpus()["ok"])

    def test_fixture_manifest_matches_disk(self):
        res = io.verify_manifest()
        self.assertTrue(res["ok"], res)

    def test_phase0_tables_are_well_formed(self):
        doc = io.PROJECT_ROOT / "docs" / "phase0"
        for name, n_rows, n_cols in (("rule_classification.csv", 48, 6), ("methods_preconditions.csv", 17, 12)):
            rows = list(csv.reader((doc / name).read_text().splitlines()))
            self.assertEqual(len(rows) - 1, n_rows, name)
            self.assertEqual({len(r) for r in rows}, {n_cols}, name)


class Rederivation(unittest.TestCase):
    """Frozen expectations must equal what the oracle derives from the stored inputs."""

    def test_every_frozen_case_rederives(self):
        for case in CASES:
            if not (io.FIXTURE_DIR / f"{case.case_id}.json").exists():
                continue  # pending cases are reported by the demo, not silently passed
            with self.subTest(case=case.case_id):
                fx, rows = io.load_fixture(case.case_id)
                derived = io.clean(case.derive(fx["input"], rows))
                assert_close(self, derived, {k: fx[k] for k in derived})


class HandArithmetic(unittest.TestCase):
    """Numbers worked out by hand, independently of oracle.py."""

    def test_01_drift_residual(self):
        e = frozen("01_ratio_identity")["expected"]
        self.assertEqual(e["main"]["verdict"], "VERIFIED_IDENTITY")
        self.assertAlmostEqual(e["drifted_variant"]["max_abs_residual"], 0.0475 - 36 / 800, places=12)

    def test_02_kitagawa_split(self):
        # w0=(.2,.8) w1=(.8,.2) r0=(.02,.06) r1=(.025,.065):
        # rate = .5*.005 + .5*.005 = .005 ; mix = .0225*.6 + .0625*(-.6) = -.024
        e = frozen("02_simpson_kitagawa")["expected"]["main"]
        self.assertAlmostEqual(e["rate_component"], 0.005, places=12)
        self.assertAlmostEqual(e["mix_component"], -0.024, places=12)
        self.assertAlmostEqual(e["total_change"], 0.033 - 0.052, places=12)

    def test_04_srm(self):
        e = frozen("04_valid_rct")["expected"]["srm_at_assignment_unit"]
        self.assertAlmostEqual(e["chi2"], (40 ** 2 + 40 ** 2) / 10000, places=12)

    def test_08_wealth_paths(self):
        # λ=.5: increment = .5 z − .125 ; correct uses z = .3, 1.1, .4, .9
        f = frozen("08_revised_bucket_eprocess")
        self.assertAlmostEqual(f["expected"]["log_wealth"], 0.025 + 0.425 + 0.075 + 0.325, places=12)
        self.assertFalse(f["expected"]["alarm"])
        # first-seen-root keeps z=5.0 for b3: .025+.425+2.375+.325
        self.assertAlmostEqual(f["v21_reference"]["first_seen_root_implementation"]["log_wealth"], 3.15, places=12)
        self.assertTrue(f["v21_reference"]["first_seen_root_implementation"]["alarm"])


class OracleSelfChecks(unittest.TestCase):
    def test_distributions_against_published_values(self):
        self.assertAlmostEqual(oracle.student_t_critical(18), 2.100922, places=5)  # t table
        self.assertAlmostEqual(oracle.student_t_two_sided_p(1.0, 1), 0.5, places=12)  # Cauchy
        self.assertAlmostEqual(oracle.chi2_1df_sf(3.841459), 0.05, places=6)
        self.assertAlmostEqual(oracle.normal_critical(0.95), 1.959964, places=6)


class TruthFirewall(unittest.TestCase):
    TRUTH_ONLY_KEYS = {"true_values", "icc", "complier_share", "never_taker_shift",
                       "realised_complier_share", "parallel_trends", "latent"}

    def test_fixtures_never_contain_truth(self):
        for path in io.FIXTURE_DIR.glob("*.json"):
            text = path.read_text()
            for key in self.TRUTH_ONLY_KEYS:
                self.assertNotIn(f'"{key}"', text, f"{path.name} leaks truth key {key}")

    def test_truth_is_only_in_truth_dir(self):
        for path in io.TRUTH_DIR.glob("*.json"):
            self.assertIn("true_values", json.loads(path.read_text()))
            self.assertFalse((io.FIXTURE_DIR / path.name).with_suffix(".truth.json").exists())


class CoreImportBoundary(unittest.TestCase):
    """G01 seed: the core may not import the emulator, the evaluator, scripts or vendor SDKs."""

    FORBIDDEN = {"emulator", "evaluation", "scripts", "tests",
                 "requests", "databricks", "statsig", "openai", "anthropic", "babbel_trace"}

    def test_core_imports(self):
        core = io.PROJECT_ROOT / "causal_core"
        for path in core.rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module]
                for name in names:
                    self.assertNotIn(name.split(".")[0], self.FORBIDDEN, f"{path}: imports {name}")


if __name__ == "__main__":
    unittest.main()
