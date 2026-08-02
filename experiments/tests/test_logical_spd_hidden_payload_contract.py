#!/usr/bin/env python3
"""Source contract for the private logical-SPD payload substrate."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAYOUT_PATH = ROOT / "src/mem/MAA/LogicalSPDHiddenPayload.hh"
SPD_HEADER_PATH = ROOT / "src/mem/MAA/SPD.hh"
SPD_SOURCE_PATH = ROOT / "src/mem/MAA/SPD.cc"
MAA_SOURCE_PATH = ROOT / "src/mem/MAA/MAA.cc"
CPU_PORT_PATH = ROOT / "src/mem/MAA/CpuSidePort.cc"
IF_SOURCE_PATH = ROOT / "src/mem/MAA/IF.cc"
INVALIDATOR_SOURCE_PATH = ROOT / "src/mem/MAA/Invalidator.cc"
TEST_PATH = ROOT / "tests/maa/logical_spd_hidden_payload_test.cc"
RUNNER_PATH = ROOT / (
    "experiments/scripts/run_logical_spd_hidden_payload_unit.sh"
)
NOTE_PATH = ROOT / (
    "experiments/analysis/"
    "logical_spd_hidden_payload_substrate_2026-08-02.md"
)


class LogicalSpdHiddenPayloadContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.layout = LAYOUT_PATH.read_text(encoding="utf-8")
        cls.spd_header = SPD_HEADER_PATH.read_text(encoding="utf-8")
        cls.spd_source = SPD_SOURCE_PATH.read_text(encoding="utf-8")
        cls.maa_source = MAA_SOURCE_PATH.read_text(encoding="utf-8")

    def test_geometry_and_payload_accounting_are_fixed(self) -> None:
        for declaration in (
            "LogicalSlotsPerMAA = 2",
            "FP64LanesPerSlot = 2",
            "LaneElements = 4096",
            "PayloadBytesPerMAA",
        ):
            self.assertIn(declaration, self.layout)
        self.assertIn("HiddenLanesPerMAA * LaneBytes", self.layout)

        test = TEST_PATH.read_text(encoding="utf-8")
        for exact_evidence in (
            "payloadBytesPerMAA() == 65536",
            "4 * Access::payloadBytesPerMAA() == 262144",
            "allocated == 64",
            "visible_bytes + 262144",
        ):
            self.assertIn(exact_evidence, test)

    def test_mapping_is_private_and_reserved_for_internal_generation(
        self,
    ) -> None:
        class_body = self.layout[
            self.layout.index("class LogicalSPDHiddenPayloadLayout") :
        ]
        private = class_body.index("private:")
        self.assertNotIn("public:", class_body[:private])
        for friend in (
            "friend class MAA;",
            "friend class SPD;",
            "friend class LogicalSPDHiddenPayloadTestAccess;",
        ):
            self.assertIn(friend, class_body[: private + 100])

        spd_private = self.spd_header[
            self.spd_header.index("private:") : self.spd_header.index(
                "public:", self.spd_header.index("private:")
            )
        ]
        self.assertIn("friend class MAA;", spd_private)
        self.assertIn("logicalSpdHiddenSlotBaseTileID", spd_private)
        self.assertIn("logicalSpdHiddenLaneTileID", spd_private)
        self.assertIn(
            "return logicalSpdHiddenLaneTileID(maa_id, logical_slot, 0)",
            self.spd_source,
        )
        for mapper_argument in (
            "visible_tile_count, num_maas, maa_id, logical_slot",
            "fp64_lane, &tile_id",
        ):
            self.assertIn(mapper_argument, self.spd_source)

        expected_formula = "static_cast<uint64_t>(maa_id) * HiddenLanesPerMAA"
        self.assertIn(expected_formula, self.layout)
        self.assertIn(
            "static_cast<uint64_t>(logical_slot) * FP64LanesPerSlot",
            self.layout,
        )
        self.assertIn("static_cast<uint64_t>(fp64_lane)", self.layout)

    def test_visible_and_allocated_counts_are_separate(self) -> None:
        self.assertIn("unsigned int visible_tile_count;", self.spd_header)
        self.assertIn("unsigned int allocated_tile_count;", self.spd_header)
        self.assertNotIn("unsigned int num_tiles;", self.spd_header)
        self.assertIn(
            "tiles_status = new SPD::TileStatus[allocated_tile_count]",
            self.spd_source,
        )
        self.assertIn(
            "new std::vector<uint8_t>[allocated_tile_count]",
            self.spd_source,
        )
        self.assertIn(
            "new std::vector<int>[allocated_tile_count]", self.spd_source
        )

    def test_every_existing_spd_tile_entry_rejects_hidden_ids(self) -> None:
        check_start = self.spd_header.index("void check_tile_id")
        check_end = self.spd_header.index(
            "void check_tile_element_id", check_start
        )
        public_check = self.spd_header[check_start:check_end]
        self.assertIn("tile_id >= visible_tile_count", public_check)
        self.assertNotIn("allocated_tile_count", public_check)

        for template_name in (
            "getData",
            "getDataPtr",
            "setData",
            "setFakeData",
        ):
            start = self.spd_header.index(template_name + "(")
            body = self.spd_header[start : self.spd_header.index("}", start)]
            self.assertIn("check_tile_element_id", body, template_name)

        checked_functions = (
            "setDataLatency",
            "getTileStatus",
            "setTileIdle",
            "setTileFinished",
            "setTileService",
            "setTileDirty",
            "setTileClean",
            "getTileDirty",
            "setTileReady",
            "setTileNotReady",
            "getTileReady",
            "getElementFinished",
            "wakeup_waiting_units",
            "getSize",
            "getSizeForReadyElement",
            "setSize",
            "setVirtualSize",
        )
        for function in checked_functions:
            pattern = re.compile(
                rf"(?:\w[\w:<>, ]*\s+)?SPD::{function}\([^{{]*\)\s*"
                rf"\{{\s*check_tile_(?:element_)?id\(",
                re.DOTALL,
            )
            self.assertRegex(self.spd_source, pattern, function)

        for function in (
            "setTileIdle",
            "setTileFinished",
            "setTileService",
            "setTileDirty",
            "setTileClean",
            "setTileReady",
            "setTileNotReady",
            "getElementFinished",
        ):
            start = self.spd_source.index(f"SPD::{function}(")
            body = self.spd_source[start : self.spd_source.index("}", start)]
            self.assertIn("check_tile_id(tile_id, word_size)", body, function)

    def test_legacy_admission_mmio_coherence_and_waits_stay_visible(
        self,
    ) -> None:
        self.assertIn(
            "new SPD(this, num_tiles, num_maas, num_tile_elements",
            self.maa_source,
        )
        self.assertIn(
            "new IF(num_instructions_per_maa, num_maas, num_tiles, this)",
            self.maa_source,
        )
        self.assertIn(
            "invalidator->allocate(num_maas, num_tiles, num_tile_elements",
            self.maa_source,
        )
        for wait_bound in (
            "tokenTileID < 0 || tokenTileID >= num_tiles",
            "num_tiles + tokenTileID * MaxVirtualPages",
        ):
            self.assertIn(wait_bound, self.maa_source)

        for path in (
            CPU_PORT_PATH,
            IF_SOURCE_PATH,
            INVALIDATOR_SOURCE_PATH,
        ):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("allocated_tile_count", source, str(path))
            self.assertNotIn("logicalSpdHiddenLaneTileID", source, str(path))

    def test_allocation_initializes_and_charges_the_hidden_tail(self) -> None:
        for evidence in (
            "tryAllocatedPayloadBytes(",
            "tryAllocatedElementStateCount(",
            "tiles_data = new uint8_t[allocated_payload_bytes]",
            "element_finished = new bool[allocated_element_count]",
            "i < allocated_tile_count",
            "tiles_status[i] = SPD::TileStatus::Finished",
            "tiles_size[i] = 0",
            "tiles_dirty[i] = false",
            "tiles_ready[i] = 0",
            "i < allocated_element_count",
            "element_finished[i] = true",
            "initializeHiddenPayload(",
            "allocated_payload_bytes - visible_bytes",
        ):
            self.assertIn(evidence, self.spd_source + self.layout)
        for released in (
            "delete[] tiles_dirty",
            "delete[] tiles_ready",
            "delete[] waiting_units_funcs",
            "delete[] waiting_units_ids",
        ):
            self.assertIn(released, self.spd_source)

    def test_scope_contains_no_scheduler_response_or_benchmark_wiring(
        self,
    ) -> None:
        mapper_name = "logicalSpdHiddenLaneTileID"
        occurrences = []
        for path in (ROOT / "src/mem/MAA").glob("*.cc"):
            if mapper_name in path.read_text(encoding="utf-8"):
                occurrences.append(path.name)
        self.assertEqual(occurrences, ["SPD.cc"])

        note = NOTE_PATH.read_text(encoding="utf-8")
        for boundary in (
            "No logical scheduling",
            "No response handling",
            "No benchmark wiring",
            "No performance claim",
            "65,536 bytes per MAA",
            "262,144 bytes for four MAAs",
        ):
            self.assertIn(boundary, note)

    def test_runner_is_host_only_strict_and_sanitized(self) -> None:
        runner = RUNNER_PATH.read_text(encoding="utf-8")
        for flag in (
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic",
            "-fsanitize=address,undefined",
        ):
            self.assertIn(flag, runner)
        self.assertIn("logical_spd_hidden_payload_test.cc", runner)
        self.assertIn("test_logical_spd_hidden_payload_contract.py", runner)
        self.assertNotIn("gem5", runner.lower().replace("no-gem5", ""))


if __name__ == "__main__":
    unittest.main()
