#!/usr/bin/env python3
"""Keep the storage audit tied to the constants and dataflow it reports."""
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
AUDIT = (
    ROOT / "experiments/analysis/virtualization_storage_audit_2026_08_10.json"
)


class VirtualizationStorageAuditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = json.loads(AUDIT.read_text())
        cls.indirect = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        cls.spd = (ROOT / "src/mem/MAA/SPD.cc").read_text()
        cls.spool = (
            ROOT / "src/mem/MAA/BoundedDescriptorSpool.hh"
        ).read_text()
        cls.runner = (
            ROOT / "experiments/scripts/run_virtual_tile_consumer_case.sh"
        ).read_text()

    def test_source_constants_match_audit(self):
        c = self.audit["constants"]
        self.assertEqual(c["logical_tile_elements"], 16384)
        self.assertEqual(c["hybrid_physical_tile_elements"], 4096)
        self.assertIn("--maa_num_tile_elements=16384", self.runner)
        self.assertIn("physical_tile_elements) * sizeof(uint32_t)", self.spd)
        self.assertIn("MaxActiveDescriptors = 4096", self.spool)
        self.assertIn("MaxLogicalDescriptors = 16384", self.spool)
        self.assertIn("DescriptorBytes = 6", self.spool)

    def test_liveness_has_explicit_source_mechanisms(self):
        self.assertIn("direct_index_words.erase(word)", self.indirect)
        self.assertIn(
            "DirectIndexDiscardReason::DescriptorInserted", self.indirect
        )
        self.assertIn("insertVirtualCombineWord", self.indirect)
        self.assertIn("createRetirementWrite", self.indirect)
        self.assertIn("backingWordAddr(itr)", self.indirect)
        self.assertTrue(
            self.audit["paths"]["transparent_hybrid4"][
                "result_persists_in_shared_backing"
            ]
        )

    def test_hybrid_counts_both_fp64_staging_pages(self):
        hybrid = self.audit["paths"]["transparent_hybrid4"]
        page_bytes = (
            self.audit["constants"]["hybrid_physical_tile_elements"]
            * self.audit["constants"]["fp64_bytes"]
        )
        self.assertEqual(hybrid["spd_input_page_bytes"], page_bytes)
        self.assertEqual(hybrid["spd_output_page_bytes"], page_bytes)
        self.assertEqual(
            hybrid["simultaneous_spd_staging_bytes"], 2 * page_bytes
        )
        self.assertFalse(
            hybrid["application_destination_is_virtualization_overhead"]
        )

    def test_bounded_spool_formula(self):
        p = self.audit["paths"]["bounded4"]
        self.assertEqual(
            p["external_descriptor_worst_case_bytes"],
            3 * ((4096 * 6 + 63) // 64) * 64,
        )
        self.assertIn(
            "external descriptors are timing-visible backing",
            self.audit["finite_on_chip_or_private_state"]["descriptor_spool"],
        )


if __name__ == "__main__":
    unittest.main()
