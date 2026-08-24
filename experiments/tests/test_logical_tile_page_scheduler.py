#!/usr/bin/env python3
"""Source-contract checks for the fixed logical-tile page scheduler."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "src/mem/MAA/LogicalTilePageScheduler.hh"
CPP_TEST = ROOT / "tests/maa/logical_tile_page_scheduler_test.cc"
RUNNER = ROOT / "experiments/scripts/run_logical_tile_page_scheduler_unit.sh"


class LogicalTilePageSchedulerSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.header = HEADER.read_text(encoding="utf-8")
        cls.cpp_test = CPP_TEST.read_text(encoding="utf-8")
        cls.runner = RUNNER.read_text(encoding="utf-8")

    def test_geometry_is_element_based_and_fixed(self) -> None:
        for evidence in (
            "LogicalDescriptors = 8",
            "PhysicalFrames = 4",
            "MaxFrameLaneSpan = 2",
            "LogicalElements = 16 * 1024",
            "PagesPerTile = 4",
            "ElementsPerPage = 4 * 1024",
            "uint64_t{LogicalElements} * config.wordBytes",
            "uint64_t{ElementsPerPage} * config.wordBytes",
        ):
            self.assertIn(evidence, self.header)
        self.assertIn("config.backingBytes != span", self.header)
        self.assertIn("(config.backingAddress % span) != 0", self.header)

    def test_state_is_bounded_and_payload_free(self) -> None:
        for evidence in (
            "std::array<DescriptorConfig, LogicalDescriptors>",
            "std::array<bool, LogicalDescriptors>",
            "std::array<uint16_t, PhysicalFrames>",
            "std::array<FrameRole, PhysicalFrames>",
        ):
            self.assertIn(evidence, self.header)
        forbidden = (
            r"std::vector",
            r"std::map",
            r"std::deque",
            r"std::list",
            r"std::unordered",
            r"\bnew\b",
            r"\bmalloc\b",
        )
        for pattern in forbidden:
            self.assertIsNone(re.search(pattern, self.header))
        self.assertNotRegex(self.header, r"\bdata\s*\[")

    def test_native_action_carries_complete_identity(self) -> None:
        action = self.header[
            self.header.index("struct NativeAction") : self.header.index(
                "explicit LogicalTilePageScheduler"
            )
        ]
        for field in (
            "Transaction transaction",
            "Generation generation",
            "uint16_t source1Frame",
            "uint16_t source2Frame",
            "uint16_t destinationFrame",
            "uint64_t backingAddress",
            "uint64_t byteOffset",
            "uint64_t byteLength",
            "uint8_t page",
        ):
            self.assertIn(field, action)

    def test_completion_is_exact_and_publication_is_response_gated(
        self,
    ) -> None:
        for status in (
            "TransactionExhausted",
            "NonMonotonicTransaction",
            "StaleResponse",
            "DuplicateResponse",
            "StaleGeneration",
            "WrongAction",
            "WrongFrame",
            "WrongAddress",
            "WrongSize",
        ):
            self.assertIn(status, self.header)
        complete = self.header[
            self.header.index("Status complete(") : self.header.index(
                "Status setFrameAvailable"
            )
        ]
        self.assertNotIn("readyPageMask |=", complete)
        advance = self.header[
            self.header.index("void advance()") : self.header.index(
                "void clearOperation()"
            )
        ]
        self.assertIn("Phase::WriteDestination", advance)
        self.assertIn("readyPageMask |=", advance)
        self.assertIn("release(source1Frame)", advance)
        self.assertIn("release(source2Frame)", advance)

    def test_tests_cover_required_shapes_and_failures(self) -> None:
        for evidence in (
            "geometryAndConfigurationAreExact",
            "denseStoreUsesExactPageOffsets",
            "unaryRetainsDirtyDestinationThroughWriteResponse",
            "distinctVectorRetainsBothSourcesUntilComputeCompletion",
            "selfVectorUsesOneSourceFrame",
            "activeReferencesAndAliasesFailClosed",
            "mismatchedAndDuplicateCompletionsFailClosed",
            "frameAndTransactionExhaustionAreClosed",
        ):
            self.assertIn(evidence, self.cpp_test)
        self.assertIn("fp32", self.cpp_test)
        self.assertIn("fp64", self.cpp_test)

    def test_runner_executes_optimized_and_sanitized_modes(self) -> None:
        self.assertIn("optimized sanitize", self.runner)
        self.assertIn("-fsanitize=address,undefined", self.runner)
        self.assertIn("detect_leaks=0", self.runner)
        self.assertIn("UBSAN_OPTIONS=halt_on_error=1", self.runner)


if __name__ == "__main__":
    unittest.main()
