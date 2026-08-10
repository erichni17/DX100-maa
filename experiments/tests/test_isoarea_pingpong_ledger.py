import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "isoarea_ledger", ROOT / "experiments/analysis/isoarea_pingpong_ledger.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class IsoAreaLedgerTest(unittest.TestCase):
    def test_payload_arithmetic(self):
        ledger = MODULE.build_ledger()
        self.assertEqual(ledger["payload"]["visible_spd"]["bytes"], 524288)
        self.assertEqual(
            ledger["payload"]["private_logical_spd_runtime"]["bytes"],
            32768,
        )
        self.assertEqual(
            ledger["payload"]["total_maa_local_payload"]["bytes"], 557056
        )
        self.assertEqual(
            ledger["payload"]["private_logical_spd_runtime"]["storage_owner"],
            "LogicalSPDCacheRuntime",
        )

    def test_spd_metadata_excludes_runtime_owned_payload(self):
        metadata = MODULE.build_ledger()["metadata"]["spd_arrays"]
        self.assertEqual(metadata["tile_status_u8"], 32)
        self.assertEqual(metadata["element_finished_bool"], 32 * 4096)
        self.assertEqual(metadata["total_bytes"], 131392)

    def test_all_arms_have_identical_resources(self):
        arms = MODULE.build_ledger()["arms"]
        ignored = {"chunks", "chunk_elements", "payload_slots_used"}
        normalized = [
            {key: value for key, value in arm.items() if key not in ignored}
            for arm in arms.values()
        ]
        self.assertEqual(normalized[0], normalized[1])
        self.assertEqual(normalized[1], normalized[2])

    def test_bounded_table_byte_arithmetic(self):
        tables = MODULE.build_ledger()["bounded_table_arrays"]
        self.assertEqual(
            tables["indirect_offset_table"]["total_semantic_bytes"], 278528
        )
        self.assertEqual(
            tables["all_row_table_organizations"]["total_entries"], 32768
        )
        self.assertEqual(
            tables["all_row_table_organizations"]["total_core_array_bytes"],
            616734,
        )

    def test_source_contract(self):
        MODULE.verify_sources(ROOT)


if __name__ == "__main__":
    unittest.main()
