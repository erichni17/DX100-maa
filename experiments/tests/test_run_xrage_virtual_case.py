#!/usr/bin/env python3
"""Contract tests for the representative XRAGE virtualization runner."""

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/scripts/run_xrage_virtual_case.sh"


class XrageVirtualCaseContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = RUNNER.read_text(encoding="utf-8")

    def test_shell_syntax(self):
        subprocess.run(["bash", "-n", str(RUNNER)], check=True)

    def test_freezes_all_external_artifacts_and_resolves_ramulator(self):
        for name in (
            "gem5.opt",
            "xrage_verify",
            "xrage.json",
            "libramulator.so",
            "ramulator_provenance.json",
            "simulator_provenance.json",
        ):
            self.assertIn(f'"$out/input/{name}"', self.source)
        self.assertIn('LD_LIBRARY_PATH="$library_path" ldd "$gem5"', self.source)
        self.assertIn('artifact_sha256.txt', self.source)
        self.assertIn('checkpoint_identity.sha256', self.source)
        self.assertIn('evidence_sha256.txt', self.source)

    def test_ramulator_json_authenticates_frozen_elf(self):
        self.assertIn("dx100.ramulator_provenance.v1", self.source)
        self.assertIn('record.get("frozen_library", {}).get("sha256"', self.source)
        self.assertIn("Ramulator provenance does not authenticate", self.source)
        self.assertIn("Ramulator ELF build ID differs", self.source)

    def test_simulator_provenance_separates_binary_and_runner_commits(self):
        self.assertIn("dx100.simulator_provenance.v1", self.source)
        self.assertIn("simulator_source_commit", self.source)
        self.assertIn("runner_source_commit", self.source)
        self.assertIn("simulator_build.command", self.source)
        self.assertIn("simulator_build.log", self.source)
        self.assertIn(
            "simulator provenance does not authenticate a clean successful build",
            self.source,
        )
        self.assertIn(
            "comparison=4k_physical_full_metadata_vs_4k_physical_bounded_metadata",
            self.source,
        )

    def test_first_arm_is_exact_full_metadata_smoke(self):
        self.assertIn("run_xrage_direct_index_smoke.sh", self.source)
        self.assertIn("XRAGE_GUEST_ARM=direct4", self.source)
        self.assertIn("MAA_PHYSICAL_TILE_ELEMENTS=4096", self.source)
        self.assertIn("MAA_ROW_TABLE_ROWS_PER_SLICE=64", self.source)
        self.assertIn("MAA_NUM_OFFSET_TABLE_ENTRIES=16384", self.source)
        self.assertIn("MAA_NUM_OFFSET_TABLE_EPOCH_ENTRIES=16384", self.source)

    def test_bounded_arm_reuses_neutral_checkpoint(self):
        self.assertIn("recover_xrage_checkpoint.sh", self.source)
        self.assertIn("XRAGE_ALLOW_PRE_MAA_RETARGET=1", self.source)
        self.assertIn("MAA_ROW_TABLE_ROWS_PER_SLICE=32", self.source)
        self.assertIn("MAA_NUM_OFFSET_TABLE_ENTRIES=4096", self.source)
        self.assertIn("MAA_NUM_OFFSET_TABLE_EPOCH_ENTRIES=4096", self.source)
        self.assertIn("MAA_VIRTUAL_INDEX_PARTITIONS=4", self.source)
        self.assertIn("MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE=16", self.source)
        self.assertIn('checkpoint_run=$shared_checkpoint_run', self.source)
        self.assertIn(
            '! $checkpoint_command =~ (^|[[:space:]])--maa($|[[:space:]])',
            self.source,
        )

    def test_frozen_checkpoint_requires_matching_guest_abi_and_input(self):
        self.assertIn("checkpoint_source/artifact_sha256.txt", self.source)
        self.assertIn("workload_hash", self.source)
        self.assertIn(
            "frozen checkpoint input does not match the requested XRAGE input",
            self.source,
        )
        self.assertIn(
            "XRAGE verifier binary does not match the frozen checkpoint ABI",
            self.source,
        )

    def test_resume_is_fail_closed_on_exact_smoke_evidence(self):
        self.assertIn("XRAGE_RESUME", self.source)
        self.assertIn("xrage_checkpoint_recovery.pass", self.source)
        self.assertIn("resumed XRAGE input artifacts changed", self.source)
        self.assertIn("resumed XRAGE full-metadata artifacts changed", self.source)
        self.assertIn("MAA_GATHER_VERIFY_FAIL", self.source)
        self.assertIn("resume_runner_source_commit", self.source)

    def test_comparison_requires_exact_hash_and_simticks(self):
        self.assertIn('bounded_hash == "$full_hash"', self.source)
        self.assertIn("roi_simTicks", self.source)
        self.assertIn("delta_vs_full_pct", self.source)
        self.assertNotIn("hostSeconds", self.source)


if __name__ == "__main__":
    unittest.main()
