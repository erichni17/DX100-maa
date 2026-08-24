import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HEADER = ROOT / "benchmarks/gapbs/src/sssp_tail_route.hh"
REPLAY_HEADER = ROOT / "benchmarks/gapbs/src/sssp_tail_replay.hh"
SOURCE = ROOT / "benchmarks/gapbs/src/sssp.cc"
CPP_TEST = ROOT / "tests/sssp_tail_replay_test.cpp"


class SsspTailRepairTest(unittest.TestCase):
    def test_compiled_production_cursor_replay_and_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = pathlib.Path(tmp) / "route_test"
            subprocess.run(
                [
                    "g++",
                    "-std=c++11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    f"-I{HEADER.parent}",
                    str(CPP_TEST),
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
            "SSSP_TAIL_REPLAY_TEST_PASS boundaries=8 cursor=closed "
            "published_replay=ordered duplicate_pages=preserved\n",
        )

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
        self.assertIn("sssp_tail_replay::ConsumeCursorWords", source)
        self.assertIn("sssp_tail_replay::OrderedMinReplay", source)
        self.assertIn("sssp_tail_replay::ReplayPublishedPages", source)
        self.assertIn("logical_reorder_words=", source)
        self.assertIn("physical_spd_words=", source)
        self.assertIn("max_host_spd_element=", source)
        self.assertIn("illegal_host_spd_attempts", source)
        self.assertNotIn("out_of_range_spd_ids=0", source)
        self.assertEqual(
            REPLAY_HEADER.read_text().count("ConsumeCursorWords"), 2
        )
        self.assertNotIn("physical_tile_elements=16384", source)


if __name__ == "__main__":
    unittest.main()
