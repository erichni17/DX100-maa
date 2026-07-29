import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "summarize_maa_timeline.py"
SPEC = importlib.util.spec_from_file_location("summarize_maa_timeline", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SummarizeMaaTimelineTest(unittest.TestCase):
    def test_parses_intervals_and_cross_core_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "debug.log"
            log.write_text(
                "\n".join(
                    [
                        "10: global: S[0] Start [INSTR[core_id(1) opcode(STREAM_LD)]]",
                        "20: global: I[0] Start [INSTR[core_id(1) opcode(INDIR_LD_VIRTUAL)]]",
                        "30: global: S[0] End [INSTR[core_id(1) opcode(STREAM_LD)]]",
                        "30: global: S[0] Start [INSTR[core_id(0) opcode(STREAM_LD)]]",
                        "40: global: S[0] End [INSTR[core_id(0) opcode(STREAM_LD)]]",
                        "50: global: I[0] End [INSTR[core_id(1) opcode(INDIR_LD_VIRTUAL)]]",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            events = MODULE.parse_events("compact", log)

        self.assertEqual(len(events), 3)
        self.assertEqual(events[0].duration_ticks, 20)
        self.assertEqual(MODULE.overlap_ticks(events[0], events[1]), 10)
        self.assertEqual(MODULE.overlap_ticks(events[1], events[2]), 10)
        self.assertEqual(MODULE.union_ticks(events), 40)

    def test_rejects_unmatched_start(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "debug.log"
            log.write_text(
                "10: global: I[0] Start [INSTR[core_id(0) opcode(TEST)]]\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit):
                MODULE.parse_events("bad", log)


if __name__ == "__main__":
    unittest.main()
