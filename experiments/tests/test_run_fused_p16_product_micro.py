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


if __name__ == "__main__":
    unittest.main()
