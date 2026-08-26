import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "experiments/scripts/run_cg_fused_p16_product_q16.py"
TEXT = RUNNER_PATH.read_text()
SPEC = importlib.util.spec_from_file_location("fused_pair", RUNNER_PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def terminal(treatment: str) -> dict[str, str]:
    windows = 10
    pages = windows * 4
    words = windows * 16384
    common = {
        "full_windows": windows,
        "staged_index_words": words,
        "staged_value_words": 0,
        "product_words": words,
        "index_publish_pages": 0,
        "value_publish_pages": 0,
        "logical_alu_vectors": 0,
        "logical_page_windows": 0,
        "physical_page_product_windows": 0,
        "direct4_product_page_fed_q16_windows": 0,
        "physical_p_gather_pages": 0,
        "page_fed_admit_pages": pages,
        "page_fed_closes": windows,
        "q_spmv_eligible_windows": 8,
        "q_spmv_routed_windows": 8,
        "residual_spmv_eligible_windows": 2,
        "residual_spmv_routed_windows": 2,
        "physical_spd_payload_bytes": 524288,
        "host_payload_access": 0,
        "coherent_index_backing_bytes": 0,
        "p16_reorder_preserved": 1,
        "q16_reorder_preserved": 1,
        "hidden_spill_bytes": 0,
        "global_fallbacks": 0,
    }
    if treatment == "page_fed_product_soa_jit":
        common.update(
            {
                "p_gather_mode": "virtual_16k",
                "page_fed_product_windows": windows,
                "fused_p16_product_windows": 0,
                "physical_alu_vectors": pages,
                "product_publish_pages": pages,
                "product_publisher_lines": pages * 256,
                "virtual_p_gather_windows": windows,
                "virtual_p_backing_bytes": 262144,
                "virtual_p_allocation_bytes": 1048576,
                "virtual_p_write_bytes": windows * 65536,
                "virtual_p_read_bytes": windows * 65536,
                "virtual_backing_traffic_eliminated": 0,
                "external_coherent_backing_bytes": 524288,
            }
        )
    else:
        common.update(
            {
                "p_gather_mode": "fused_virtual_16k_product",
                "page_fed_product_windows": 0,
                "fused_p16_product_windows": windows,
                "physical_alu_vectors": 0,
                "product_publish_pages": 0,
                "product_publisher_lines": 0,
                "virtual_p_gather_windows": 0,
                "virtual_p_backing_bytes": 0,
                "virtual_p_allocation_bytes": 0,
                "virtual_p_write_bytes": 0,
                "virtual_p_read_bytes": 0,
                "virtual_backing_traffic_eliminated": 1,
                "external_coherent_backing_bytes": 262144,
            }
        )
    return {key: str(value) for key, value in common.items()}


class FusedP16PairContract(unittest.TestCase):
    def test_only_na256_control_and_candidate_are_selected(self):
        self.assertEqual(runner.CG_NA, 256)
        self.assertEqual(
            runner.ARMS,
            (
                ("control", "page_fed_product_soa_jit"),
                ("candidate", "fused_p16_product_q16"),
            ),
        )
        self.assertIn("native/full are forbidden", TEXT)
        self.assertNotIn("CG_NA = 150000", TEXT)

    def test_terminal_closes_control_and_zero_virtual_p_candidate(self):
        self.assertEqual(
            runner.require_terminal(
                terminal("page_fed_product_soa_jit"),
                "page_fed_product_soa_jit",
            ),
            10,
        )
        self.assertEqual(
            runner.require_terminal(
                terminal("fused_p16_product_q16"), "fused_p16_product_q16"
            ),
            10,
        )
        broken = terminal("fused_p16_product_q16")
        broken["virtual_p_write_bytes"] = "1"
        with self.assertRaises(RuntimeError):
            runner.require_terminal(broken, "fused_p16_product_q16")

    def test_finite_knobs_and_all_ledgers_are_mandatory(self):
        for knob in (
            "--maa_virtual_combine_ways=4",
            "--maa_virtual_combine_banks=4",
            "--maa_virtual_words_per_cycle=1",
            "--maa_virtual_response_slots=8",
            "--maa_virtual_max_outstanding_writes=32",
            "--maa_soa_jit_value_prefetch_credits=0",
        ):
            self.assertIn(knob, runner.FINITE_KNOBS)
        self.assertIn('--maa_soa_jit_value_cache_enable', TEXT)
        self.assertIn('--maa_soa_jit_active_value_owners=32', TEXT)
        cg = (ROOT / "benchmarks/NAS/cg/cg.cpp").read_text()
        self.assertIn("std::memset(virtual_gather_storage, 0,", cg)
        for ledger in (
            "IND_FusedP16Operations",
            "IND_FusedP16Epochs",
            "IND_FusedP16SourceOrdinals",
            "IND_FusedP16CoefficientDeliveries",
            "IND_FusedP16MulAccepts",
            "IND_FusedP16MulCompletions",
            "IND_FusedP16ProductInsertions",
            "IND_FusedP16ProductWriteCompletions",
            "IND_FusedP16PublisherLines",
            "IND_FusedP16VirtualPBytes",
            "IND_SoaJitPageFedCommandResponses",
            "IND_SoaJitValueDeliveries",
        ):
            self.assertIn(ledger, TEXT)

    def test_acceptance_requires_exactness_and_lower_simticks(self):
        self.assertIn(
            'control["fingerprint_line"] == candidate["fingerprint_line"]',
            TEXT,
        )
        self.assertIn(
            'control["reduction_evidence"] == candidate["reduction_evidence"]',
            TEXT,
        )
        self.assertIn(
            '"ACCEPT" if candidate_ticks < control_ticks else "REJECT"',
            TEXT,
        )
        self.assertIn("checkpoint_before == checkpoint_after", TEXT)


if __name__ == "__main__":
    unittest.main()
