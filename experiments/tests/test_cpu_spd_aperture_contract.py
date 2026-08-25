#!/usr/bin/env python3
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CPU = (ROOT / "src/mem/MAA/CpuSidePort.cc").read_text(encoding="utf-8")
MAA_HH = (ROOT / "src/mem/MAA/MAA.hh").read_text(encoding="utf-8")
MAA_CC = (ROOT / "src/mem/MAA/MAA.cc").read_text(encoding="utf-8")
HELPER = (ROOT / "src/mem/MAA/CpuSpdAperture.hh").read_text(encoding="utf-8")
UNIT = (ROOT / "tests/maa/cpu_spd_aperture_test.cc").read_text(
    encoding="utf-8"
)
RUNNER = (
    ROOT / "experiments/scripts/run_cpu_spd_prefetch_boundary_smoke.sh"
).read_text(encoding="utf-8")
GUEST = (ROOT / "benchmarks/API/test_cpu_spd_prefetch_boundary.cpp").read_text(
    encoding="utf-8"
)
QUEUED = (ROOT / "src/mem/cache/prefetch/queued.cc").read_text(
    encoding="utf-8"
)
CACHE = (ROOT / "src/mem/cache/cache.cc").read_text(encoding="utf-8")
BASE_CACHE = (ROOT / "src/mem/cache/base.cc").read_text(encoding="utf-8")
MSHR = (ROOT / "src/mem/cache/mshr.cc").read_text(encoding="utf-8")
REQUEST = (ROOT / "src/mem/request.hh").read_text(encoding="utf-8")


class CpuSpdApertureContractTest(unittest.TestCase):
    def test_pure_policy_has_fail_closed_dispositions(self):
        for token in (
            "Valid",
            "DropBoundaryPrefetch",
            "PhysicalOutOfRange",
            "CrossesPhysicalPayload",
            "CrossesLogicalTile",
            "InvalidGeometry",
        ):
            self.assertIn(token, HELPER)
        self.assertIn("physical_elements > logical_elements", HELPER)
        self.assertIn("packet_bytes != cache_line_bytes", HELPER)
        self.assertIn("range_offset % cache_line_bytes != 0", HELPER)

    def test_executable_unit_covers_required_boundary_cases(self):
        for token in (
            "testValidLastPhysicalLine",
            "testElement4096Policy",
            "DropBoundaryPrefetch",
            "PhysicalOutOfRange",
            "CrossesPhysicalPayload",
            "CrossesLogicalTile",
            "InvalidGeometry",
            "CPU_SPD_APERTURE_UNIT_PASS",
        ):
            self.assertIn(token, UNIT)

    def test_only_observed_downstream_task_tagged_shared_read_can_drop(self):
        helper = CPU[
            CPU.index("isDroppableBoundaryPrefetch") : CPU.index(
                "} // anonymous namespace"
            )
        ]
        self.assertIn("pkt->cmd == MemCmd::ReadSharedReq", helper)
        self.assertIn("context_switch_task_id::Prefetcher", helper)
        self.assertNotIn("pkt->cmd.isPrefetch() ||", helper)
        self.assertNotIn("pkt->req->isPrefetch() ||", helper)

    def test_queued_prefetch_task_tag_survives_shared_miss_conversion(self):
        create = QUEUED[
            QUEUED.index("Queued::DeferredPacket::createPkt") : QUEUED.index(
                "Queued::DeferredPacket::startTranslation"
            )
        ]
        self.assertIn("Request>(paddr, blk_size,", create)
        self.assertIn("0, requestor_id", create)
        self.assertIn(
            "req->taskId(context_switch_task_id::Prefetcher)", create
        )
        self.assertIn("MemCmd::HardPFReq", create)

        miss = CACHE[
            CACHE.index("Cache::createMissPacket") : CACHE.index(
                "Cache::handleAtomicReqMiss"
            )
        ]
        self.assertIn("MemCmd::ReadSharedReq", miss)
        self.assertIn("new Packet(cpu_pkt->req, cmd, blkSize)", miss)
        self.assertIn(
            "uint32_t _taskId = context_switch_task_id::Unknown", REQUEST
        )
        self.assertIn("_taskId(other._taskId)", REQUEST)

    def test_error_response_cannot_fill_and_prefetch_target_is_discarded(self):
        self.assertIn("if (is_fill && !is_error)", BASE_CACHE)
        no_error_fill = BASE_CACHE[
            BASE_CACHE.index("if (is_fill && !is_error)") : BASE_CACHE.index(
                "serviceMSHRTargets(mshr, pkt, blk)"
            )
        ]
        self.assertIn(
            "handleFill(pkt, blk, writebacks, allocate)", no_error_fill
        )
        self.assertIn(
            "if (is_error)\n                tgt_pkt->copyError(pkt)", CACHE
        )
        prefetch_target = CACHE[
            CACHE.index("case MSHR::Target::FromPrefetcher") : CACHE.index(
                "case MSHR::Target::FromSnoop"
            )
        ]
        self.assertIn("delete tgt_pkt", prefetch_target)
        self.assertIn("target->cmd == MemCmd::HardPFReq", MSHR)

    def test_drop_precedes_readiness_and_never_enters_maa_request_path(self):
        receive = CPU[CPU.index("bool MAA::CpuSidePort::recvTimingReq") :]
        classify = receive.index("CpuSpdAperture::classify")
        drop = receive.index("DropBoundaryPrefetch")
        readiness = receive.index("if (tryTiming(pkt))")
        mutation_path = receive.index("maa.recvTimingReq(pkt, core_id)")
        self.assertLess(classify, drop)
        self.assertLess(drop, readiness)
        self.assertLess(readiness, mutation_path)
        drop_body = receive[drop:readiness]
        self.assertIn("pkt->setBadAddress()", drop_body)
        self.assertNotIn("maa.recvTimingReq", drop_body)
        self.assertNotIn("maa.spd", drop_body)
        self.assertNotIn("maa.invalidator", drop_body)
        reserved = receive.index("logicalTileReservedLane(tile_id)")
        owned = receive.index("logicalCompletionLaneOwned(tile_id)")
        self.assertLess(reserved, classify)
        self.assertLess(owned, classify)

    def test_valid_accesses_retain_reserved_lane_checks(self):
        try_timing = CPU[CPU.index("bool MAA::CpuSidePort::tryTiming") :]
        try_timing = try_timing[: try_timing.index("void MAA::recvTimingReq")]
        self.assertIn("logicalTileReservedLane(tile_id)", try_timing)
        self.assertIn("logicalCompletionLaneOwned(tile_id)", try_timing)
        self.assertLess(
            try_timing.index("logicalTileReservedLane(tile_id)"),
            try_timing.index("getTileReady(tile_id)"),
        )

    def test_drop_has_stats_and_a_state_free_trace(self):
        for name in (
            "cpu_spd_boundary_prefetch_drops",
            "cpu_spd_out_of_range_rejections",
        ):
            self.assertIn(f"statistics::Scalar {name};", MAA_HH)
            self.assertIn(f"ADD_STAT({name}", MAA_CC)
            self.assertIn(f"maa.stats.{name}++", CPU)
        self.assertIn("event=cpu_spd_boundary_prefetch_drop schema=1", CPU)
        self.assertIn("spd_touched=0 invalidator_touched=0", CPU)

    def test_live_smoke_is_exact_and_requires_a_real_drop(self):
        self.assertIn("PhysicalElements = 4096", GUEST)
        self.assertIn("maa_stream_load<uint32_t>", GUEST)
        self.assertIn("wait_ready(tile_id)", GUEST)
        self.assertLess(
            GUEST.index("wait_ready(tile_id)"), GUEST.index("m5_reset_stats")
        )
        self.assertIn("element < PhysicalElements", GUEST)
        self.assertIn("volatile uint32_t *tile", GUEST)
        self.assertIn("CPU_SPD_PREFETCH_BOUNDARY guest_elements=", GUEST)
        for token in (
            "--l1d-hwp-type=StridePrefetcher",
            "--maa_num_tile_elements=16384",
            "--maa_physical_tile_elements=4096",
            "[[ $drops =~ ^[1-9][0-9]*$ ]]",
            "[[ $rejections == 0 ]]",
            'grep -Fxc "$expected"',
            " event=cpu_spd_boundary_prefetch_drop ",
            "task_prefetch=1 cmd=ReadSharedReq response=BadAddress",
            "CPU SPD aperture rejected physical_out_of_range access:",
            "CPU_SPD_PREFETCH_BOUNDARY_NEGATIVE_OBSERVED",
            "CPU_SPD_PREFETCH_BOUNDARY_SMOKE_PASS",
        ):
            self.assertIn(token, RUNNER)


if __name__ == "__main__":
    unittest.main()
