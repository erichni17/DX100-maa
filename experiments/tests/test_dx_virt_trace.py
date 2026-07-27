#!/usr/bin/env python3

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dx_virt_trace.py"
SPEC = importlib.util.spec_from_file_location("dx_virt_trace", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_case(root: Path, completions=4):
    (root / "run").mkdir()
    (root / "manifest.txt").write_text(
        "elements=64\ncase=test\n", encoding="utf-8"
    )
    (root / "result.tsv").write_text(
        "elements\tspd_read_cycles\n64\t0\n", encoding="utf-8"
    )
    lines = [
        "---------- Begin Simulation Statistics ----------",
        "simTicks 1000",
        "simInsts 10",
        "system.maa.I0_IND_VirtIndexLineReads 4",
        "system.maa.I0_IND_VirtIndexLineHighWater 2",
        "system.maa.I0_IND_VirtIndexWords 64",
        "system.maa.I0_IND_VirtIndexWordHighWater 16",
        "system.maa.I0_IND_VirtBuildRounds 2",
        "system.maa.I0_IND_NumRTFull 1",
        "system.maa.I0_IND_LoadsMemAccessing 12",
        "system.maa.I0_IND_VirtResponseSlotHighWater 4",
        "system.maa.I0_IND_VirtResponseWordHighWater 32",
        "system.maa.I0_IND_VirtWriteIssues 4",
        f"system.maa.I0_IND_VirtWriteCompletions {completions}",
        "system.maa.I0_IND_VirtFullLineWrites 4",
        "system.maa.I0_IND_VirtPipelineCyclesSourceOnly 10",
        "system.maa.I0_IND_VirtPipelineCyclesWriteOnly 20",
        "system.maa.I0_IND_VirtPipelineCyclesOverlap 70",
        "---------- End Simulation Statistics ----------",
        "---------- Begin Simulation Statistics ----------",
        "simTicks 999999",
        "---------- End Simulation Statistics ----------",
    ]
    (root / "run/stats.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


class TraceTests(unittest.TestCase):
    def test_first_roi_timeline_and_overlap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_case(root)
            summary = MODULE.build_summary(root)
        self.assertEqual(summary["performance"]["simTicks"], 1000)
        self.assertEqual(summary["raw_counters"]["source_reads"], 8)
        self.assertEqual(summary["timeline"][1]["row_table_full_events"], 1)
        self.assertAlmostEqual(summary["pipeline"]["overlap_fraction"], 0.7)

    def test_unbalanced_retirement_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_case(root, completions=3)
            with self.assertRaisesRegex(MODULE.TraceError, "writes_balanced"):
                MODULE.build_summary(root)

    def test_incomplete_stats_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run").mkdir()
            (root / "run/stats.txt").write_text(
                "---------- Begin Simulation Statistics ----------\n"
                "simTicks 1\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MODULE.TraceError, "incomplete"):
                MODULE.parse_first_stats(root / "run/stats.txt")


if __name__ == "__main__":
    unittest.main()
