#!/usr/bin/env python3
"""Source contracts for isolated descriptor-spool A-source routing."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KNOB = "virtual_descriptor_spool_source_bypass_cache"


class DescriptorSpoolSourceBypassContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.options = (ROOT / "configs/common/Options.py").read_text()
        cls.config = (ROOT / "configs/common/MAAConfig.py").read_text()
        cls.simobject = (ROOT / "src/mem/MAA/MAA.py").read_text()
        cls.header = (ROOT / "src/mem/MAA/MAA.hh").read_text()
        cls.maa = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        cls.indirect = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        cls.matrix = (
            ROOT
            / "experiments/scripts/run_descriptor_spool_read_ahead_matrix.sh"
        ).read_text()

    def test_knob_is_explicit_and_defaults_off(self) -> None:
        self.assertIn(
            "--maa_virtual_descriptor_spool_source_bypass_cache",
            self.options,
        )
        self.assertIn(
            'opts["virtual_descriptor_spool_source_bypass_cache"]',
            self.config,
        )
        self.assertIn(
            "virtual_descriptor_spool_source_bypass_cache = Param.Bool(\n"
            "        False,",
            self.simobject,
        )
        self.assertIn(f"bool {KNOB};", self.header)
        self.assertIn(f"p.{KNOB}", self.maa)

    def test_knob_requires_descriptor_spooling(self) -> None:
        self.assertIn(
            "panic_if(virtual_descriptor_spool_source_bypass_cache &&\n"
            "                 !virtual_index_descriptor_spool,",
            self.maa,
        )

    def test_only_descriptor_spooled_a_source_route_is_changed(self) -> None:
        build = self.indirect.split("case Status::Build:", 1)[1].split(
            "case Status::Request:", 1
        )[0]
        self.assertIn("if (descriptor_spool_operation)", build)
        self.assertIn(
            "maa->virtual_descriptor_spool_source_bypass_cache", build
        )
        self.assertIn(
            "my_force_cache = source_bypass_cache\n"
            "                    ? false\n"
            "                    : direct_index_force_cache;",
            build,
        )
        self.assertIn("event=descriptor_spool_source_route schema=1", build)

        direct_index = self.indirect.split(
            "void IndirectAccessUnit::createDirectIndexReadPacket", 1
        )[1].split(
            "void IndirectAccessUnit::createDescriptorSpoolReadPacket", 1
        )[
            0
        ]
        self.assertIn("direct_index_force_cache);", direct_index)
        descriptor_read = self.indirect.split(
            "void IndirectAccessUnit::createDescriptorSpoolReadPacket", 1
        )[1].split(
            "void IndirectAccessUnit::createDescriptorSpoolWritePacket", 1
        )[
            0
        ]
        self.assertIn("maa->getClockEdge(Cycles(0)), true);", descriptor_read)
        descriptor_write = self.indirect.split(
            "void IndirectAccessUnit::createDescriptorSpoolWritePacket", 1
        )[1].split("void IndirectAccessUnit::memReadPacketSent", 1)[0]
        self.assertIn("maa->getClockEdge(Cycles(0)), true);", descriptor_write)

    def test_matrix_records_resolved_route_and_trace(self) -> None:
        for token in (
            "source_route.tsv",
            "virtual_descriptor_spool_source_bypass_cache=$resolved",
            "event=descriptor_spool_source_route",
            "route_evidence a_source_routing_4k 1 0",
        ):
            self.assertIn(token, self.matrix)


if __name__ == "__main__":
    unittest.main()
