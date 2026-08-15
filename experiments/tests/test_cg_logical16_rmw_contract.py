#!/usr/bin/env python3

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class CGLogical16RmwContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "benchmarks/NAS/cg/cg.cpp").read_text()
        cls.makefile = (ROOT / "benchmarks/NAS/cg/Makefile").read_text()
        cls.runner = (
            ROOT / "experiments/scripts/run_cg_logical16_rmw_smoke.sh"
        ).read_text()

    def test_target_is_guarded_to_existing_general_hybrid_geometry(self):
        self.assertIn("%_maa_16K_general_fp_rmw:", self.makefile)
        for define in (
            "-DMAA_GENERAL_VIRTUAL_CONSUMER",
            "-DMAA_CONSUMER_TILE_SIZE=4096",
            "-DCG_LOGICAL16_RMW",
            "-DTILE_SIZE=16384",
        ):
            self.assertIn(define, self.makefile)
        self.assertIn("#if TILE_SIZE != 16384", self.source)
        self.assertIn("MAA_CONSUMER_TILE_SIZE != 4096", self.source)

    def test_selector_is_explicit_and_fail_closed(self):
        self.assertIn(
            "CG selector must contain exactly CONSUMER TREATMENT", self.source
        )
        self.assertIn('treatment == "legacy_4k"', self.source)
        self.assertIn('treatment == "residual_soa_jit"', self.source)
        self.assertIn(
            'treatment == "residual_soa_jit_response_bearing"', self.source
        )
        self.assertIn('mode != "MAA_DEFERRED" || argc != 3', self.source)
        self.assertIn("read_cg_treatment_selector", self.source)
        selection = self.source[
            self.source.index(
                'std::cout << "CG_LOGICAL16_RMW_SELECTION'
            ) : self.source.index(
                "#endif\n#endif\n#endif",
                self.source.index('std::cout << "CG_LOGICAL16_RMW_SELECTION'),
            )
        ]
        self.assertIn('<< " result=PASS"', selection)

    def test_only_residual_full_windows_use_soa_jit(self):
        residual = self.source.index("// LOOP 2")
        soa = self.source.index("maa_indirect_rmw_vector_soa_jit<float>")
        self.assertGreater(soa, residual)
        self.assertEqual(
            self.source.count("maa_indirect_rmw_vector_soa_jit<float>"), 1
        )
        self.assertIn("gather_size == TILE_SIZE", self.source[residual:soa])
        self.assertIn(
            "cg_rmw_treatment == CgRmwTreatment::ResidualSoaJit",
            self.source[residual:soa],
        )
        self.assertIn("nullptr,", self.source[soa : soa + 300])

    def test_cpu_staging_control_preserves_exact_page_operands_and_order(self):
        producer = self.source[
            self.source.index(
                "const bool soa_residual_full_window"
            ) : self.source.index(
                "#else\n            maa_const(k_base",
                self.source.index("const bool soa_residual_full_window"),
            )
        ]
        self.assertIn("cg_soa_indices[tid] + page_offset", producer)
        self.assertIn("cg_soa_values[tid] + page_offset", producer)
        self.assertIn("index_dst[word] >=", producer)
        self.assertIn("static_cast<uint32_t>(j_max - j_base)", producer)
        self.assertLess(
            producer.index("maa_stream_store<uint32_t>(index_dst"),
            producer.index("wait_ready(t0)"),
        )
        self.assertLess(
            producer.index("maa_stream_store<float>(value_dst"),
            producer.index("wait_ready(t7)"),
        )

    def test_cpu_control_never_reads_past_the_physical_spd_page(self):
        producer = self.source[
            self.source.index(
                "const bool soa_residual_full_window"
            ) : self.source.index(
                "#else\n            maa_const(k_base",
                self.source.index("const bool soa_residual_full_window"),
            )
        ]
        staging = producer[
            producer.index("if (soa_residual_full_window)") : producer.index(
                "cg_soa_value_words[tid] += page_size;"
            )
        ]
        self.assertNotIn("get_cacheable_tile_pointer", staging)
        self.assertIn("4096 beyond the physical tile", staging)
        self.assertIn("maa_const<int>(0, r2);", staging)
        self.assertIn("maa_const<int>(page_size, r3);", staging)
        self.assertIn(
            "maa_stream_store<uint32_t>(index_dst, r2, r3, r1,",
            staging,
        )
        self.assertIn(
            "maa_stream_store<float>(value_dst, r2, r3, r1, t7);", staging
        )
        self.assertLess(
            staging.index("maa_stream_store<uint32_t>"),
            staging.index("wait_ready(t0)"),
        )
        self.assertLess(
            staging.index("maa_stream_store<float>"),
            staging.index("wait_ready(t7)"),
        )

    def test_logical_window_storage_and_response_publisher_are_accounted(self):
        self.assertIn(
            "static uint32_t cg_soa_indices[NUM_CORES][TILE_SIZE]", self.source
        )
        self.assertIn(
            "static float cg_soa_values[NUM_CORES][TILE_SIZE]", self.source
        )
        self.assertIn("add_mem_region(cg_soa_indices[core]", self.source)
        self.assertIn("add_mem_region(cg_soa_values[core]", self.source)
        self.assertIn("external_staging_bytes=", self.source)
        self.assertIn('"cpu_after_spd_completion"', self.source)
        self.assertIn("response_bearing_spd_overlap", self.source)
        self.assertIn("dedicated_physical_payload_bytes=", self.source)
        self.assertIn("publisher_credit_payload_bytes=", self.source)
        self.assertIn("hidden_logical16_payload_bytes=0", self.source)
        self.assertIn("cpu_untimed_copy_bytes=", self.source)

    def test_response_publisher_keeps_4k_payloads_and_restores_range_check(
        self,
    ):
        producer = self.source[
            self.source.index(
                "const bool soa_residual_full_window"
            ) : self.source.index(
                "#else\n            maa_const(k_base",
                self.source.index("const bool soa_residual_full_window"),
            )
        ]
        response = producer[
            producer.index(
                "if (soa_residual_response_bearing)"
            ) : producer.index(
                "} else {\n                        // The provenance control",
                producer.index("if (soa_residual_response_bearing)"),
            )
        ]
        self.assertEqual(
            response.count("maa_publish_spd_page_logical16_response_bearing"),
            2,
        )
        self.assertIn("MAA_CONSUMER_TILE_SIZE", response)
        self.assertIn("wait_ready(t4)", response)
        self.assertIn("wait_ready(t5)", response)
        self.assertNotIn("maa_stream_store", response)
        self.assertNotIn("std::memcpy", response)
        self.assertNotIn("get_cacheable_tile_pointer", response)
        self.assertIn("std::atomic_thread_fence", response)
        self.assertIn("index_dst[word] >=", response)
        self.assertIn("coherent backing", response)
        self.assertIn("cg_soa_published_index_words", response)
        self.assertIn("cg_soa_published_value_words", response)
        self.assertIn("cg_soa_verified_index_words", response)

    def test_row_pointer_streams_use_page_local_physical_positions(self):
        for token in (
            "maa_const<int>(j_max - j_base, r5);",
            "maa_stream_load<int>(&rowstr[j_base], r4, r5, r1, t2);",
            "maa_stream_load<int>(&rowstr[j_base + 1], r4, r5, r1, t3);",
        ):
            self.assertEqual(self.source.count(token), 2)
        self.assertGreaterEqual(self.source.count("maa_const<int>(0, r4);"), 2)

    def test_virtual_mode_ordinary_spd_operands_are_page_rebased(self):
        # The virtual gather owns logical k positions, but the ordinary
        # colidx/a consumers are real 4K SPD tiles.  Their base address,
        # rather than their SPD index, carries the absolute page position.
        for token in (
            "maa_stream_load<int>(&colidx[page_base], r2, r3,",
            "maa_stream_load<float>(&a[page_base], r2, r3, r1, t5);",
        ):
            self.assertEqual(self.source.count(token), 4)
        self.assertNotIn("maa_const<int>(page_base, r2);", self.source)
        self.assertNotIn(
            "maa_const<int>(page_base + page_size, r3);", self.source
        )

    def test_terminal_closes_staging_and_requires_dynamic_use(self):
        self.assertIn("index_words == full_windows * TILE_SIZE", self.source)
        self.assertIn("value_words == full_windows * TILE_SIZE", self.source)
        self.assertIn(
            "verified_index_words == full_windows * TILE_SIZE", self.source
        )
        self.assertIn("full_windows > 0 && staged_counts_close", self.source)
        self.assertIn("CG_LOGICAL16_RMW_TERMINAL", self.source)

    def test_smoke_pins_p16_v32_and_physical_4k(self):
        for option in (
            "--maa_num_tile_elements=16384",
            "--maa_physical_tile_elements=4096",
            "--maa_num_offset_table_entries=16384",
            "--maa_num_offset_table_epoch_entries=16384",
            "--maa_soa_jit_predicate_active_credits=16",
            "--maa_soa_jit_active_value_owners=32",
        ):
            self.assertIn(option, self.runner)
        self.assertIn("IND_SoaJitTerminalCompletions", self.runner)
        self.assertIn("IND_SoaJitAliasesApplied", self.runner)
        self.assertIn("performance_promotable=0", self.runner)
        self.assertIn("speedup_claim=0", self.runner)

    def test_smoke_uses_immutable_selector_specific_checkpoints(self):
        self.assertIn("token_stream_ld legacy_4k", self.runner)
        self.assertIn("token_stream_ld residual_soa_jit", self.runner)
        self.assertIn("make_checkpoint legacy", self.runner)
        self.assertIn("make_checkpoint residual", self.runner)
        self.assertIn("selector_sha256.before", self.runner)
        self.assertIn("selector_sha256.after", self.runner)
        self.assertIn("cmp --silent", self.runner)


if __name__ == "__main__":
    unittest.main()
