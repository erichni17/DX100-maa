#!/usr/bin/env python3

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class CGBoundedVirtualContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "benchmarks/NAS/cg/cg.cpp").read_text()
        cls.makefile = (ROOT / "benchmarks/NAS/cg/Makefile").read_text()

    def test_target_enables_bounded_direct_index_path(self):
        self.assertIn("%_maa_16K_bounded:", self.makefile)
        self.assertIn("-DMAA_BOUNDED_VIRTUAL_GATHER", self.makefile)

    def test_only_complete_logical_tiles_use_direct_index(self):
        self.assertGreaterEqual(
            self.source.count("if (gather_size == TILE_SIZE)"), 2
        )
        self.assertGreaterEqual(
            self.source.count("maa_indirect_load_virtual_index<float>"), 2
        )
        self.assertGreaterEqual(
            self.source.count("maa_const<int>(k_base + gather_size, r3)"), 2
        )
        self.assertGreaterEqual(
            self.source.count("if (gather_size != TILE_SIZE)"), 2
        )

    def test_partial_tail_keeps_staged_virtual_fallback(self):
        self.assertGreaterEqual(
            self.source.count("maa_indirect_load_virtual<float>"), 2
        )

    def test_registered_backing_includes_isolated_descriptor_slots(self):
        self.assertIn("virtual_descriptor_spool_units = 4", self.source)
        self.assertIn("virtual_descriptor_spool_words", self.source)
        self.assertIn("add_mem_region(virtual_gather_storage", self.source)


if __name__ == "__main__":
    unittest.main()
