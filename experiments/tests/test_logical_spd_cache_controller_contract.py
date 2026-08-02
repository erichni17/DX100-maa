#!/usr/bin/env python3
"""Contract and behavioral checks for the bounded SPD-cache core."""

import os
import subprocess
import tempfile
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
            "pin(const PageIdentity &page)",
        ):
            self.assertIn(operation, self.header)
        self.assertIn("FillReleasedObsolete", self.header)
        self.assertIn("GenerationExhausted", self.header)

    def test_memory_actions_and_responses_require_unique_serials(self) -> None:
        action = self.header[
            self.header.index("struct MemoryAction") : self.header.index(
                "enum class ActionResult"
            )
        ]
        self.assertIn("TransactionSerial serial = NoTransaction", action)
        self.assertIn("serial == other.serial", action)
        for signature in (
            "completeFill(uint16_t slotIndex, const PageIdentity &page,",
            "completeWriteback(uint16_t slotIndex, const PageIdentity &page,",
        ):
            self.assertIn(signature, self.header)
        for evidence in (
            "slot.transaction != serial",
            "action.serial = nextMemorySerial()",
            "lastMemorySerial = action.serial",
            "memorySerialExhausted()",
            "std::numeric_limits<TransactionSerial>::max()",
        ):
            self.assertIn(evidence, self.header)

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
        self.assertIn("slot.transaction != serial", completion)
        self.assertIn("slot = Slot{}", completion)

    def test_tests_cover_reordered_duplicate_and_late_responses(self) -> None:
        source = TEST_PATH.read_text(encoding="utf-8")
        for evidence in (
            "testTransactionSerialRejectsReorderedDuplicateAndLateResponses",
            "firstFill.serial",
            "laterFill.serial",
            "firstWriteback.serial",
            "laterWriteback.serial",
            "laterFill.serial + 1",
            "ResponseResult::Stale",
            "ResponseResult::Invalid",
        ):
            self.assertIn(evidence, source)

    def test_tests_cover_writeback_fill_exclusion(self) -> None:
        source = TEST_PATH.read_text(encoding="utf-8")
        pending = self.header[
            self.header.index("pendingAction() const") : self.header.index(
                "acceptAction(const MemoryAction &action)"
            )
        ]
        self.assertIn("pageHasOwner(missQueue[0])", pending)
        for evidence in (
            "testWritebackFillExclusionPreservesSinglePageOwner",
            "pageWriteback",
            "replacementFill",
            "ActionKind::None",
            "Phase::Writeback",
            "replayFill",
        ):
            self.assertIn(evidence, source)

    def test_existing_finite_fail_closed_paths_remain_covered(self) -> None:
        source = TEST_PATH.read_text(encoding="utf-8")
        for evidence in (
            "Backpressure",
            "discardsCleanVictim",
            "FillReleasedObsolete",
            "notifyPageReady(oldPage)",
            "forgedLease",
            "freeDescriptor",
        ):
            self.assertIn(evidence, source)

    def test_adversarial_cpp_scenarios_execute_from_python(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "logical_spd_cache_controller_test"
            compile_result = subprocess.run(
                [
                    os.environ.get("CXX", "g++"),
                    f"-I{ROOT / 'src'}",
                    "-std=c++17",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-pedantic",
                    str(TEST_PATH),
                    "-o",
                    str(binary),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                0,
                compile_result.returncode,
                compile_result.stdout + compile_result.stderr,
            )
            run_result = subprocess.run(
                [str(binary)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(
            0,
            run_result.returncode,
            run_result.stdout + run_result.stderr,
        )
        self.assertIn(
            "logical_spd_cache_controller_test: PASS", run_result.stdout
        )

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
