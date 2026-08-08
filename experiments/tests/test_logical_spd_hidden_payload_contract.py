#!/usr/bin/env python3
"""Source contract for atomic Runtime ownership of logical SPD payload."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAA_DIR = ROOT / "src" / "mem" / "MAA"


class LogicalSpdPayloadAuthorityContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.layout = (MAA_DIR / "LogicalSPDHiddenPayload.hh").read_text()
        cls.runtime = (MAA_DIR / "LogicalSPDCacheRuntime.hh").read_text()
        cls.bridge_hh = (MAA_DIR / "LogicalSPDCacheGem5Bridge.hh").read_text()
        cls.bridge_cc = (MAA_DIR / "LogicalSPDCacheGem5Bridge.cc").read_text()
        cls.spd_hh = (MAA_DIR / "SPD.hh").read_text()
        cls.spd_cc = (MAA_DIR / "SPD.cc").read_text()
        cls.maa_hh = (MAA_DIR / "MAA.hh").read_text()
        cls.maa_cc = (MAA_DIR / "MAA.cc").read_text()
        cls.sconscript = (MAA_DIR / "SConscript").read_text()
        cls.live_runner = (
            ROOT / "experiments" / "scripts" /
            "run_logical_spd_cache_live_smoke.sh"
        ).read_text()

    def test_runtime_owns_exact_payload(self) -> None:
        for evidence in (
            "std::array<double, Slice::PayloadElements> payload{}",
            "PrivatePayloadBits =",
            "Slice::PayloadBytes * 8",
            "PackedBytes == 34077",
        ):
            self.assertIn(evidence, self.runtime)
        self.assertIn(
            "PayloadBytesPerMAA ==\n              32768", self.layout
        )

    def test_retired_header_is_accounting_only(self) -> None:
        self.assertIn("Accounting-only compatibility constants", self.layout)
        for forbidden in (
            "tryAllocatedTileCount",
            "tryHiddenLaneTileID",
            "tryAllocatedPayloadBytes",
            "initializeHiddenPayload",
            "uint8_t *payload",
        ):
            self.assertNotIn(forbidden, self.layout)

    def test_spd_allocates_visible_state_only(self) -> None:
        self.assertIn("unsigned int visible_tile_count;", self.spd_hh)
        self.assertNotIn("allocated_tile_count", self.spd_hh + self.spd_cc)
        for evidence in (
            "new uint8_t[allocated_payload_bytes]",
            "new SPD::TileStatus[visible_tile_count]",
            "new bool[visible_tile_count]",
            "new uint16_t[visible_tile_count]",
            "new uint32_t[visible_tile_count]",
            "new std::vector<uint8_t>[visible_tile_count]",
            "new std::vector<int>[visible_tile_count]",
            "memset(tiles_data, 0, allocated_payload_bytes)",
        ):
            self.assertIn(evidence, self.spd_cc)
        for forbidden in (
            "logicalSpdHidden",
            "initializeHiddenPayload",
            "HiddenLanesPerMAA",
        ):
            self.assertNotIn(forbidden, self.spd_hh + self.spd_cc)

    def test_maa_owns_one_authenticated_live_bridge(self) -> None:
        self.assertIn(
            "std::unique_ptr<LogicalSPDCacheGem5Bridge> logicalSpdBridge",
            self.maa_hh,
        )
        self.assertEqual(
            self.maa_cc.count(
                "std::make_unique<LogicalSPDCacheGem5Bridge>(\n        num_maas, logicalSpdMode)"
            ),
            1,
        )
        self.assertIn(
            "std::vector<std::unique_ptr<LogicalSPDCacheRuntime>>",
            self.bridge_hh,
        )
        self.assertIn(
            "bool admissionClosed() const { return false; }", self.bridge_hh
        )
        for evidence in (
            "submitLogicalSPDDescriptor",
            "recvLogicalSPDTimingResp",
            "serviceLogicalSPD",
            "compute != Slice::Status::Busy",
            "elements=%lu",
            "static_cast<unsigned long>(LogicalElements)",
            "logicalSpdBridge->registerSource",
            "logicalSpdBridge->receive",
        ):
            self.assertIn(evidence, self.maa_hh + self.maa_cc)
        self.assertNotIn("sendTimingReq", self.bridge_hh + self.bridge_cc)
        self.assertNotIn(
            "my_outstanding_pkt_map", self.bridge_hh + self.bridge_cc
        )

    def test_build_closure_is_explicit(self) -> None:
        self.assertEqual(
            self.sconscript.count("Source('LogicalSPDCacheTransport.cc')"), 1
        )
        self.assertEqual(
            self.sconscript.count("Source('LogicalSPDCacheGem5Bridge.cc')"), 1
        )

    def test_runtime_count_follows_maa_count(self) -> None:
        self.assertIn("runtimes.reserve(numMaas)", self.bridge_cc)
        self.assertIn("maaId < numMaas", self.bridge_cc)
        self.assertIn("runtimeCount() const", self.bridge_hh)

    def test_live_runner_requires_clean_source_and_unique_terminals(self) -> None:
        for evidence in (
            "[[ ! -s $out/source_status.txt ]]",
            "[[ ! -s $out/source.diff ]]",
            "^Exiting @ tick [0-9]+ because checkpoint$",
            "^Exiting @ tick [0-9]+ because m5_exit instruction encountered$",
        ):
            self.assertIn(evidence, self.live_runner)


if __name__ == "__main__":
    unittest.main()
