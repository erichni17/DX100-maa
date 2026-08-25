#!/usr/bin/env python3
"""Static fail-closed contract for the candidate-only page-fed small-CG gate."""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/scripts/run_cg_page_fed_application_small.sh"


class CgPageFedApplicationSmallContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = RUNNER.read_text()

    def test_archive_selector_and_geometry_are_pinned(self):
        for token in (
            "gem5-page-fed-606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427.opt",
            "gem5_sha=606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427",
            "token_stream_ld page_fed_product_soa_jit",
            "-DCG_PAGE_FED_SOA_ONLY",
            "--maa_page_fed_soa_jit",
            '-DCG_NA="$cg_na"',
            "--maa_num_indirect_units_per_maa=4",
            "--maa_num_tile_elements=16384",
            "--maa_physical_tile_elements=4096",
            "--maa_num_initial_row_table_slices=32",
            "--mem-channels=2",
        ):
            self.assertIn(token, self.text)
        self.assertNotIn(
            "CG_PHYSICAL_PAGE_PRODUCT_ONLY -DCG_FP_ENABLE\n", self.text
        )

    def test_only_frozen_predecessor_is_compared(self):
        for token in (
            "2026-08-24-cg-page-product-fusion-small-08a7b267-r2/result.txt",
            "predecessor_sha=4364635c504c738fcc6026d0dd10351418cd3bc458938082915fda1ee3bd0d32",
            "predecessor_ticks=6348682603",
            "candidate_only=true",
            "native_reruns=0",
            "predecessor_reruns=0",
            "wall_timeout=none",
            "speedup_vs_accepted",
            "traffic_delta_vs_accepted_lines",
        ):
            self.assertIn(token, self.text)
        self.assertNotIn("timeout ", self.text)

    def test_correctness_and_page_fed_closure_are_exact(self):
        for token in (
            "exact_quantized_hashes:x_q5,x_q6,z_q5,z_q6",
            "x_sum:1e-8",
            "rnorm:1e-3",
            "zeta:1e-10",
            "[[ $windows -eq 65 ]]",
            "page_fed_admits",
            "page_fed_closes",
            "IND_SoaJitPageFedCoherentIndexReadLines",
            "IND_SoaJitPageFedCoherentIndexWriteLines",
            "IND_SoaJitPageFedStateByteOperations",
            "IND_BoundedGlobalMergeFallbacks",
            'values["opens"] != 1',
            'values["open_responses"] != 1',
            'values["admissions"] != 4',
            'values["closes"] != 1',
            'values["value_read_lines"] != 16384',
            'values["a_read_lines"] != values["a_write_lines"]',
            "page_fed_trace_closure",
            'values["persistent_state_bytes"] != 16',
            "publisher_issue_accept_response=",
        ):
            self.assertIn(token, self.text)

    def test_immutability_and_clean_source_are_checked_before_gate_complete(
        self,
    ):
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

    def test_shell_is_valid(self):
        subprocess.run(["bash", "-n", str(RUNNER)], check=True)


if __name__ == "__main__":
    unittest.main()
