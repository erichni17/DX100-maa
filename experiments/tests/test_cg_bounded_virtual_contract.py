#!/usr/bin/env python3

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class CGBoundedVirtualContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "benchmarks/NAS/cg/cg.cpp").read_text()
        cls.makefile = (ROOT / "benchmarks/NAS/cg/Makefile").read_text()
        cls.maa = (ROOT / "src/mem/MAA/MAA.cc").read_text()

    def test_target_enables_bounded_direct_index_path(self):
        self.assertIn("%_maa_16K_bounded:", self.makefile)
        self.assertIn("-DMAA_BOUNDED_VIRTUAL_GATHER", self.makefile)
        self.assertIn("-DMAA_CONSUMER_TILE_SIZE=4096", self.makefile)

    def test_complete_logical_tiles_use_one_direct_index_gather(self):
        self.assertGreaterEqual(
            self.source.count("if (gather_size == TILE_SIZE)"), 2
        )
        self.assertGreaterEqual(
            self.source.count("maa_indirect_load_virtual_index<float>"), 2
        )
        self.assertGreaterEqual(
            self.source.count("maa_const<int>(k_base + gather_size, r3)"), 2
        )

    def test_consumers_are_explicitly_paged(self):
        self.assertGreaterEqual(
            self.source.count("page_offset += MAA_CONSUMER_TILE_SIZE"),
            2,
        )
        self.assertGreaterEqual(self.source.count("const int page_size"), 2)
        self.assertIn('<< " consumer=" << MAA_CONSUMER_TILE_SIZE', self.source)

    def test_partial_tail_uses_physical_native_pages(self):
        self.assertGreaterEqual(
            self.source.count("maa_stream_load<int>(colidx, r2, r3, r1, t6)"),
            4,
        )
        self.assertGreaterEqual(
            self.source.count("maa_indirect_load<float>(p, t6, t4)"), 2
        )
        self.assertGreaterEqual(
            self.source.count("maa_indirect_load<float>(z, t6, t4)"), 2
        )

    def test_row_work_is_bounded_by_consumer_capacity(self):
        self.assertIn(
            "const int row_tile_size = MAA_CONSUMER_TILE_SIZE", self.source
        )
        self.assertGreaterEqual(
            self.source.count("j_base += row_tile_size"), 2
        )

    def test_ordinary_spd_producers_use_physical_capacity(self):
        self.assertIn(
            "num_request_table_entries_per_address,\n"
            "                                      physical_tile_elements, this",
            self.maa,
        )
        self.assertIn("p.num_ALU_lanes, physical_tile_elements", self.maa)
        self.assertIn(
            "rangeUnits[i].allocate(physical_tile_elements, this, i)", self.maa
        )

    def test_registered_backing_includes_isolated_descriptor_slots(self):
        self.assertIn("virtual_descriptor_spool_units = 4", self.source)
        self.assertIn("virtual_descriptor_spool_words", self.source)
        self.assertIn("add_mem_region(virtual_gather_storage", self.source)

    def test_layout_reports_compiled_memory_size(self):
        self.assertIn('<< " maa_mem_size=" << MEM_SIZE', self.source)


if __name__ == "__main__":
    unittest.main()
