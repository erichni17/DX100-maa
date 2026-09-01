import pathlib
import struct
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class SsspOldResultHybridContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "benchmarks/gapbs/src/sssp.cc").read_text()
        cls.makefile = (ROOT / "benchmarks/gapbs/Makefile").read_text()
        cls.runner = (
            ROOT / "experiments/scripts/run_sssp_old_result_hybrid_small.sh"
        ).read_text()

    def test_opt_in_target_preserves_legacy_default(self):
        target = "sssp_maa_2G_old_result_hybrid_fp:"
        self.assertEqual(self.makefile.count(target), 1)
        candidate_rule = self.makefile[self.makefile.index(target) :]
        self.assertIn("-DSSSP_OLD_RESULT_HYBRID=1", candidate_rule)
        legacy_rule = self.makefile[
            self.makefile.index("%_maa:") : self.makefile.index("%_maa_1K:")
        ]
        self.assertNotIn("SSSP_OLD_RESULT_HYBRID", legacy_rule)
        self.assertIn("maa_indirect_rmw_vector<WeightT>(", self.source)
        self.assertIn("Operation_t::MIN_OP, -1, tilei", self.source)

    def test_integer_min_equivalence_is_fail_closed(self):
        self.assertIn("numeric_limits<float>::is_iec559", self.source)
        self.assertIn("candidate > kDistInf", self.source)
        self.assertIn("dist[wn.v] < 0 || dist[wn.v] > kDistInf", self.source)
        self.assertIn("hybrid_active_sources[wn.v]", self.source)
        self.assertIn("hybrid_chunk_admission.observeDestination", self.source)
        self.assertIn("hybrid_chunk_admission.safe", self.source)
        self.assertIn("wn.w <= 0", self.source)
        self.assertIn(
            "routed_windows + unsafe_eligible_windows == eligible_windows",
            self.source,
        )
        self.assertNotIn("hybrid_iteration_safe", self.source)

    def test_four_physical_pages_precede_ordered_old_result(self):
        chunk = self.source[
            self.source.index(
                "SsspHybridChunkFrontierWords("
            ) : self.source.index("PublishSsspHybridPage(")
        ]
        self.assertIn("NUM_CORES * 4096", chunk)
        self.assertNotIn("NUM_CORES * 8192", chunk)
        self.assertNotIn("NUM_CORES * 16384", chunk)
        publish = self.source.index("PublishSsspHybridPage(")
        old_result = self.source.index(
            "maa_indirect_rmw_vector_soa_jit_old_result(", publish
        )
        completion = self.source.index(
            "wait_ready(completion_tile);", old_result
        )
        frontier = self.source.index(
            "sssp_hybrid_old_results[tid][lane] > final_distance", completion
        )
        self.assertLess(publish, old_result)
        self.assertLess(old_result, completion)
        self.assertLess(completion, frontier)
        self.assertIn("logical_page == 3", self.source)
        self.assertIn("curr_size !=", self.source)
        self.assertIn("kSsspPhysicalWords", self.source)
        self.assertIn("index_publish_pages == routed_windows * 4", self.source)
        self.assertIn("value_publish_pages == routed_windows * 4", self.source)
        self.assertIn("duplicate_order=legacy_physical_pages", self.source)

    def test_ordered_old_results_reproduce_legacy_page_winners(self):
        initial = {7: 100, 9: 80}
        indices = [7, 9, 7, 7, 9]
        candidates = [70, 60, 50, 65, 55]
        current = dict(initial)
        old_results = []
        for index, candidate in zip(indices, candidates):
            old_results.append(current[index])
            current[index] = min(current[index], candidate)

        page_final = current
        legacy = [
            candidate == page_final[index] and old > page_final[index]
            for index, candidate, old in zip(indices, candidates, old_results)
        ]
        reconstructed_final = {}
        for index, candidate, old in reversed(
            list(zip(indices, candidates, old_results))
        ):
            reconstructed_final.setdefault(index, min(old, candidate))
        reconstructed = [
            candidate == reconstructed_final[index]
            and old > reconstructed_final[index]
            for index, candidate, old in zip(indices, candidates, old_results)
        ]
        self.assertEqual(reconstructed, legacy)
        self.assertEqual(reconstructed, [False, False, True, False, True])

    def test_integer_bits_are_fp_ordered_over_admitted_domain(self):
        samples = [0, 1, 2, 4096, (2**31 - 1) // 2]
        as_float = [
            struct.unpack("!f", struct.pack("!I", value))[0]
            for value in samples
        ]
        self.assertEqual(
            sorted(range(len(samples)), key=samples.__getitem__),
            sorted(range(len(samples)), key=as_float.__getitem__),
        )

    def test_coherent_spans_replace_host_spd_reads_on_routed_path(self):
        helper_start = self.source.index("RunSsspHybridWindow(")
        helper_end = self.source.index("#endif", helper_start)
        helper = self.source[helper_start:helper_end]
        self.assertIn("sssp_hybrid_indices", helper)
        self.assertIn("sssp_hybrid_values", helper)
        self.assertIn("sssp_hybrid_predicates", helper)
        self.assertIn("sssp_hybrid_old_results", helper)
        self.assertNotIn("get_cacheable_tile_pointer", helper)
        self.assertIn('<< " host_spd_reads=" << host_spd_reads', self.source)
        self.assertIn('<< " hidden_result_payload_bytes=0"', self.source)
        self.assertIn("physical_spd_words=", self.source)
        self.assertIn("row_table_slices=32", self.source)

    def test_candidate_gate_is_exact_and_candidate_only(self):
        self.assertNotIn("timeout ", self.runner)
        self.assertIn("native_arms=0", self.runner)
        self.assertIn("full_graph=false", self.runner)
        self.assertIn("--maa_physical_tile_elements=4096", self.runner)
        self.assertIn("--maa_num_tile_elements=16384", self.runner)
        self.assertIn("--mem-channels=2", self.runner)
        self.assertIn("--maa_num_indirect_units_per_maa=4", self.runner)
        self.assertIn("--maa_num_initial_row_table_slices=32", self.runner)
        self.assertIn("SSSP_CHUNK_ADMISSION_VARIANT", self.runner)
        self.assertIn("expected_routed=4", self.runner)
        self.assertIn("expected_routed_windows=%s", self.runner)
        self.assertIn("hash_a=a0531a7ddb9387df", self.runner)
        self.assertIn("hash_b=39f1ea63bc8817e8", self.runner)
        self.assertIn("result=PASS", self.runner)
        self.assertNotIn("sssp_maa_1K", self.runner)

    def test_host_oracle_fingerprints_the_base_delta_step(self):
        base_start = self.source.index("pvector<WeightT> DeltaStep(")
        base_end = self.source.index("void PrintSSSPStats", base_start)
        base = self.source[base_start:base_end]
        self.assertIn("#if defined(SSSP_FP_ENABLE) && !defined(GEM5)", base)
        self.assertEqual(
            base.count("PrintSSSPFingerprint(g, source, dist);"), 1
        )
        self.assertLess(
            base.index("PrintSSSPFingerprint(g, source, dist);"),
            base.index("return dist;"),
        )

    def test_successor_is_not_the_rejected_pre_fallback_consumer_source(self):
        baseline = subprocess.run(
            ["git", "show", "e690867f:benchmarks/gapbs/src/sssp.cc"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        self.assertNotEqual(self.source.encode(), baseline)
        self.assertIn('#include "sssp_coherent_fallback.hh"', self.source)
        self.assertIn("PublishAndConsumeSsspFallbackPage(", self.source)

    def test_small_gate_proves_routed_path_has_no_fallback_or_host_spd_reads(
        self,
    ):
        for exact in (
            "expected_fallback_pages",
            "expected_fallback_issue_pages",
            "expected_fallback_words",
            "coherent_tail_words=0",
            "host_spd_reads=0",
            "max_host_spd_element=-1",
            "illegal_host_spd_line_starts=0",
            "response_closure=1",
            "counts_close=1",
        ):
            self.assertIn(exact, self.runner)
        self.assertIn("expected_unsafe=0", self.runner)
        self.assertIn("active_source)", self.runner)
        self.assertIn("cross_owner)", self.runner)
        self.assertIn("terminal_value", self.runner)
        self.assertIn("helper_sha256", self.runner)


if __name__ == "__main__":
    unittest.main()
