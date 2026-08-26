"""Adversarial fixtures for the fail-closed hybrid goal completion auditor."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "hybrid_audit",
    ROOT / "experiments/scripts/audit_hybrid_goal_completion.py",
)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class HybridGoalAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self.tmp.name)
        self.roots = {name: self.base / name for name in AUDIT.DEFAULTS}
        for root in self.roots.values():
            root.mkdir()
        self._cg_certificate()
        self._direct4(terminal=True)
        self._is_certificate()
        self._hashjoin("hashjoin_pro", "PRO")
        self._hashjoin("hashjoin_prh", "PRH")
        self._sssp(gated=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _hash(path: pathlib.Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _ledger(
        self, root: pathlib.Path, name: str, targets: list[pathlib.Path]
    ) -> None:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(
            "".join(f"{self._hash(p)}  {p}\n" for p in targets)
        )

    def _input(self, root: pathlib.Path) -> None:
        source = root / "immutable-input"
        source.write_text("input")
        self._ledger(root, "input_sha256.txt", [source])

    def _cg_certificate(self) -> None:
        root = self.roots["cg_certificate"]
        (root / "manifest.json").write_text("{}")
        cert = {
            "verdict": "PASS_NUMERICAL_MECHANISM_CORRECT",
            "raw_or_quantized_exact": False,
            "candidate": {
                "terminal": {
                    "result": "PASS",
                    "physical_spd_payload_bytes": "524288",
                    "performance_promotable": "0",
                }
            },
        }
        (root / "certificate.json").write_text(json.dumps(cert))
        (root / "gate.complete").write_text(
            "PASS_NUMERICAL_MECHANISM_CORRECT\n"
        )
        self._input(root)

    def _direct4(self, terminal: bool) -> None:
        root = self.roots["cg_direct4"]
        manifest = {
            "terminal": False,
            "certificate": {"verdict": "PASS_NUMERICAL_MECHANISM_CORRECT"},
            "geometry": {
                "tiles_per_core": 8,
                "physical_spd_payload_bytes": 524288,
            },
            "commands": {
                "restore": ["page_fed", "predicate_active_credits=16"]
            },
        }
        (root / "manifest.json").write_text(json.dumps(manifest))
        if not terminal:
            for name in (
                "result.json",
                "gate.complete",
                "certified_artifacts.sha256",
            ):
                (root / name).unlink(missing_ok=True)
            return
        result = {
            "terminal": True,
            "candidate_only": True,
            "certificate": {"verdict": "PASS_NUMERICAL_MECHANISM_CORRECT"},
            "p16_reorder_preserved": False,
            "q16_reorder_preserved": True,
            "performance": {"candidate": 1},
        }
        (root / "result.json").write_text(json.dumps(result))
        authorities = []
        for name in (
            "run/restore.log",
            "run/restore.log.exit",
            "run/stats.txt",
            "run/config.ini",
            "input/source_commit.before",
            "input/source_commit.after",
        ):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
            authorities.append(path)
        self._ledger(
            root,
            "certified_artifacts.sha256",
            [root / "manifest.json", *authorities],
        )
        (root / "gate.complete").write_text(
            "PASS_NUMERICAL_MECHANISM_CORRECT\n"
            f"result_sha256={self._hash(root / 'result.json')}\n"
            f"certified_artifacts_sha256={self._hash(root / 'certified_artifacts.sha256')}\n"
        )

    def _is_certificate(self) -> None:
        root = self.roots["is_certificate"]
        (root / "manifest.json").write_text("{}")
        (root / "certificate.json").write_text(
            json.dumps(
                {
                    "verdict": "PASS_FULL_IS_CORRECTNESS",
                    "official_nas_verification": True,
                    "performance_promoted": False,
                    "physical_spd_payload_bytes": 524288,
                    "staging_payload_bytes": 0,
                }
            )
        )
        (root / "gate.complete").write_text("PASS_FULL_IS_CORRECTNESS\n")
        self._input(root)

    def _hashjoin(self, root_name: str, kernel: str) -> None:
        root = self.roots[root_name]
        child = root / kernel
        child.mkdir()
        (root / "gate.complete").write_text(
            f"terminal=pass\nkernel_selector={kernel}\n"
        )
        (root / "manifest.txt").write_text(
            "candidate_only=1\nnative_rerun=0\nexpected_cardinality=2000000\n"
            "geometry=memory_channels:2,row_table_slices:32,indirect_units:4,logical_elements:16384,physical_elements:4096\n"
        )
        shifted = "not_applicable" if kernel == "PRO" else "tail_only"
        (child / "mechanism.status").write_text(
            f"kernel={kernel}\nfirst_pass_coverage=routed\nshifted_pass_coverage={shifted}\n"
        )
        (root / "results.tsv").write_text("result=2000000\n")
        self._ledger(
            root,
            "result_sha256.txt",
            [
                root / "gate.complete",
                root / "manifest.txt",
                root / "results.tsv",
            ],
        )

    def _sssp(self, gated: bool) -> None:
        root = self.roots["sssp"]
        (root / "candidate.manifest").write_text(
            "native_arms=0\nlogical_elements=16384\nphysical_tile_elements=4096\nactive_contexts=8\n"
        )
        (root / "external_reference.manifest").write_text(
            "oracle=SSSP_FINGERPRINT result=PASS\n"
        )
        (root / "run").mkdir(exist_ok=True)
        (root / "run/restore.log").write_text(
            "coherent fallback accounting closed\n"
        )
        if gated:
            (root / "gate.complete").write_text("PASS\n")
        else:
            (root / "gate.complete").unlink(missing_ok=True)
        for name in (
            "provenance/artifacts.before.sha256",
            "provenance/checkpoint.before.files.sha256",
            "provenance/checkpoint.before.identity.sha256",
        ):
            witness = root / (name.replace("/", "_") + ".witness")
            witness.write_text(name)
            self._ledger(root, name, [witness])

    def result(self) -> dict:
        return AUDIT.audit(self.roots)

    def test_complete_fixture_is_pass_and_writes_gate_last(self) -> None:
        result = self.result()
        self.assertEqual(result["status"], "PASS")
        out = self.base / "output"
        self.assertFalse((out / "gate.complete").exists())

    def test_missing_or_forged_gate_is_not_pass(self) -> None:
        self.roots["is_certificate"].joinpath("gate.complete").write_text(
            "PASS\n"
        )
        self.assertEqual(self.result()["status"], "INCOMPLETE")

    def test_stale_hash_is_not_pass(self) -> None:
        self.roots["hashjoin_pro"].joinpath("results.tsv").write_text(
            "result=7\n"
        )
        self.assertEqual(self.result()["status"], "INCOMPLETE")

    def test_correctness_only_is_not_performance(self) -> None:
        self.roots["is_certificate"].joinpath("certificate.json").write_text(
            json.dumps(
                {
                    "verdict": "PASS_FULL_IS_CORRECTNESS",
                    "official_nas_verification": True,
                    "performance_promoted": True,
                    "physical_spd_payload_bytes": 524288,
                    "staging_payload_bytes": 0,
                }
            )
        )
        self.assertEqual(
            AUDIT.audit_is(self.roots["is_certificate"])["status"], "failed"
        )

    def test_medium_cg_evidence_is_pending_not_full(self) -> None:
        self._direct4(terminal=False)
        self.assertEqual(
            AUDIT.audit_cg(
                self.roots["cg_certificate"], self.roots["cg_direct4"]
            )["status"],
            "pending",
        )

    def test_hidden_hardware_bytes_are_rejected(self) -> None:
        self.roots["sssp"].joinpath("candidate.manifest").write_text(
            "native_arms=0\nlogical_elements=16384\nphysical_tile_elements=4096\nactive_contexts=8\nhidden_payload_bytes=4\n"
        )
        self.assertEqual(
            AUDIT.audit_sssp(self.roots["sssp"])["status"], "failed"
        )

    def test_pending_process_marker_is_not_pass(self) -> None:
        self._sssp(gated=False)
        self.roots["sssp"].joinpath("RUNNING.status").write_text("running\n")
        self.assertEqual(
            AUDIT.audit_sssp(self.roots["sssp"])["status"], "pending"
        )

    def test_native_rerun_is_rejected(self) -> None:
        root = self.roots["hashjoin_pro"]
        root.joinpath("manifest.txt").write_text(
            root.joinpath("manifest.txt")
            .read_text()
            .replace("native_rerun=0", "native_rerun=1")
        )
        self.assertEqual(AUDIT.audit_hashjoin(root, "PRO")["status"], "failed")

    def test_partial_hashjoin_is_rejected(self) -> None:
        self.roots["hashjoin_prh"].joinpath("results.tsv").write_text(
            "result=1999999\n"
        )
        self._ledger(
            self.roots["hashjoin_prh"],
            "result_sha256.txt",
            [
                self.roots["hashjoin_prh"] / x
                for x in ("gate.complete", "manifest.txt", "results.tsv")
            ],
        )
        self.assertEqual(
            AUDIT.audit_hashjoin(self.roots["hashjoin_prh"], "PRH")["status"],
            "failed",
        )

    def test_premature_final_gate_is_removed_when_incomplete(self) -> None:
        self._direct4(terminal=False)
        out = self.base / "output"
        out.mkdir()
        (out / "gate.complete").write_text("PASS\n")
        result = self.result()
        self.assertEqual(result["status"], "INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
