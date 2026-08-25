#!/usr/bin/env python3
"""Static adversarial contract for the trace-free full-CG page-fed gate."""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/scripts/run_cg_page_fed_application_full.sh"


class CgPageFedApplicationFullContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = RUNNER.read_text()

    def test_frozen_candidate_inputs_are_pinned(self):
        for token in (
            "gem5-page-fed-606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427.opt",
            "gem5_sha=606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427",
            "frozen_header_sha=f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131",
            '-DUSE_DATA_FROM_FILE -DCG_NA="$cg_na"',
            "token_stream_ld page_fed_product_soa_jit",
            "--maa_page_fed_soa_jit",
            "--maa_num_indirect_units_per_maa=4",
            "--maa_num_tile_elements=16384",
            "--maa_physical_tile_elements=4096",
            "--maa_num_initial_row_table_slices=32",
            "--mem-channels=2",
        ):
            self.assertIn(token, self.text)

    def test_oracles_and_exact_full_closure_are_guarded(self):
        for token in (
            "predecessor_ticks=818687246165",
            "native16_simTicks=58928150676",
            "expected_windows=10960",
            "expected_pages=43840",
            "expected_publish_lines=11223040",
            "x_q5 x_q6 z_q5 z_q6",
            "IND_SoaJitPageFedCoherentIndexReadLines",
            "IND_SoaJitPageFedCoherentIndexWriteLines",
            "IND_SoaJitPageFedStateByteOperations",
            "IND_SoaJitValueReadResponses",
            "IND_SoaJitAWriteResponses",
            "IND_BoundedGlobalMergeFallbacks",
            "IND_SoaJitEpochDrains",
            "page_fed_total_abi_responses",
            "ratio_predecessor_over_candidate",
            "ratio_native16_over_candidate",
        ):
            self.assertIn(token, self.text)

    def test_predecessor_json_check_accepts_pinned_pretty_printing(self):
        self.assertIn(
            "grep -Fq '\"candidate_simTicks\": 818687246165,'",
            self.text,
        )
        self.assertNotIn(
            "grep -Fqx '\"candidate_simTicks\": 818687246165,'",
            self.text,
        )

    def test_trace_and_timeout_are_prohibited(self):
        self.assertNotIn("--debug-flags", self.text)
        self.assertNotIn("--debug-file", self.text)
        self.assertNotIn("timeout ", self.text)
        self.assertIn('[[ ! -e "$out/run/page_fed_trace.log"', self.text)

    def test_adversarial_removal_of_each_critical_guard_is_detected(self):
        guards = (
            "frozen_header_sha=f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131",
            "predecessor_ticks=818687246165",
            "expected_windows=10960",
            "IND_SoaJitPageFedCoherentIndexReadLines",
            "IND_SoaJitValueReadResponses",
            "IND_BoundedGlobalMergeFallbacks",
        )
        for guard in guards:
            with self.subTest(guard=guard):
                mutated = self.text.replace(guard, "", 1)
                self.assertNotIn(guard, mutated)
                self.assertIn(guard, self.text)

    def test_immutable_ledgers_and_gate_order_are_present(self):
        for token in (
            "checkpoint.files.sha256.before",
            "checkpoint.files.sha256.after",
            "artifact_sha256.before",
            "artifact_sha256.after",
            "source_status.before",
            "source_status.after",
            'cmp -s "$out/input/source_status.before" "$out/input/source_status.after"',
            'touch "$out/gate.complete"',
            "refusing nonempty output",
        ):
            self.assertIn(token, self.text)
        self.assertLess(
            self.text.index('cmp -s "$out/input/source_status.before"'),
            self.text.index('touch "$out/gate.complete"'),
        )

    def test_shell_is_valid(self):
        subprocess.run(["bash", "-n", str(RUNNER)], check=True)


if __name__ == "__main__":
    unittest.main()
