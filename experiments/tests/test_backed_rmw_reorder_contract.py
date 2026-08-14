import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class BackedRmwContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hh = (ROOT / "src/mem/MAA/IndirectAccess.hh").read_text()
        cls.cc = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        cls.api = (ROOT / "benchmarks/API/MAA_gem5.hpp").read_text()
        cls.bench = (
            ROOT / "benchmarks/API/test_backed_rmw_reorder.cpp"
        ).read_text()
        cls.runner = (
            ROOT / "experiments/scripts/run_backed_rmw_reorder_matrix.sh"
        ).read_text()

    def test_abi_is_guarded_existing_opcode(self) -> None:
        self.assertIn("maa_indirect_rmw_vector_backed", self.api)
        self.assertIn("OpcodeType::INDIR_RMW_VECTOR", self.api)
        self.assertIn("sizeof(MAAIndirectRmwRecord) == 32", self.api)
        self.assertNotIn("INDIR_RMW_VECTOR_BACKED", self.api)

    def test_storage_is_one_finite_4k_epoch(self) -> None:
        self.assertIn(
            "BackedRmwValueSlots =\n        BoundedDescriptorSpool::MaxActiveDescriptors",
            self.hh,
        )
        self.assertIn(
            "std::array<BackedRmwValueSlot, BackedRmwValueSlots>", self.hh
        )
        self.assertNotIn("std::vector<BackedRmwValueSlot>", self.hh)
        self.assertIn("diagnostic_value_slots=%u value_hwm=%u", self.cc)
        self.assertIn("BackedRmwResponseRecords = 64", self.hh)

    def test_generation_and_exact_write_ack_gate_completion(self) -> None:
        self.assertIn("generation != backed_rmw_generation", self.cc)
        self.assertIn(
            "backed_rmw_write_issues != backed_rmw_write_acks", self.cc
        )
        self.assertIn("MemCmd::WriteReq", self.cc)
        self.assertIn("event=backed_rmw_write_ack schema=1", self.cc)
        self.assertIn("generation_exact=1", self.cc)

    def test_publication_is_timed_and_claim_is_scoped(self) -> None:
        self.assertIn("Publication is deliberately inside the ROI", self.bench)
        self.assertIn("std::atomic_thread_fence", self.bench)
        self.assertIn("scope=api_mechanism", self.runner)
        self.assertIn("publication=timed_guest_cache_stores", self.runner)

    def test_backing_traffic_is_explicitly_accounted(self) -> None:
        self.assertIn("record_line_reads=%lu record_read_bytes=%lu", self.cc)
        self.assertIn("descriptor_write_bytes=%lu", self.cc)
        self.assertIn("descriptor_read_bytes=%lu", self.cc)
        self.assertIn("descriptor_write_acks=%u", self.cc)
        self.assertIn("descriptor_read_responses=%u", self.cc)

    def test_matrix_has_exact_four_arms_and_fixed_guest_abi(self) -> None:
        self.assertIn("-DTILE_SIZE=16384", self.runner)
        self.assertIn('-DPHYSICAL_PAGE="$tile"', self.runner)
        self.assertIn("run_arm native16 native 16384 16384 64", self.runner)
        self.assertIn("run_arm native4 native 4096 4096 32", self.runner)
        self.assertIn("run_arm backed16meta backed 4096 16384 64", self.runner)
        self.assertIn("run_arm backed4diag backed 4096 4096 32", self.runner)
        self.assertIn("output_hash", self.runner)
        self.assertIn("simTicks", self.runner)


if __name__ == "__main__":
    unittest.main()
