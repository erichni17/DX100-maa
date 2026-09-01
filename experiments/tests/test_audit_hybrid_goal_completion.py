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
                "restore": [
                    "page_fed",
                    "predicate_active_credits=16",
                    "--maa_soa_jit_value_cache_enable",
                ]
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
            "selected_value_cache_enable": True,
            "hardware_accounting": {
                "enabled": True,
                "new_payload_bytes": 0,
                "new_control_bytes": 0,
                "new_ports": 0,
                "fixed_value_owner_lines_per_unit": 128,
                "active_value_owner_lines_per_unit": 32,
                "fixed_value_owner_payload_bytes_per_maa": 32768,
                "active_value_owner_payload_bytes_per_maa": 8192,
            },
            "candidate": {
                "stats": {
                    "IND_SoaJitValueReadIssues": 64,
                    "IND_SoaJitValueHits": 960,
                    "IND_SoaJitValueMergedWaiters": 0,
                    "IND_SoaJitValueDeliveries": 1024,
                }
            },
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
            "native_arms=0\nlogical_elements=16384\n"
            "physical_tile_elements=4096\nactive_contexts=8\n"
            "full_graph=true\ntrace=false\nwall_timeout=none\n"
            "external_coherent_backing_bytes=1048576\n"
            "external_admission_dense_metadata_bytes=37748736\n"
            "external_admission_tracker_max_bytes=1024\n"
            "external_application_metadata_bytes=37749760\n"
            "admission_metadata_accelerator_sram_bytes=0\n"
            "page_final_map_entries_per_thread=4096\n"
            "page_final_map_threads=4\n"
            "page_final_map_allocator_bytes="
            "implementation_defined_external_application_memory\n"
        )
        oracle = (
            "SSSP_FINGERPRINT vertices=8 reached=8 unreachable=0 "
            "distance_sum=12 max_distance=3 hash_a=0123456789abcdef "
            "hash_b=fedcba9876543210 triangle_violations=0 "
            "missing_predecessors=0 nonpositive_weights=0 "
            "negative_distances=0 result=PASS"
        )
        (root / "external_reference.manifest").write_text(f"oracle={oracle}\n")
        (root / "run").mkdir(exist_ok=True)
        terminal = (
            "SSSP_OLD_RESULT_HYBRID_TERMINAL "
            "treatment=old_result_hybrid eligible_windows=2 routed_windows=1 "
            "unsafe_eligible_windows=1 index_publish_pages=4 "
            "value_publish_pages=4 old_result_words=16384 "
            "legacy_words=16384 fallback_pages=4 "
            "fallback_publication_issue_pages=12 "
            "fallback_publication_response_pages=12 "
            "fallback_publication_words=49152 "
            "fallback_publication_bytes=196608 "
            "fallback_consumed_words=16384 predicate_restore_words=16384 "
            "coherent_tail_words=0 logical_reorder_words=16384 "
            "physical_spd_words=4096 row_table_slices=32 host_spd_reads=0 "
            "illegal_host_spd_line_starts=0 new_dedicated_payload_bytes=0 "
            "hidden_logical_spd_bytes=0 hidden_result_payload_bytes=0 "
            "response_closure=1 counts_close=1"
        )
        (root / "run/restore.log").write_text(
            f"{terminal}\nROI End!!!\n{oracle}\n"
            "Exiting @ tick 9 because m5_exit instruction encountered\n"
        )
        (root / "run/stats.txt").write_text(
            "---------- Begin Simulation Statistics ----------\n"
            "simTicks 8\n"
            "---------- End Simulation Statistics ----------\n"
            "---------- Begin Simulation Statistics ----------\n"
            "simTicks 9\n"
            "---------- End Simulation Statistics ----------\n"
        )
        (root / "checkpoint.exit").write_text("0\n")
        (root / "run/restore.exit").write_text("0\n")
        (root / "wrapper.status").write_text("exit_code=0\n")
        (root / "result.txt").write_text(
            "validation=PASS\n"
            "routing_status=eligible_subset_routed_fallbacks_preserved\n"
        )
        if gated:
            (root / "gate.complete").write_text("PASS\n")
        else:
            (root / "gate.complete").unlink(missing_ok=True)
        artifact_witness = root / "artifact.witness"
        checkpoint_witness = root / "checkpoint.witness"
        artifact_witness.write_text("artifact")
        checkpoint_witness.write_text("checkpoint")
        for name in (
            "provenance/artifacts.before.sha256",
            "provenance/artifacts.after.sha256",
        ):
            self._ledger(root, name, [artifact_witness])
        for name in (
            "provenance/checkpoint.before.files.sha256",
            "provenance/checkpoint.after.files.sha256",
        ):
            self._ledger(root, name, [checkpoint_witness])
        identity = self._hash(
            root / "provenance/checkpoint.before.files.sha256"
        )
        (root / "provenance/checkpoint.before.identity.sha256").write_text(
            identity + "\n"
        )
        (root / "provenance/checkpoint.after.identity.sha256").write_text(
            identity + "\n"
        )

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

    def test_full_cg_without_retention_traffic_is_rejected(self) -> None:
        root = self.roots["cg_direct4"]
        result = json.loads(root.joinpath("result.json").read_text())
        result["candidate"]["stats"]["IND_SoaJitValueHits"] = 0
        root.joinpath("result.json").write_text(json.dumps(result))
        gate = root.joinpath("gate.complete").read_text().splitlines()
        gate[1] = f"result_sha256={self._hash(root / 'result.json')}"
        root.joinpath("gate.complete").write_text("\n".join(gate) + "\n")
        self.assertEqual(
            AUDIT.audit_cg(
                self.roots["cg_certificate"], self.roots["cg_direct4"]
            )["status"],
            "failed",
        )

    def test_lane4_successor_certificate_is_accepted(self) -> None:
        root = self.base / "lane4-certificate"
        root.mkdir()
        root.joinpath("manifest.json").write_text("{}")
        cert = {
            "schema": "dx100.cg.direct4_product_page_fed_q16_lane4_full_successor_certificate.v1",
            "verdict": "PASS_NUMERICAL_MECHANISM_CORRECT",
            "native_speedup_claim": False,
            "iso_area_claim": False,
            "full_promotion_claim": False,
            "candidate": {
                "first_roi_simTicks": 111116739967,
                "stats": {
                    "IND_SoaJitInstructions": 10960,
                    "IND_SoaJitActiveApplyLanes": 43840,
                    "IND_SoaJitApplyLaneHighWater": 43242,
                },
                "terminal": {
                    "p16_reorder_preserved": 0,
                    "q16_reorder_preserved": 1,
                    "physical_spd_payload_bytes": 524288,
                    "external_coherent_backing_bytes": 262144,
                },
            },
            "accepted_lane_1_control": {
                "first_roi_simTicks": 123968991971,
                "certificate_verified": True,
            },
            "lane_accounting": {
                "at_least_one_operation_used_four_lanes": True
            },
            "hardware_accounting": {
                "new_payload_bytes": 0,
                "new_control_bytes": 0,
                "new_ports": 0,
                "physical_spd_payload_bytes": 524288,
                "fixed_apply_lanes_per_indirect_unit": 4,
                "incremental_apply_lane_pool_bytes_vs_lane_1": 0,
            },
        }
        root.joinpath("certificate.json").write_text(json.dumps(cert))
        root.joinpath("gate.complete").write_text(
            "PASS_NUMERICAL_MECHANISM_CORRECT\n"
        )
        self._input(root)
        self.assertEqual(
            AUDIT.audit_cg(self.roots["cg_certificate"], root)["status"],
            "passed",
        )

    def test_hidden_hardware_bytes_are_rejected(self) -> None:
        self.roots["sssp"].joinpath("candidate.manifest").write_text(
            "native_arms=0\nlogical_elements=16384\nphysical_tile_elements=4096\nactive_contexts=8\nhidden_payload_bytes=4\n"
        )
        self.assertEqual(
            AUDIT.audit_sssp(self.roots["sssp"])["status"], "failed"
        )

    def test_sssp_zero_routed_windows_are_rejected(self) -> None:
        restore = self.roots["sssp"] / "run/restore.log"
        restore.write_text(
            restore.read_text()
            .replace("routed_windows=1", "routed_windows=0")
            .replace("unsafe_eligible_windows=1", "unsafe_eligible_windows=2")
            .replace("index_publish_pages=4", "index_publish_pages=0")
            .replace("value_publish_pages=4", "value_publish_pages=0")
            .replace("old_result_words=16384", "old_result_words=0")
        )
        self.assertEqual(
            AUDIT.audit_sssp(self.roots["sssp"])["status"], "failed"
        )

    def test_sssp_restore_must_contain_exact_oracle(self) -> None:
        restore = self.roots["sssp"] / "run/restore.log"
        restore.write_text(restore.read_text().replace("hash_a=", "hash_a=x"))
        self.assertEqual(
            AUDIT.audit_sssp(self.roots["sssp"])["status"], "failed"
        )

    def test_sssp_nonzero_restore_exit_is_rejected(self) -> None:
        self.roots["sssp"].joinpath("run/restore.exit").write_text("1\n")
        self.assertEqual(
            AUDIT.audit_sssp(self.roots["sssp"])["status"], "failed"
        )

    def test_sssp_before_after_artifact_drift_is_rejected(self) -> None:
        witness = self.roots["sssp"] / "artifact-after.witness"
        witness.write_text("changed")
        self._ledger(
            self.roots["sssp"],
            "provenance/artifacts.after.sha256",
            [witness],
        )
        self.assertEqual(
            AUDIT.audit_sssp(self.roots["sssp"])["status"], "failed"
        )

    def test_sssp_external_metadata_must_be_disclosed(self) -> None:
        manifest = self.roots["sssp"] / "candidate.manifest"
        manifest.write_text(
            manifest.read_text().replace(
                "external_application_metadata_bytes=37749760",
                "external_application_metadata_bytes=0",
            )
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
