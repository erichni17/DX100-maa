import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "benchmarks/gapbs/src/sssp.cc"
HELPER = ROOT / "benchmarks/gapbs/src/sssp_coherent_fallback.hh"
HELPER_TEST = ROOT / "tests/sssp_coherent_fallback_test.cpp"
RUNNER = ROOT / "experiments/scripts/run_sssp_coherent_fallback_reproducer.sh"


class SsspCoherentFallbackContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()
        cls.helper = HELPER.read_text()
        cls.helper_test = HELPER_TEST.read_text()
        cls.runner = RUNNER.read_text()

    def test_helper_executes_boundary_cursor_counter_and_cycle_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = pathlib.Path(tmp) / "sssp_coherent_fallback_test"
            subprocess.run(
                [
                    "g++",
                    "-I",
                    str(HELPER.parent),
                    "-std=c++11",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(HELPER_TEST),
                    "-o",
                    str(binary),
                ],
                check=True,
                cwd=ROOT,
            )
            result = subprocess.run(
                [str(binary)], check=True, capture_output=True, text=True
            )
        self.assertEqual(
            result.stdout.strip(), "SSSP_COHERENT_FALLBACK_HELPER_PASS"
        )
        self.assertIn("page < 20", self.helper_test)
        self.assertIn("page % 4", self.helper_test)

    def test_unsigned_remaining_subtraction_is_fail_closed(self):
        guard = self.source.index(
            "if (hybrid_observed_words > hybrid_chunk_words)"
        )
        subtraction = self.source.index(
            "hybrid_chunk_words - hybrid_observed_words", guard
        )
        route = self.source.index("SelectRemainingRoute(", subtraction)
        self.assertLess(guard, subtraction)
        self.assertLess(subtraction, route)

    def test_partial_tail_reconstructs_before_any_partial_maa_tile(self):
        loop = self.source.index("const size_t hybrid_remaining_words")
        tail = self.source.index("RunSsspCoherentTail(", loop)
        range_loop = self.source.index("maa_range_loop<SGOffset>", loop)
        self.assertLess(tail, range_loop)
        tail_helper = self.source[
            self.source.index("RunSsspCoherentTail(") : self.source.index(
                "RunSsspHybridWindow("
            )
        ]
        self.assertIn("ConsumeCursorWords(", tail_helper)
        self.assertIn("OrderedMinReplay(", tail_helper)
        self.assertNotIn("get_cacheable_tile_pointer", tail_helper)
        self.assertIn("words >= kSsspPhysicalWords", tail_helper)

    def test_full_fallback_publishes_three_backing_pages_then_consumes(self):
        start = self.source.index("PublishAndConsumeSsspFallbackPage(")
        end = self.source.index("RunSsspCoherentTail(", start)
        block = self.source[start:end]
        self.assertEqual(block.count("PublishSsspFallbackBackingPage("), 3)
        self.assertIn("sssp_hybrid_indices[tid]", block)
        self.assertIn("sssp_hybrid_values[tid]", block)
        self.assertIn("sssp_hybrid_predicates[tid]", block)
        consume = block.index("for (size_t lane = begin; lane < end; ++lane)")
        last_publish = block.rindex("PublishSsspFallbackBackingPage(")
        self.assertLess(last_publish, consume)
        self.assertIn("recordFallbackPage()", block)

    def test_dead_completion_tiles_are_waited_before_sequential_reuse(self):
        publisher_start = self.source.index("PublishSsspFallbackBackingPage(")
        publisher_end = self.source.index(
            "PublishAndConsumeSsspFallbackPage(", publisher_start
        )
        publisher = self.source[publisher_start:publisher_end]
        issue = publisher.index(
            "maa_publish_spd_page_logical16_response_bearing<T>("
        )
        wait = publisher.index("wait_ready(completion_tile);", issue)
        response = publisher.index("recordPublicationResponse();", wait)
        self.assertLess(issue, wait)
        self.assertLess(wait, response)

        consumer_start = self.source.index(
            "PublishAndConsumeSsspFallbackPage(\n                                    tid"
        )
        next_range = self.source.index(
            "} while (curr_size > 0);", consumer_start
        )
        consumer = self.source[consumer_start:next_range]
        self.assertIn(
            "tileu,\n                                    tile2", consumer
        )
        self.assertIn("AdvanceCursorWords(", consumer)

    def test_predicate_backing_is_restored_before_later_logical_window(self):
        fallback = self.source[
            self.source.index(
                "PublishAndConsumeSsspFallbackPage("
            ) : self.source.index("RunSsspCoherentTail(")
        ]
        fill = fallback.index("fill(sssp_hybrid_predicates")
        fence = fallback.index("atomic_thread_fence", fill)
        record = fallback.index("recordFallbackPage();", fence)
        self.assertLess(fill, fence)
        self.assertLess(fence, record)

    def test_terminal_counter_closure_is_acceptance_authority(self):
        for text in (
            "host_spd_reads += fallback.host_spd_reads",
            "fallback_publication_issue_pages ==",
            "fallback_publication_response_pages",
            "legacy_words == fallback_consumed_words",
            '<< " response_closure="',
            '<< " counts_close="',
            "if (!counts_close)",
        ):
            self.assertIn(text, self.source)
        self.assertNotIn('" host_spd_reads=0', self.source)

    def test_runner_is_immutable_archived_candidate_only_gate(self):
        for text in (
            "gem5-703c1e1d756ada75306e7ed941f3dad967370cd4f224c092430b5b2b5fb0f1a5.opt",
            "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753",
            "source_to_4096_middle_degree4",
            "expected_fallback_pages=4",
            "native_arms=0",
            "wall_timeout=none",
            "artifacts.before.sha256",
            "artifacts.after.sha256",
            'cmp -s "$out/artifacts.before.sha256"',
            "SSSP_COHERENT_FALLBACK_REPRODUCER_PASS",
        ):
            self.assertIn(text, self.runner)
        self.assertNotIn("timeout ", self.runner)
        self.assertIn("refusing existing output", self.runner)
        self.assertIn(
            "refusing evidence run from a dirty source tree", self.runner
        )

    def test_zero_new_hardware_payload_and_eight_tile_geometry(self):
        self.assertEqual(
            self.source.count("alignas(kSsspLogicalBytes) static"), 4
        )
        self.assertIn("new_dedicated_payload_bytes=0", self.source)
        self.assertIn("hidden_logical_spd_bytes=0", self.source)
        self.assertIn("-DNUM_TILES_PER_CORE=8", self.runner)
        self.assertIn("num_tiles_per_core=8", self.runner)
        self.assertNotIn("sssp_hybrid_fallback_payload", self.source)


if __name__ == "__main__":
    unittest.main()
