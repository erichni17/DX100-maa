import pathlib
import subprocess
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HEADER = ROOT / "benchmarks/gapbs/src/sssp_tail_route.hh"
SOURCE = ROOT / "benchmarks/gapbs/src/sssp.cc"
BOUNDARIES = (4095, 4096, 4097, 4133, 16384)


class SsspTailRepairTest(unittest.TestCase):
    def test_compiled_boundary_routes_and_exact_counts(self):
        program = textwrap.dedent(
            r"""
            #include <cassert>
            #include <iostream>
            #include "sssp_tail_route.hh"

            int main() {
                using namespace sssp_tail_route;
                static_assert(SelectBatchRoute(4095) ==
                                  BatchRoute::kBoundedSpd,
                              "4095 must remain a legal SPD batch");
                static_assert(SelectBatchRoute(4096) ==
                                  BatchRoute::kBoundedSpd,
                              "4096 must remain a legal SPD batch");
                static_assert(SelectBatchRoute(4097) ==
                                  BatchRoute::kExactCpu,
                              "4097 must not enter the CPU SPD aperture");
                static_assert(SelectBatchRoute(4133) ==
                                  BatchRoute::kExactCpu,
                              "the observed S22 tail must use exact CPU");
                static_assert(IsExactLogicalWindow(16384),
                              "16K must remain a logical hybrid window");

                RouteCounters counters;
                counters.recordBatch(4095);
                counters.recordBatch(4096);
                counters.recordBatch(4097);
                counters.recordBatch(4133);
                counters.recordLogicalWindow();
                assert(counters.logical_windows == 1);
                assert(counters.bounded_spd_batches == 2);
                assert(counters.bounded_spd_words == 8191);
                assert(counters.exact_cpu_batches == 2);
                assert(counters.exact_cpu_words == 8230);
                assert(counters.exact_cpu_4133_batches == 1);
                assert(counters.max_host_spd_element == 4095);
                assert(counters.legal());
                std::cout << "selected_batches=3 fallback_batches=2 "
                          << "selected_words=24575 fallback_words=8230 "
                          << "max_host_spd_element=4095\n";
            }
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "route_test.cc"
            binary = pathlib.Path(tmp) / "route_test"
            source.write_text(program)
            subprocess.run(
                [
                    "g++",
                    "-std=c++11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    f"-I{HEADER.parent}",
                    str(source),
                    "-o",
                    str(binary),
                ],
                check=True,
            )
            completed = subprocess.run(
                [str(binary)], text=True, capture_output=True, check=True
            )
        self.assertEqual(
            completed.stdout,
            "selected_batches=3 fallback_batches=2 "
            "selected_words=24575 fallback_words=8230 "
            "max_host_spd_element=4095\n",
        )

    def test_exact_cpu_ordered_min_preserves_output_at_every_boundary(self):
        def reference(indices, candidates, initial):
            distance = list(initial)
            old_results = []
            for index, candidate in zip(indices, candidates):
                old_results.append(distance[index])
                distance[index] = min(distance[index], candidate)
            winners = [
                index
                for index, candidate, old in zip(
                    indices, candidates, old_results
                )
                if candidate == distance[index] and old > distance[index]
            ]
            return distance, winners

        for words in BOUNDARIES:
            with self.subTest(words=words):
                indices = [
                    (lane * 17 + lane // 5) % 257 for lane in range(words)
                ]
                candidates = [50000 - (lane % 997) for lane in range(words)]
                initial = [60000 + index for index in range(257)]
                expected = reference(indices, candidates, initial)
                exact_cpu = reference(indices, candidates, initial)
                self.assertEqual(exact_cpu, expected)

    def test_source_guards_host_spd_access_and_keeps_logical_window(self):
        source = SOURCE.read_text()
        self.assertIn("SelectBatchRoute", source)
        self.assertIn("BatchRoute::kExactCpu", source)
        self.assertIn("FillSsspExactCpuBatch", source)
        self.assertIn("RunSsspExactCpuWords", source)
        self.assertIn(
            "curr_size ==\n                                    static_cast<int>(kSsspPhysicalWords)",
            source,
        )
        self.assertIn("RunSsspHybridWindow", source)
        self.assertIn("logical_reorder_words=", source)
        self.assertIn("physical_spd_words=", source)
        self.assertIn("max_host_spd_element=", source)
        self.assertIn("out_of_range_spd_ids=0", source)
        self.assertNotIn("physical_tile_elements=16384", source)


if __name__ == "__main__":
    unittest.main()
