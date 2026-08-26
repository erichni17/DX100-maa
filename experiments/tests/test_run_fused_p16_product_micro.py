import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = (
    ROOT / "experiments/scripts/run_fused_p16_product_micro.sh"
).read_text()
GUEST = (ROOT / "benchmarks/API/test_fused_p16_product.cpp").read_text()


class FusedP16ProductMicroContract(unittest.TestCase):
    def test_guest_has_four_segment_exact_product_and_q_oracles(self):
        for token in (
            "all_same,same_line,cross_page,pseudorandom",
            "maa_indirect_load_virtual_index_product_fp32",
            "FUSED_P16_PRODUCT_DUMP",
            "DumpWords = 64",
            "referenceProducts[ordinal]",
            "product != expected || q != expected",
            "FUSED_P16_PRODUCT_SENTINELS count=",
            "q_page_admissions=4",
            "virtual_p_allocation_bytes=0",
            "product_publisher_lines=0",
        ):
            self.assertIn(token, GUEST)

    def test_runner_pins_only_the_finite_guarded_geometry(self):
        for option in (
            "--maa_num_tile_elements=16384",
            "--maa_physical_tile_elements=4096",
            "--maa_num_offset_table_entries=16384",
            "--maa_num_offset_table_epoch_entries=16384",
            "--maa_num_initial_row_table_slices=32",
            "--maa_virtual_combine_slots=16",
            "--maa_virtual_combine_ways=4",
            "--maa_virtual_combine_banks=4",
            "--maa_virtual_words_per_cycle=1",
            "--maa_virtual_response_slots=8",
            "--maa_virtual_max_outstanding_writes=32",
            "--maa_soa_jit_value_cache_enable",
            "--maa_soa_jit_active_value_owners=32",
            "--maa_soa_jit_value_prefetch_credits=0",
        ):
            self.assertIn(option, RUNNER)
        self.assertNotIn("--maa_virtual_words_per_cycle=0", RUNNER)
        self.assertNotIn("native", RUNNER.lower())
        self.assertNotIn("CG_NA=150000", RUNNER)

    def test_runner_requires_every_fused_and_q_ledger(self):
        for stat in (
            "IND_FusedP16Operations",
            "IND_FusedP16Epochs",
            "IND_FusedP16SourceOrdinals",
            "IND_FusedP16CoefficientReadIssues",
            "IND_FusedP16CoefficientDeliveries",
            "IND_FusedP16MulAccepts",
            "IND_FusedP16MulCompletions",
            "IND_FusedP16ProductInsertions",
            "IND_FusedP16ProductWriteCompletions",
            "IND_FusedP16EpochDrains",
            "IND_FusedP16Fallbacks",
            "IND_FusedP16PublisherLines",
            "IND_FusedP16VirtualPBytes",
            "IND_SoaJitPageFedOperations",
            "IND_SoaJitPageFedAdmitCommands",
            "IND_SoaJitPageFedCommandResponses",
            "IND_SoaJitValueDeliveries",
        ):
            self.assertIn(stat, RUNNER)
        self.assertIn("p_issue == p_response", RUNNER)
        self.assertIn("c_issue == c_response", RUNNER)
        self.assertIn('addresses("source_issue", operation_tick)', RUNNER)
        self.assertIn("expected 256 dump records", RUNNER)
        self.assertIn('"$out/checkpoint.log"', RUNNER)

    def test_removed_or_renamed_zero_stat_is_rejected(self):
        script = ROOT / "experiments/scripts/run_fused_p16_product_micro.sh"
        with tempfile.TemporaryDirectory() as tmp:
            stats = Path(tmp) / "stats.txt"
            begin = "---------- Begin Simulation Statistics ----------\n"
            end = "---------- End Simulation Statistics ----------\n"
            stats.write_text(
                begin
                + "system.maa.indirectUnits0_IND_FusedP16Fallbacks 0\n"
                + end
            )
            present = subprocess.run(
                [
                    str(script),
                    "--require-stat",
                    str(stats),
                    "IND_FusedP16Fallbacks",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(present.returncode, 0)
            self.assertEqual(present.stdout.strip(), "0")
            for body in ("", "IND_FusedP16Fallback 0\n"):
                stats.write_text(begin + body + end)
                rejected = subprocess.run(
                    [
                        str(script),
                        "--require-stat",
                        str(stats),
                        "IND_FusedP16Fallbacks",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(rejected.returncode, 0)

    def test_micro_persists_terminal_exit_and_digest_bound_raw_root(self):
        for token in (
            '"$out/input/wrapper_exit"',
            '"$out/input/checkpoint_exit"',
            '"$out/input/restore_exit"',
            '"$out/checkpoint.terminal"',
            '"$out/run/restore.terminal"',
            "raw_root.sha256",
            "gate.complete",
            "raw_root_sha256=",
            "! -name raw_root.sha256",
            "! -name gate.complete",
        ):
            self.assertIn(token, RUNNER)
        self.assertNotIn("stat_zero()", RUNNER)


if __name__ == "__main__":
    unittest.main()
