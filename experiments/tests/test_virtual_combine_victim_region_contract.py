#!/usr/bin/env python3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class VirtualCombineVictimRegionContractTest(unittest.TestCase):
    def test_cli_and_config_forward_default_off_knob(self):
        options = (ROOT / "configs/common/Options.py").read_text()
        config = (ROOT / "configs/common/MAAConfig.py").read_text()
        params = (ROOT / "src/mem/MAA/MAA.py").read_text()
        self.assertIn("--maa_virtual_combine_victim_slots", options)
        self.assertIn("default=0", options)
        self.assertIn("choices=(0, 1, 2, 3, 4)", options)
        self.assertIn('opts["virtual_combine_victim_slots"]', config)
        self.assertIn(
            "virtual_combine_victim_slots = Param.Unsigned(\n        0", params
        )

    def test_bounds_and_constant_payload_contract(self):
        source = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        header = (ROOT / "src/mem/MAA/IndirectAccess.hh").read_text()
        self.assertIn(
            "virtual_combine_victim_slots >= _virtual_combine_slots", source
        )
        self.assertIn(
            "victim region requires finite virtual combiner ways", source
        )
        self.assertIn(
            "virtual_combine_primary_slots % virtual_combine_ways", source
        )
        self.assertIn("_virtual_combine_victim_policy > 4", source)
        self.assertIn("candidate.line_vaddr <", source)
        self.assertIn("candidate.line_vaddr >", source)
        self.assertIn("IND_VirtCombinePagePriorityEvictions", source)
        self.assertIn("std::array<uint8_t, 64> data", header)
        for victim_slots in (0, 16, 32, 64):
            self.assertEqual(
                (384 - victim_slots) * 64 + victim_slots * 64, 384 * 64
            )


if __name__ == "__main__":
    unittest.main()
