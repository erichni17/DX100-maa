#!/usr/bin/env python3

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/classify_cg_page_fed_full_tolerant.py"
SPEC = importlib.util.spec_from_file_location("cg_full_tolerant", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
CLASSIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLASSIFIER)


class FullCgTolerantCertificateTest(unittest.TestCase):
    def valid_certificate(self):
        _, certificate = CLASSIFIER.build_documents({}, {}, {}, {}, [])
        return certificate

    def test_exact_contract_and_minimal_claims_are_pinned(self):
        self.assertEqual(
            CLASSIFIER.TOLERANCES,
            {
                "x_sum": "1e-8",
                "x_norm_sq": "1e-8",
                "z_sum": "1e-8",
                "z_norm_sq": "1e-8",
                "rnorm": "1e-3",
                "zeta": "1e-10",
            },
        )
        certificate = self.valid_certificate()
        self.assertEqual(
            certificate["verdict"], "PASS_NUMERICAL_MECHANISM_CORRECT"
        )
        self.assertIn(
            "neither FP-bit/quantized exact to native16 nor officially "
            "NAS-verified",
            certificate["minimal_correctness_claim"],
        )
        self.assertIn(
            "does not establish that reduction order caused the full",
            certificate["causality_claim"],
        )

    def test_loosened_or_incomplete_tolerances_are_rejected(self):
        loosened = dict(CLASSIFIER.TOLERANCES, rnorm="1e-2")
        with self.assertRaisesRegex(
            CLASSIFIER.CertificateError, "tolerances changed or were loosened"
        ):
            CLASSIFIER.require_declared_tolerances(loosened)
        incomplete = dict(CLASSIFIER.TOLERANCES)
        incomplete.pop("zeta")
        with self.assertRaises(CLASSIFIER.CertificateError):
            CLASSIFIER.require_declared_tolerances(incomplete)

    def test_missing_delivery_or_cache_accounting_is_rejected(self):
        terminal = {"index_publish_pages": "0"}
        stats = dict(CLASSIFIER.FULL_STATS_EXPECTED)
        stats.pop("IND_SoaJitValueHits")
        with self.assertRaisesRegex(
            CLASSIFIER.CertificateError, "delivery/cache"
        ):
            CLASSIFIER.validate_full_closure(stats, terminal)

        stats = dict(CLASSIFIER.FULL_STATS_EXPECTED)
        stats["IND_SoaJitValueMergedWaiters"] = 0
        with self.assertRaises(CLASSIFIER.CertificateError):
            CLASSIFIER.validate_full_closure(stats, terminal)

    def test_exactness_and_official_verification_overclaims_are_rejected(self):
        for field in ("raw_or_quantized_exact", "official_nas_verification"):
            certificate = self.valid_certificate()
            certificate[field] = True
            with self.subTest(field=field), self.assertRaisesRegex(
                CLASSIFIER.CertificateError, "forbidden claim overreach"
            ):
                CLASSIFIER.validate_certificate_claims(certificate)

    def test_full_causality_native_speedup_and_isoarea_overclaims_are_rejected(
        self,
    ):
        for field in (
            "full_reduction_cause_proven",
            "native_speedup",
            "iso_area",
        ):
            certificate = self.valid_certificate()
            certificate[field] = True
            with self.subTest(field=field), self.assertRaises(
                CLASSIFIER.CertificateError
            ):
                CLASSIFIER.validate_certificate_claims(certificate)

    def test_changed_root_is_rejected(self):
        roots = {
            "candidate": Path("/tmp/not-the-candidate"),
            "predecessor": CLASSIFIER.PREDECESSOR_ROOT,
            "native16": CLASSIFIER.NATIVE16_ROOT,
            "untreated": CLASSIFIER.UNTREATED_ROOT,
            "deterministic_1024": CLASSIFIER.DETERMINISTIC_1024_ROOT,
            "deterministic_4096": CLASSIFIER.DETERMINISTIC_4096_ROOT,
        }
        with self.assertRaisesRegex(
            CLASSIFIER.CertificateError, "not exactly pinned"
        ):
            CLASSIFIER.verify_pinned_root_arguments(roots)

    def test_changed_pinned_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "artifact"
            artifact.write_text("frozen\n")
            changed = {"fixture": (artifact, "0" * 64)}
            with mock.patch.object(CLASSIFIER, "PINNED_FILES", changed):
                with self.assertRaisesRegex(
                    CLASSIFIER.CertificateError, "pinned hash mismatch"
                ):
                    CLASSIFIER.verify_pinned_files({})

    def test_missing_deterministic_record_is_rejected(self):
        result = json.loads(
            (CLASSIFIER.DETERMINISTIC_4096_ROOT / "result.json").read_text()
        )
        result["reduction_evidence"]["physical"].pop()
        with self.assertRaisesRegex(CLASSIFIER.CertificateError, "11-record"):
            CLASSIFIER.validate_deterministic_result(result, 4096)

    def test_altered_arithmetic_is_rejected(self):
        certificate = self.valid_certificate()
        certificate["performance_arithmetic"]["predecessor_over_candidate"][
            "numerator"
        ] += 1
        with self.assertRaisesRegex(
            CLASSIFIER.CertificateError, "performance arithmetic changed"
        ):
            CLASSIFIER.validate_certificate_claims(certificate)

    def test_source_and_each_historical_root_are_forbidden_outputs(self):
        forbidden = (
            CLASSIFIER.SOURCE_ROOT / "certificate",
            CLASSIFIER.CANDIDATE_ROOT / "certificate",
            CLASSIFIER.PREDECESSOR_ROOT / "certificate",
            CLASSIFIER.NATIVE16_ROOT / "certificate",
            CLASSIFIER.UNTREATED_ROOT / "certificate",
            CLASSIFIER.DETERMINISTIC_1024_ROOT / "certificate",
            CLASSIFIER.DETERMINISTIC_4096_ROOT / "certificate",
        )
        for output in forbidden:
            with self.subTest(output=output), self.assertRaisesRegex(
                CLASSIFIER.CertificateError, "must be external"
            ):
                CLASSIFIER.validate_output_root(output)

    def test_validate_existing_is_read_only(self):
        manifest = {"schema": "fixture"}
        certificate = self.valid_certificate()
        snapshot = {"fixture:input": "a" * 64}
        manifest_text = CLASSIFIER.json_text(manifest)
        certificate_text = CLASSIFIER.json_text(certificate)
        inputs_text = CLASSIFIER.input_ledger_text(snapshot)
        gate_text = CLASSIFIER.expected_gate(
            manifest_text, certificate_text, inputs_text
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "certificate"
            output.mkdir()
            (output / "manifest.json").write_text(manifest_text)
            (output / "certificate.json").write_text(certificate_text)
            (output / "input_sha256.txt").write_text(inputs_text)
            (output / "gate.complete").write_text(gate_text)
            before = {
                path.name: (path.stat().st_mtime_ns, path.read_bytes())
                for path in output.iterdir()
            }
            with mock.patch.object(
                CLASSIFIER,
                "audit_inputs",
                return_value=(manifest, certificate, snapshot),
            ):
                validated = CLASSIFIER.validate_existing(output, {})
            after = {
                path.name: (path.stat().st_mtime_ns, path.read_bytes())
                for path in output.iterdir()
            }
            self.assertEqual(validated, certificate)
            self.assertEqual(before, after)

    def test_cli_defaults_all_six_pinned_inputs(self):
        args = SimpleNamespace(
            candidate_root=CLASSIFIER.CANDIDATE_ROOT,
            predecessor_root=CLASSIFIER.PREDECESSOR_ROOT,
            native16_root=CLASSIFIER.NATIVE16_ROOT,
            untreated_root=CLASSIFIER.UNTREATED_ROOT,
            deterministic_1024_root=CLASSIFIER.DETERMINISTIC_1024_ROOT,
            deterministic_4096_root=CLASSIFIER.DETERMINISTIC_4096_ROOT,
        )
        CLASSIFIER.verify_pinned_root_arguments(
            CLASSIFIER.roots_from_args(args)
        )


if __name__ == "__main__":
    unittest.main()
