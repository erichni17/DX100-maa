#!/usr/bin/env python3

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/analyze_cg_bounded_descriptor_matrix.py"
SPEC = importlib.util.spec_from_file_location("cg_matrix_analysis", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class CGBoundedAnalysisTests(unittest.TestCase):
    def test_legal_reorder_drift_passes(self):
        reference = {
            "x_q5": "aa",
            "z_q5": "bb",
            "x_sum": "-385.94697800930589",
            "x_norm_sq": "0.99999999994898348",
            "z_sum": "-1793.1550141293555",
            "z_norm_sq": "21.586407955559853",
            "rnorm": "0.0010974966323353099",
            "zeta": "109.99944232372989",
        }
        candidate = dict(reference)
        candidate.update(
            x_sum="-385.9469780116342",
            z_sum="-1793.1550141340122",
            rnorm="0.0010975011901720496",
        )
        errors = MODULE.validate_fingerprint("bounded", candidate, reference)
        self.assertLess(errors["rnorm"], 1.0e-3)

    def test_coarse_element_mismatch_fails(self):
        reference = {
            "x_q5": "aa",
            "z_q5": "bb",
            **{key: "1" for key in MODULE.RELATIVE_TOLERANCES},
        }
        candidate = dict(reference, x_q5="cc")
        with self.assertRaisesRegex(ValueError, "coarse per-element"):
            MODULE.validate_fingerprint("bounded", candidate, reference)

    def test_excessive_numeric_drift_fails(self):
        reference = {
            "x_q5": "aa",
            "z_q5": "bb",
            **{key: "1" for key in MODULE.RELATIVE_TOLERANCES},
        }
        candidate = dict(reference, zeta="1.01")
        with self.assertRaisesRegex(ValueError, "numerical drift"):
            MODULE.validate_fingerprint("bounded", candidate, reference)


if __name__ == "__main__":
    unittest.main()
