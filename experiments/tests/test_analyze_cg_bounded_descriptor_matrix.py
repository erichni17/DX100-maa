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
    @staticmethod
    def fingerprint_line(**overrides):
        values = {
            "mode": "MAA",
            "elements": "150000",
            "x_raw": "x-raw",
            "z_raw": "z-raw",
            "x_q5": MODULE.EXPECTED_EXACT_FINGERPRINT["x_q5"],
            "x_q6": "x-q6",
            "z_q5": "diagnostic-z-q5",
            "z_q6": "z-q6",
            "x_sum": "-385.9469780116342",
            "x_norm_sq": "0.99999999995071809",
            "z_sum": "-1793.1550141340122",
            "z_norm_sq": "21.58640795548791",
            "rnorm": "0.0010975011901720496",
            "zeta": "109.99944232372989",
            "nonfinite_x": "0",
            "nonfinite_z": "0",
            "result": "PASS",
        }
        values.update(overrides)
        return "CG_FINGERPRINT " + " ".join(
            f"{key}={values[key]}" for key in MODULE.FINGERPRINT_KEYS
        )

    def test_parser_accepts_diagnostic_z_hash(self):
        values = MODULE.parse_fingerprint(self.fingerprint_line(), "native16")
        self.assertEqual(values["z_q5"], "diagnostic-z-q5")

    def test_parser_requires_canonical_x_hash(self):
        with self.assertRaisesRegex(ValueError, "exact semantic fingerprint"):
            MODULE.parse_fingerprint(
                self.fingerprint_line(x_q5="wrong-x-q5"), "bounded"
            )

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
            z_q5="legal-scheduling-drift",
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
        with self.assertRaisesRegex(ValueError, "exact semantic"):
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
