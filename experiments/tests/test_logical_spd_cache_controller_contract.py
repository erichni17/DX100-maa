#!/usr/bin/env python3
"""Static contract checks for the standalone bounded SPD-cache core."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEADER_PATH = ROOT / "src/mem/MAA/LogicalSPDCacheController.hh"
TEST_PATH = ROOT / "tests/maa/logical_spd_cache_controller_test.cc"
RUNNER_PATH = (
    ROOT / "experiments/scripts/run_logical_spd_cache_controller_unit.sh"
)
NOTE_PATH = (
    ROOT / "experiments/analysis/logical_spd_cache_controller_2026-08-02.md"
)


class LogicalSpdCacheControllerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.header = HEADER_PATH.read_text(encoding="utf-8")

    def test_core_is_compile_time_bounded_and_payload_free(self) -> None:
        self.assertIn("LogicalDescriptors = 2", self.header)
        self.assertIn("PhysicalSlots = 2", self.header)
        self.assertIn("MissQueueEntries = 4", self.header)
        self.assertIn("LeaseEntries = 4", self.header)
        for array in (
            "std::array<Descriptor, LogicalDescriptors>",
            "std::array<Slot, PhysicalSlots>",
            "std::array<PageIdentity, MissQueueEntries>",
            "std::array<LeaseRecord, LeaseEntries>",
        ):
            self.assertIn(array, self.header)
        for forbidden in (
            "std::vector",
            "std::deque",
            "std::list",
            "std::map",
            "std::unordered",
            "new ",
            "malloc",
            "uint8_t data",
        ):
            self.assertNotIn(forbidden, self.header)

    def test_every_external_page_name_is_generation_tagged(self) -> None:
        identity = self.header[
            self.header.index("struct PageIdentity") : self.header.index(
                "struct Lease"
            )
        ]
        self.assertIn("uint16_t logical", identity)
        self.assertIn("uint16_t page", identity)
        self.assertIn("Generation generation", identity)
        for operation in (
            "notifyPageReady(const PageIdentity &page)",
            "access(const PageIdentity &page)",
            "completeFill(uint16_t slotIndex, const PageIdentity &page)",
            "completeWriteback(uint16_t slotIndex, const PageIdentity &page)",
            "pin(const PageIdentity &page)",
        ):
            self.assertIn(operation, self.header)
        self.assertIn("FillReleasedObsolete", self.header)
        self.assertIn("GenerationExhausted", self.header)

    def test_explicit_lease_and_writeback_completion_contracts(self) -> None:
        for phase in ("Filling", "Clean", "Dirty", "Writeback"):
            self.assertIn(phase, self.header)
        for api in (
            "pendingAction() const",
            "acceptAction(const MemoryAction &action)",
            "markDirty(const Lease &lease)",
            "release(const Lease &lease)",
            "completeWriteback(uint16_t slotIndex",
        ):
            self.assertIn(api, self.header)
        completion = self.header[
            self.header.index("completeWriteback(") : self.header.index(
                "/** Acquire one explicit",
                self.header.index("completeWriteback("),
            )
        ]
        self.assertIn("slot.phase != Phase::Writeback", completion)
        self.assertIn("slot.page != page", completion)
        self.assertIn("slot = Slot{}", completion)

    def test_tests_cover_required_adversarial_paths(self) -> None:
        source = TEST_PATH.read_text(encoding="utf-8")
        for evidence in (
            "Backpressure",
            "discardsCleanVictim",
            "FillReleasedObsolete",
            "WritebackCompleted",
            "notifyPageReady(oldPage)",
            "forgedLease",
            "freeDescriptor",
            "controller.pendingAction()",
        ):
            self.assertIn(evidence, source)

    def test_runner_is_standalone_and_notes_reject_integration_claims(
        self,
    ) -> None:
        runner = RUNNER_PATH.read_text(encoding="utf-8")
        self.assertIn("logical_spd_cache_controller_test.cc", runner)
        self.assertIn("test_logical_spd_cache_controller_contract.py", runner)
        self.assertNotIn("gem5.opt", runner)
        self.assertNotIn("scons", runner.lower())

        notes = NOTE_PATH.read_text(encoding="utf-8")
        for caveat in (
            "not wired into gem5",
            "not a timing model",
            "not a synthesis or area estimate",
            "stores no page payload",
        ):
            self.assertIn(caveat, notes)


if __name__ == "__main__":
    unittest.main()
