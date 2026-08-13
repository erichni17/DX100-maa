#!/usr/bin/env python3
"""Static wiring contract for zero-state UMT issue observability."""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HEADER = (ROOT / "src/mem/LANLMAA/lanl_maa.hh").read_text(encoding="utf-8")
SOURCE = (ROOT / "src/mem/LANLMAA/lanl_maa.cc").read_text(encoding="utf-8")
STATE = (ROOT / "src/mem/LANLMAA/UmtOrderedWaveStreamState.hh").read_text(
    encoding="utf-8"
)
ABI = (ROOT / "src/mem/LANLMAA/DescriptorABI.md").read_text(encoding="utf-8")

SNAPSHOT_COUNTERS = {
    "descriptorUmtStateBankReadConflictCycles": "bankConflicts()",
    "descriptorUmtStateWritebackStallCycles": "writebackStalls()",
}
TRANSIENT_COUNTER = "descriptorUmtStateDividerNoLaneCycles"


class UmtIssueObservabilityTest(unittest.TestCase):
    def test_snapshot_counters_are_declared_registered_and_recorded_once(self):
        record_body = re.search(
            r"LANLMAA::recordUmtOrderedWaveStreamStats\(\)\n\{(.*?)\n\}",
            SOURCE,
            re.DOTALL,
        )
        self.assertIsNotNone(record_body)
        for counter, getter in SNAPSHOT_COUNTERS.items():
            with self.subTest(counter=counter):
                self.assertEqual(
                    HEADER.count(f"statistics::Scalar {counter};"), 1
                )
                self.assertEqual(SOURCE.count(f"ADD_STAT({counter},"), 1)
                self.assertEqual(record_body.group(1).count(counter), 1)
                self.assertEqual(record_body.group(1).count(getter), 1)

    def test_divider_counter_uses_only_transient_cycle_result(self):
        self.assertEqual(
            HEADER.count(f"statistics::Scalar {TRANSIENT_COUNTER};"), 1
        )
        self.assertEqual(SOURCE.count(f"ADD_STAT({TRANSIENT_COUNTER},"), 1)
        self.assertEqual(SOURCE.count(f"++stats.{TRANSIENT_COUNTER};"), 1)
        self.assertIn("bool dividerNoLaneCycle = false;", STATE)
        self.assertNotIn("dividerNoLaneCycles", STATE)

    def test_all_counters_are_cycles_and_boundary_is_documented(self):
        for counter in (*SNAPSHOT_COUNTERS, TRANSIENT_COUNTER):
            registration = re.search(
                rf"ADD_STAT\({counter},\n"
                r"\s+statistics::units::Cycle::get\(\),",
                SOURCE,
            )
            self.assertIsNotNone(registration, counter)
            self.assertIn(f"`{counter}`", ABI)
        self.assertIn("simulation instrumentation", ABI)
        self.assertRegex(ABI, r"not\s+architecturally visible or persistent")
        self.assertIn("add no bits", ABI)


if __name__ == "__main__":
    unittest.main()
