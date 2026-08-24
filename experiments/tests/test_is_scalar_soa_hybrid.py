#!/usr/bin/env python3

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class IsScalarSoaHybridContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "benchmarks/NAS/is/is.cpp").read_text()
        cls.makefile = (ROOT / "benchmarks/NAS/is/Makefile").read_text()
        cls.runner = (
            ROOT / "experiments/scripts/run_is_scalar_soa_hybrid.sh"
        ).read_text()

    def test_compile_and_runtime_selectors_preserve_legacy_default(self):
        self.assertIn("-DIS_SCALAR_SOA_JIT", self.makefile)
        self.assertIn("IsRmwTreatment::Legacy", self.source)
        self.assertIn('std::string(argv[2]) == "scalar_soa_jit"', self.source)
        self.assertIn('legacy_default=" << (argc == 2 ? 1 : 0)', self.source)
        self.assertEqual(
            self.source.count("maa_indirect_rmw_scalar_soa_jit<int>"), 1
        )
        self.assertEqual(self.source.count("maa_indirect_rmw_scalar<int>"), 1)

    def test_logical_window_uses_registered_coherent_indices(self):
        soa = self.source.index("maa_indirect_rmw_scalar_soa_jit<int>")
        region = self.source.rindex(
            "add_mem_region(key_buff_ptr2, &key_buff_ptr2[NUM_KEYS])", 0, soa
        )
        self.assertLess(region, soa)
        window = self.source[soa - 1100 : soa + 700]
        self.assertIn("key_buff_ptr2 + i", window)
        self.assertIn("window_words", window)
        self.assertIn("nullptr, scalar, minimum, maximum, stride", window)
        self.assertIn("completion_tile", window)
        self.assertIn("wait_ready(completion_tile)", window)

    def test_exact_range_and_tail_are_bounded(self):
        for token in (
            "minimum = get_new_reg<int>(0)",
            "maximum = get_new_reg<int>(TILE_SIZE)",
            "stride = get_new_reg<int>(1)",
            "scalar = get_new_reg<int>(1)",
            "remaining < TILE_SIZE ? remaining : TILE_SIZE",
            "maa_const<int>(window_words, maximum)",
        ):
            self.assertIn(token, self.source)

    def test_no_application_staging_or_spd_readback(self):
        treatment = self.source[
            self.source.index("if (is_rmw_treatment ==") : self.source.index(
                "continue;", self.source.index("if (is_rmw_treatment ==")
            )
        ]
        self.assertNotIn("get_cacheable_tile_pointer", treatment)
        self.assertNotIn("maa_stream_store", treatment)
        self.assertNotIn("new ", treatment)
        self.assertIn("host_spd_reads=0 staging_bytes=0", self.source)

    def test_terminal_accounts_every_index_and_no_payload(self):
        for token in (
            "generations >= 2",
            "index_words == NUM_KEYS",
            "full_windows * TILE_SIZE + tail_words",
            '" predicate_words=0 value_words=0"',
            '" host_spd_reads=0 staging_bytes=0"',
            '"IS_SCALAR_SOA_JIT_TERMINAL',
        ):
            self.assertIn(token, self.source)

    def test_runner_is_hybrid_only_and_has_no_wall_timeout(self):
        self.assertIn("native_runs=0", self.runner)
        self.assertIn("wall_timeout=none", self.runner)
        self.assertNotIn("timeout ", self.runner)
        self.assertNotIn("run_arm control", self.runner)
        self.assertIn("--maa_num_tile_elements=16384", self.runner)
        self.assertIn("--maa_physical_tile_elements=4096", self.runner)

    def test_runner_requires_exact_correctness_and_closed_ledgers(self):
        for token in (
            "successfull: passed verification 6",
            "HYBRID_RMW_SCALAR_SOA_RESULT generations=2",
            "IND_VirtIndexLineReads",
            "IND_VirtIndexWords",
            "IND_SoaJitPredicateLineResponses",
            "IND_SoaJitValueReadResponses",
            "IND_SoaJitAReadResponses",
            "IND_SoaJitAWriteResponses",
            "IND_SoaJitTerminalCompletions",
            "staging_bytes=0 result=PASS",
        ):
            self.assertIn(token, self.runner)

    def test_runner_records_exact_provenance_and_frozen_analysis_source(self):
        for token in (
            "source_commit=",
            "source_sha256=",
            "gem5_sha256=",
            "guest_sha256=",
            "input_sha256=",
            "physical_tile_sweep_baseline_20260822.json",
            "frozen_native_sha256=",
            "num_initial_row_table_slices=32",
        ):
            self.assertIn(token, self.runner)


if __name__ == "__main__":
    unittest.main()
