#!/usr/bin/env python3
"""Focused tests for the fail-closed final GZP attribution assembler."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "experiments/scripts/run_gzp_final_physical_attribution.py"
SPEC = importlib.util.spec_from_file_location("gzp_final_attribution", RUNNER)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def complete_ledger(arm: str) -> dict[str, int]:
    instructions = 61 if arm == "volume_only" else 122
    selected = 1_000_000
    ledger = {suffix: 0 for suffix in MODULE.SOA_SUFFIXES}
    ledger.update({suffix: 0 for suffix in MODULE.PUBLISH_SUFFIXES})
    ledger.update(
        {
            "IND_SoaJitInstructions": instructions,
            "IND_SoaJitTerminalCompletions": instructions,
            "IND_SoaJitSelected": selected,
            "IND_SoaJitAliasesApplied": selected,
            "IND_SoaJitValueDeliveries": selected,
            "IND_SoaJitLookaheadIssues": selected,
            "IND_SoaJitLookaheadResponses": selected,
            "IND_SoaJitAReadIssues": 100,
            "IND_SoaJitAReadResponses": 100,
            "IND_SoaJitAWriteIssues": 100,
            "IND_SoaJitAWriteResponses": 100,
            "IND_SoaJitValueReadIssues": selected - 30,
            "IND_SoaJitValueReadResponses": selected - 30,
            "IND_SoaJitValueFills": selected - 30,
            "IND_SoaJitValueHits": 20,
            "IND_SoaJitValueMergedWaiters": 10,
            "IND_SoaJitPreAValueIssues": 40,
            "IND_SoaJitPreAValueReadyAtAResponse": 20,
            "IND_SoaJitPreAValueUses": 40,
            "numInst_INDRMW": 307 if arm == "volume_only" else 124,
            "cycles_INDRMW": 123,
            "STR_PublishCreditHWM": 0 if arm == "volume_only" else 8,
        }
    )
    if arm == "dual_logical16":
        lines = 61 * 1024
        ledger.update(
            {
                "STR_PublishIssues": lines,
                "STR_PublishAccepts": lines,
                "STR_PublishWriteResponses": lines,
                "STR_PublishTerminals": 61 * 4,
            }
        )
    return ledger


class FinalAttributionTest(unittest.TestCase):
    def test_plan_uses_three_honest_evidence_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--out",
                    str(root / "out"),
                    "--candidate-gate",
                    str(root / "candidate"),
                    "--native16-evidence",
                    str(root / "native"),
                    "--api-physical-evidence",
                    str(root / "api"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        plan = json.loads(result.stdout)
        self.assertEqual(plan["n"], 1_000_000)
        self.assertEqual(
            plan["evidence"]["schedule_gain"]["pair"],
            ["volume_only", "dual_logical16"],
        )
        self.assertEqual(
            plan["evidence"]["schedule_gain"]["replicas_per_arm"], 2
        )
        self.assertEqual(
            plan["evidence"]["end_to_end_ceiling"]["arm"],
            "ordinary_native16",
        )
        self.assertEqual(
            plan["evidence"]["virtualization_isolation"]["pair"],
            [
                "soa_metadata16_physical4",
                "soa_metadata16_physical16",
            ],
        )
        self.assertFalse(plan["same_instruction_gzp_physical16"]["included"])
        self.assertEqual(
            plan["same_instruction_gzp_physical16"][
                "required_publisher_lines_per_window"
            ],
            4096,
        )
        self.assertEqual(
            plan["selector_isolation"],
            "immutable_per_arm_ro_bind_required",
        )
        self.assertFalse(plan["timeouts"])

    def test_publisher_audit_proves_macro_only_physical16_invalid(
        self,
    ) -> None:
        audit = MODULE.audit_publisher_boundary()
        for key in (
            "publisher_fixed_4096",
            "runtime_rejects_non4096",
            "source_size_must_be_4096",
            "source_capture_starts_at_zero",
            "guest_backing_page_stride_4096",
        ):
            self.assertTrue(audit[key])
        self.assertFalse(audit["same_instruction_physical16_supported"])
        self.assertEqual(
            audit["current_fp32_lines_per_logical16_window"], 1024
        )
        self.assertEqual(
            audit["required_future_physical16_lines_per_window"], 4096
        )
        self.assertIn("source-offset", audit["blocker"])

    def test_complete_publisher_rmw_a_value_ledgers(self) -> None:
        for arm in ("volume_only", "dual_logical16"):
            with self.subTest(arm=arm):
                MODULE.validate_complete_ledgers(arm, complete_ledger(arm))

    def test_ledgers_fail_closed_on_missing_write_response(self) -> None:
        ledger = complete_ledger("dual_logical16")
        ledger["STR_PublishWriteResponses"] -= 1
        with self.assertRaisesRegex(RuntimeError, "ledger did not close"):
            MODULE.validate_complete_ledgers("dual_logical16", ledger)

    def test_candidate_selector_requires_read_only_bwrap_bind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            selector = run / "frozen_treatment.txt"
            selector.write_text("token_stream_ld dual_logical16\n")
            selector.chmod(0o444)
            command = [
                "/usr/bin/bwrap",
                "--die-with-parent",
                "--bind",
                "/",
                "/",
                "--ro-bind",
                str(selector.resolve()),
                "/frozen/checkpoint-selector.txt",
                "--",
                "/frozen/gem5.opt",
            ]
            (run / "restore.command.json").write_text(json.dumps(command))
            digest = MODULE.common.sha256(selector)
            MODULE.verify_bound_selector(run, digest)
            command.remove("--ro-bind")
            (run / "restore.command.json").write_text(json.dumps(command))
            with self.assertRaisesRegex(RuntimeError, "bind isolation"):
                MODULE.verify_bound_selector(run, digest)

    def test_api_pair_is_geometry_only_and_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "api"
            for arm in MODULE.API_ARMS:
                (root / "runs" / arm).mkdir(parents=True)
            checkpoint = root / "checkpoints/soa16"
            checkpoint.mkdir(parents=True)
            manifest = (
                "source_commit=abc\n"
                "gem5_sha256=def\n"
                f"soa_pair_checkpoint={checkpoint}\n"
                "soa_pair_only_geometry_delta=physical_tile_elements\n"
            )
            (root / "manifest.txt").write_text(manifest)
            (root / "matrix.tsv").write_text(
                "arm\tmode\tlogical\tphysical\tsimTicks\toutput_hash\n"
                "soa_metadata16_physical4\tsoa\t16384\t4096\t120\t42\n"
                "soa_metadata16_physical16\tsoa\t16384\t16384\t100\t42\n"
            )
            for arm in MODULE.API_ARMS:
                (root / "runs" / arm / "restore.log").write_text(
                    "HYBRID_RMW_SOA_RESULT errors=0\n"
                    "Exiting @ tick 1 because m5_exit instruction "
                    "encountered\n"
                )

            def fake_stats(path: Path) -> dict[str, int]:
                ticks = 120 if "physical4" in str(path) else 100
                return {"simTicks": ticks}

            with mock.patch.object(MODULE.common, "first_stats", fake_stats):
                result = MODULE.validate_api_pair(
                    root, MODULE.common.sha256(root / "manifest.txt")
                )
        self.assertEqual(result["physical4_over_physical16"], 1.2)
        self.assertIn("not a GZP publisher result", result["scope"])

    def test_summary_keeps_attribution_boundaries_separate(self) -> None:
        rows = []
        for replica in (1, 2):
            rows.append(
                {
                    "arm": "volume_only",
                    "replica": replica,
                    "simTicks": 120,
                }
            )
            rows.append(
                {
                    "arm": "dual_logical16",
                    "replica": replica,
                    "simTicks": 100,
                }
            )
        native = {
            "mean_simTicks": 80.0,
            "simTicks": [80],
            "replicas": 1,
            "records": [
                {
                    "replica": 1,
                    "simTicks": 80,
                    "rmw_instructions": 124,
                    "rmw_cycles": 10,
                }
            ],
        }
        api = {"physical4_over_physical16": 1.1}
        summary = MODULE.summarize(rows, native, api)
        self.assertEqual(
            summary["schedule_gain"]["old_over_dual_speedup"], 1.2
        )
        self.assertEqual(
            summary["ordinary_native16_ceiling"]["dual_over_native16_ticks"],
            20,
        )
        self.assertEqual(summary["api_matched_physical_overhead"], api)
        self.assertFalse(
            summary["same_instruction_gzp_physical16"]["available"]
        )


if __name__ == "__main__":
    unittest.main()
