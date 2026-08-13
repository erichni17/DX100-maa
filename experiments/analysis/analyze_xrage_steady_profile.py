#!/usr/bin/env python3
"""Fail-closed, read-only profile of the frozen XRAGE steady-state triple."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_CAMPAIGN = Path(
    "/data1/nier/dx100-runs/"
    "2026-08-13-xrage-direct-x3-multicontext-64k-40dae46c"
)
ARMS = ("native16-64k", "page-64k", "line-64k")
EXPECTED_SOURCE = "40dae46cf32f164c375751f407f45ea9707af7b7"
EXPECTED_HASH = "5576400619275092867"
EXPECTED_TICKS = {
    "native16-64k": 41_989_576,
    "page-64k": 105_259_709,
    "line-64k": 101_470_531,
}


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def read_key_values(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key:
            fail(f"malformed key/value line in {path}: {line!r}")
        if key in result:
            fail(f"duplicate key {key!r} in {path}")
        result[key] = value
    return result


def read_single_tsv(path: Path) -> dict[str, str]:
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1:
        fail(f"expected one result row in {path}, got {len(rows)}")
    return dict(rows[0])


def parse_number(value: str) -> int | float | str:
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def first_stats_block(path: Path) -> dict[str, int | float | str]:
    stats: dict[str, int | float | str] = {}
    section = 0
    for line in path.read_text().splitlines():
        if line == "---------- Begin Simulation Statistics ----------":
            section += 1
            if section == 2:
                break
            continue
        if section != 1 or not line or line.startswith("-"):
            continue
        fields = line.split()
        if len(fields) >= 2:
            stats[fields[0]] = parse_number(fields[1])
    if section == 0 or "simTicks" not in stats:
        fail(f"missing first stats block in {path}")
    return stats


def config_sections(path: Path) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in path.read_text().splitlines():
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
        elif current is not None and "=" in line:
            key, value = line.split("=", 1)
            current[key] = value
    return sections


def first_integer(text: str, pattern: str, label: str) -> int:
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        fail(f"missing {label} in restore log")
    return int(match.group(1))


def parse_restore(path: Path) -> dict[str, Any]:
    text = path.read_text()
    roi_start = text.find("ROI started:")
    roi_end = text.find("ROI End!!!", roi_start)
    if roi_start < 0 or roi_end < 0:
        fail(f"missing ROI markers in {path}")
    roi = text[roi_start:roi_end]
    if not re.search(
        r"Exiting @ tick \d+ because m5_exit instruction encountered", text
    ):
        fail(f"missing terminal m5_exit in {path}")
    verify = re.search(r"MAA_GATHER_VERIFY_PASS length=(\d+) hash=(\d+)", text)
    if verify is None:
        fail(f"missing exact gather verification in {path}")

    channels: dict[str, dict[str, int | float]] = {}
    for channel in (0, 1):
        channel_result: dict[str, int | float] = {}
        integer_fields = {
            "roi_cycles": rf"SYS{channel}_memory_system_ROI_cycles: (\d+)",
            "read_commands": rf"CH{channel}_num_RD_commands_T: (\d+)",
            "queue_full": rf"CH{channel}_queue_full_T: (\d+)",
            "queue_empty": rf"CH{channel}_queue_empty_T: (\d+)",
            "max_queue_occupancy": (
                rf"CH{channel}_max_queue_occupancy_T: (\d+)"
            ),
        }
        for key, pattern in integer_fields.items():
            channel_result[key] = first_integer(roi, pattern, key)
        occupancy = re.search(
            rf"CH{channel}_avg_queue_occupancy_T: ([0-9.]+)", roi
        )
        if occupancy is None:
            fail(
                f"missing channel {channel} average queue occupancy in {path}"
            )
        channel_result["avg_queue_occupancy"] = float(occupancy.group(1))
        channels[str(channel)] = channel_result
    return {
        "verify_length": int(verify.group(1)),
        "verify_hash": verify.group(2),
        "terminal_m5_exit": True,
        "channels": channels,
    }


def require_int(stats: dict[str, Any], key: str) -> int:
    value = stats.get(key, 0)
    if not isinstance(value, int):
        fail(f"stat {key} is not an integer: {value!r}")
    return value


def sum_stats(stats: dict[str, Any], keys: list[str]) -> int:
    return sum(require_int(stats, key) for key in keys)


def pct(delta: int | float, baseline: int | float) -> float:
    return 100.0 * delta / baseline


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_arm(campaign: Path, name: str) -> dict[str, Any]:
    root = campaign / name
    for relative in (
        "manifest.txt",
        "result.tsv",
        "checkpoint.exit",
        "restore.exit",
        "restore.log",
        "run/config.ini",
        "run/stats.txt",
    ):
        if not (root / relative).is_file():
            fail(f"missing {name}/{relative}")
    exits = {
        stage: int((root / f"{stage}.exit").read_text().strip())
        for stage in ("checkpoint", "restore")
    }
    exits["driver"] = int(
        (campaign / f"{name}.driver.exit").read_text().strip()
    )
    if any(value != 0 for value in exits.values()):
        fail(f"nonzero exit status for {name}: {exits}")

    manifest = read_key_values(root / "manifest.txt")
    result = read_single_tsv(root / "result.tsv")
    stats = first_stats_block(root / "run/stats.txt")
    config = config_sections(root / "run/config.ini")
    restore = parse_restore(root / "restore.log")
    if manifest.get("source_commit") != EXPECTED_SOURCE:
        fail(f"unexpected source commit in {name}")
    if result.get("output_hash") != EXPECTED_HASH:
        fail(f"unexpected result hash in {name}")
    if (
        restore["verify_hash"] != EXPECTED_HASH
        or restore["verify_length"] != 65_536
    ):
        fail(f"restore verifier mismatch in {name}")
    if int(result["roi_simTicks"]) != require_int(stats, "simTicks"):
        fail(f"result/stats simTicks mismatch in {name}")
    if int(result["roi_simTicks"]) != EXPECTED_TICKS[name]:
        fail(f"unexpected frozen simTicks in {name}")
    if int(result["stats_blocks"]) != 2:
        fail(f"expected two stats blocks in {name}")

    maa = config.get("system.maa", {})
    if not maa:
        fail(f"missing system.maa config section in {name}")
    return {
        "root": str(root),
        "exits": exits,
        "manifest": manifest,
        "result": result,
        "stats": stats,
        "config": config,
        "restore": restore,
    }


def compact_arm(arm: dict[str, Any]) -> dict[str, Any]:
    stats = arm["stats"]
    config = arm["config"]
    maa = config["system.maa"]
    cpu_cycles = [
        require_int(stats, f"system.switch_cpus{core}.numCycles")
        for core in range(4)
    ]
    cpu_insts = [
        require_int(stats, f"system.switch_cpus{core}.commitStats0.numInsts")
        for core in range(4)
    ]
    cache_port_packet_keys = [
        f"system.tol3bus.pktCount_system.maa.cache_side_port[{port}]::total"
        for port in range(4)
    ]
    cache_port_byte_keys = [
        key.replace("pktCount", "pktSize") for key in cache_port_packet_keys
    ]
    retirement_packet_keys = [
        "system.tol3bus.pktCount_system.maa_retirement_caches"
        f"{port}.mem_side_port[0]::total"
        for port in range(4)
    ]
    retirement_byte_keys = [
        key.replace("pktCount", "pktSize") for key in retirement_packet_keys
    ]
    retirement_blocked_keys = [
        f"system.maa_retirement_caches{port}.blockedCycles_T::total"
        for port in range(4)
    ]
    retirement_no_mshr_keys = [
        f"system.maa_retirement_caches{port}.blockedCauses_T::no_mshrs"
        for port in range(4)
    ]
    retirement_no_wb_keys = [
        f"system.maa_retirement_caches{port}.blockedCauses_T::no_wb"
        for port in range(4)
    ]
    channels = arm["restore"]["channels"]
    return {
        "sim_ticks": require_int(stats, "simTicks"),
        "maa_total_cycles": require_int(stats, "system.maa.cycles_TOTAL"),
        "maa_instruction_cycles": require_int(stats, "system.maa.cycles"),
        "cpu_cycles_by_worker": cpu_cycles,
        "cpu_instructions_by_worker": cpu_insts,
        "cpu_instructions_total": sum(cpu_insts),
        "indirect_cycles": require_int(stats, "system.maa.cycles_INDRD"),
        "indirect_fill_cycles": require_int(
            stats, "system.maa.I0_IND_CyclesFill"
        ),
        "indirect_request_cycles": require_int(
            stats, "system.maa.I0_IND_CyclesRequest"
        ),
        "a_line_memory_loads": require_int(
            stats, "system.maa.I0_IND_LoadsMemAccessing"
        ),
        "a_line_memory_load_latency": require_int(
            stats, "system.maa.I0_IND_LoadsMemAccessingLatency"
        ),
        "alu_compute_cycles": require_int(
            stats, "system.maa.A0_ALU_CyclesCompute"
        ),
        "alu_instruction_cycles": require_int(stats, "system.maa.cycles_ALUS"),
        "stream_request_cycles": require_int(
            stats, "system.maa.S0_STR_CyclesRequest"
        ),
        "stream_cache_lines": require_int(
            stats, "system.maa.S0_STR_NumCacheLineInserted"
        ),
        "virtual_build_rounds": require_int(
            stats, "system.maa.I0_IND_VirtBuildRounds"
        ),
        "virtual_response_word_pool_stalls": require_int(
            stats, "system.maa.I0_IND_VirtResponseWordPoolStalls"
        ),
        "virtual_write_issues": require_int(
            stats, "system.maa.I0_IND_VirtWriteIssues"
        ),
        "virtual_full_line_writes": require_int(
            stats, "system.maa.I0_IND_VirtFullLineWrites"
        ),
        "virtual_partial_writes": require_int(
            stats, "system.maa.I0_IND_VirtPartialWrites"
        ),
        "virtual_pipeline_overlap_cycles": require_int(
            stats, "system.maa.I0_IND_VirtPipelineCyclesOverlap"
        ),
        "direct": {
            key.removeprefix("direct_retirement_"): require_int(
                stats, f"system.maa.{key}"
            )
            for key in (
                "direct_retirement_descriptors",
                "direct_retirement_producer_acks",
                "direct_retirement_producer_line_acks",
                "direct_retirement_page_fallback_lines",
                "direct_retirement_read_issues",
                "direct_retirement_read_responses",
                "direct_retirement_alu_issues",
                "direct_retirement_alu_completions",
                "direct_retirement_write_issues",
                "direct_retirement_write_responses",
                "direct_retirement_credit_high_water",
                "direct_retirement_credit_stalls",
                "direct_retirement_address_stalls",
                "direct_retirement_retries",
                "direct_retirement_overlap_ticks",
                "direct_retirement_active_stage_high_water",
                "direct_retirement_context_high_water",
                "direct_retirement_context_full_stalls",
                "direct_retirement_request_record_high_water",
                "direct_retirement_fallbacks",
            )
        },
        "xbar": {
            "maa_cache_port_packets_by_port": [
                require_int(stats, key) for key in cache_port_packet_keys
            ],
            "maa_cache_port_bytes_by_port": [
                require_int(stats, key) for key in cache_port_byte_keys
            ],
            "retirement_cache_packets_by_port": [
                require_int(stats, key) for key in retirement_packet_keys
            ],
            "retirement_cache_bytes_by_port": [
                require_int(stats, key) for key in retirement_byte_keys
            ],
        },
        "retirement_cache": {
            "mshrs_per_cache": int(
                config["system.maa_retirement_caches0"]["mshrs"]
            ),
            "write_buffers_per_cache": int(
                config["system.maa_retirement_caches0"]["write_buffers"]
            ),
            "blocked_cycles_total": sum_stats(stats, retirement_blocked_keys),
            "no_mshr_causes_total": sum_stats(stats, retirement_no_mshr_keys),
            "no_write_buffer_causes_total": sum_stats(
                stats, retirement_no_wb_keys
            ),
        },
        "l3": {
            "mshrs": int(config["system.l3"]["mshrs"]),
            "write_buffers": int(config["system.l3"]["write_buffers"]),
            "read_req_10_maa": require_int(
                stats, "system.l3.ReadReq_10.accesses::maa"
            ),
            "write_line_req_9_maa": require_int(
                stats, "system.l3.WriteLineReq_9.accesses::maa"
            ),
        },
        "dram": {
            "roi_cycles_by_channel": [
                channels[str(i)]["roi_cycles"] for i in range(2)
            ],
            "read_commands_total": sum(
                int(channels[str(i)]["read_commands"]) for i in range(2)
            ),
            "queue_full_total": sum(
                int(channels[str(i)]["queue_full"]) for i in range(2)
            ),
            "queue_empty_total": sum(
                int(channels[str(i)]["queue_empty"]) for i in range(2)
            ),
            "avg_queue_occupancy_by_channel": [
                channels[str(i)]["avg_queue_occupancy"] for i in range(2)
            ],
        },
        "config": {
            "num_alu_lanes": int(maa["num_ALU_lanes"]),
            "alu_lane_latency": int(maa["ALU_lane_latency"]),
            "num_indirect_units": int(maa["num_indirect_units_per_maa"]),
            "num_maas": int(maa["num_maas"]),
            "max_outstanding_cache_side_packets": int(
                maa["max_outstanding_cache_side_packets"]
            ),
            "direct_retirement_line_handoff": (
                maa["direct_retirement_line_handoff"] == "true"
            ),
        },
    }


def add_xbar_totals(arm: dict[str, Any]) -> None:
    xbar = arm["xbar"]
    xbar["maa_cache_port_packets"] = sum(
        xbar["maa_cache_port_packets_by_port"]
    )
    xbar["maa_cache_port_bytes"] = sum(xbar["maa_cache_port_bytes_by_port"])
    xbar["retirement_cache_packets"] = sum(
        xbar["retirement_cache_packets_by_port"]
    )
    xbar["retirement_cache_bytes"] = sum(
        xbar["retirement_cache_bytes_by_port"]
    )
    xbar["observed_packets"] = (
        xbar["maa_cache_port_packets"] + xbar["retirement_cache_packets"]
    )
    xbar["observed_bytes"] = (
        xbar["maa_cache_port_bytes"] + xbar["retirement_cache_bytes"]
    )


def analyze(campaign: Path, verify_binary: bool) -> dict[str, Any]:
    campaign_manifest = read_key_values(campaign / "campaign.manifest")
    provenance = read_key_values(campaign / "input/gem5.provenance.txt")
    strict_pair = json.loads(
        (campaign / "validated_pair_strict4.json").read_text()
    )
    if campaign_manifest.get("source_commit") != EXPECTED_SOURCE:
        fail("campaign source commit is not the frozen commit")
    if provenance.get("source_commit") != EXPECTED_SOURCE:
        fail("simulator provenance source commit mismatch")
    if (
        strict_pair.get("status") != "pass"
        or strict_pair.get("required_context_high_water") != 4
        or strict_pair.get("required_descriptor_count") != 4
        or strict_pair.get("line_simTicks") != EXPECTED_TICKS["line-64k"]
        or strict_pair.get("page_simTicks") != EXPECTED_TICKS["page-64k"]
    ):
        fail("strict four-context pair validation is missing or stale")

    arms_raw = {name: load_arm(campaign, name) for name in ARMS}
    arms = {name: compact_arm(arms_raw[name]) for name in ARMS}
    for arm in arms.values():
        add_xbar_totals(arm)

    native = arms["native16-64k"]
    page = arms["page-64k"]
    line = arms["line-64k"]
    for name, arm in arms.items():
        if arm["sim_ticks"] != arm["maa_total_cycles"] * 313:
            fail(f"{name} simTicks are not exactly 313 ticks per MAA cycle")
        if len(set(arm["cpu_cycles_by_worker"])) != 1:
            fail(f"{name} worker cycle windows differ")
        if arm["cpu_cycles_by_worker"][0] != arm["maa_total_cycles"]:
            fail(f"{name} CPU and MAA cycle windows differ")

    for name in ("page-64k", "line-64k"):
        direct = arms[name]["direct"]
        expected = {
            "descriptors": 4,
            "read_issues": 8192,
            "read_responses": 8192,
            "alu_issues": 8192,
            "alu_completions": 8192,
            "write_issues": 8192,
            "write_responses": 8192,
            "context_high_water": 4,
            "fallbacks": 0,
        }
        for key, value in expected.items():
            if direct[key] != value:
                fail(
                    f"{name} direct counter {key}={direct[key]}, expected {value}"
                )
    if line["direct"]["producer_line_acks"] != 8192:
        fail("line arm did not receive every exact line acknowledgement")
    if line["direct"]["page_fallback_lines"] != 0:
        fail("line arm used page fallback")
    if page["direct"]["producer_line_acks"] != 0:
        fail("page arm unexpectedly used line acknowledgement")
    if page["direct"]["page_fallback_lines"] != 8192:
        fail("page arm did not close every line through page fallback")

    binary_hash = provenance.get("gem5_sha256", "")
    if verify_binary:
        measured = file_sha256(campaign / "bin/gem5.opt")
        if measured != binary_hash:
            fail("simulator binary hash does not match frozen provenance")

    line_gap_cycles = line["maa_total_cycles"] - native["maa_total_cycles"]
    direct_line_ops = line["direct"]["read_issues"]
    logical_elements = 65_536
    producer_intermediate_requests = (
        line["virtual_write_issues"] + line["direct"]["read_issues"]
    )
    result = {
        "schema": "dx100.xrage_steady_profile.v1",
        "status": "pass",
        "campaign": str(campaign),
        "source_commit": EXPECTED_SOURCE,
        "simulator_sha256": binary_hash,
        "binary_hash_recomputed": verify_binary,
        "output_hash": EXPECTED_HASH,
        "provenance": {
            "campaign_manifest": campaign_manifest,
            "guest_sha256": strict_pair["guest_sha256"],
            "guest_source_commit": strict_pair["guest_source_commit"],
            "input_sha256": "70e3d82973d7a93300db950d2c81e9db5b6a37273b0f21da8344302ce53022d9",
            "normalized_direct_checkpoint_sha256": strict_pair[
                "checkpoint_sha256"
            ],
            "strict_four_context_pair_status": strict_pair["status"],
            "same_input": len(
                {arms_raw[name]["manifest"]["input"] for name in ARMS}
            )
            == 1,
            "all_exit_statuses_zero": True,
            "all_terminal_m5_exit": True,
            "all_exact_verifier_pass": True,
            "first_roi_stats_only": True,
        },
        "arms": arms,
        "derived": {
            "line_slowdown_vs_native": line["sim_ticks"] / native["sim_ticks"],
            "page_slowdown_vs_native": page["sim_ticks"] / native["sim_ticks"],
            "line_speedup_vs_page": page["sim_ticks"] / line["sim_ticks"],
            "line_handoff_saved_cycles": (
                page["maa_total_cycles"] - line["maa_total_cycles"]
            ),
            "line_handoff_saved_ticks": page["sim_ticks"] - line["sim_ticks"],
            "line_handoff_latency_reduction_pct": pct(
                page["sim_ticks"] - line["sim_ticks"], page["sim_ticks"]
            ),
            "line_gap_cycles_vs_native": line_gap_cycles,
            "indirect_cycle_delta": line["indirect_cycles"]
            - native["indirect_cycles"],
            "indirect_fill_cycle_delta": (
                line["indirect_fill_cycles"] - native["indirect_fill_cycles"]
            ),
            "indirect_request_cycle_delta": (
                line["indirect_request_cycles"]
                - native["indirect_request_cycles"]
            ),
            "a_line_memory_load_delta": (
                line["a_line_memory_loads"] - native["a_line_memory_loads"]
            ),
            "a_line_memory_load_increase_pct": pct(
                line["a_line_memory_loads"] - native["a_line_memory_loads"],
                native["a_line_memory_loads"],
            ),
            "producer_write_request_excess_over_lines": (
                line["virtual_write_issues"] - direct_line_ops
            ),
            "producer_write_request_excess_pct": pct(
                line["virtual_write_issues"] - direct_line_ops, direct_line_ops
            ),
            "producer_backing_plus_readback_requests": producer_intermediate_requests,
            "consumer_read_plus_write_requests": (
                line["direct"]["read_issues"] + line["direct"]["write_issues"]
            ),
            "native_alu_elements_per_compute_cycle": (
                logical_elements / native["alu_compute_cycles"]
            ),
            "line_alu_elements_per_compute_cycle": (
                logical_elements / line["alu_compute_cycles"]
            ),
            "line_alu_compute_cycle_increase": (
                line["alu_compute_cycles"] - native["alu_compute_cycles"]
            ),
            "line_alu_compute_cycle_factor": (
                line["alu_compute_cycles"] / native["alu_compute_cycles"]
            ),
            "line_alu_cycle_delta_as_gap_pct": pct(
                line["alu_compute_cycles"] - native["alu_compute_cycles"],
                line_gap_cycles,
            ),
            "native_stream_bytes_per_request_cycle": (
                (logical_elements * 4 + logical_elements * 8)
                / native["stream_request_cycles"]
            ),
            "native_stream_lines_per_request_cycle": (
                native["stream_cache_lines"] / native["stream_request_cycles"]
            ),
            "line_cpu_instruction_delta": (
                line["cpu_instructions_total"]
                - native["cpu_instructions_total"]
            ),
            "line_cpu_instruction_increase_pct": pct(
                line["cpu_instructions_total"]
                - native["cpu_instructions_total"],
                native["cpu_instructions_total"],
            ),
            "line_xbar_packet_delta": (
                line["xbar"]["observed_packets"]
                - native["xbar"]["observed_packets"]
            ),
            "line_xbar_packet_increase_pct": pct(
                line["xbar"]["observed_packets"]
                - native["xbar"]["observed_packets"],
                native["xbar"]["observed_packets"],
            ),
            "line_xbar_byte_delta": (
                line["xbar"]["observed_bytes"]
                - native["xbar"]["observed_bytes"]
            ),
            "line_xbar_byte_increase_pct": pct(
                line["xbar"]["observed_bytes"]
                - native["xbar"]["observed_bytes"],
                native["xbar"]["observed_bytes"],
            ),
            "line_dram_read_command_delta": (
                line["dram"]["read_commands_total"]
                - native["dram"]["read_commands_total"]
            ),
            "line_dram_read_command_change_pct": pct(
                line["dram"]["read_commands_total"]
                - native["dram"]["read_commands_total"],
                native["dram"]["read_commands_total"],
            ),
            "line_dram_queue_full_change_pct": pct(
                line["dram"]["queue_full_total"]
                - native["dram"]["queue_full_total"],
                native["dram"]["queue_full_total"],
            ),
            "line_dram_queue_empty_factor": (
                line["dram"]["queue_empty_total"]
                / native["dram"]["queue_empty_total"]
            ),
            "line_retry_increase_vs_page": (
                line["direct"]["retries"] - page["direct"]["retries"]
            ),
            "line_retry_factor_vs_page": (
                line["direct"]["retries"] / page["direct"]["retries"]
            ),
            "line_l3_read_req_10_reduction_vs_page": (
                page["l3"]["read_req_10_maa"] - line["l3"]["read_req_10_maa"]
            ),
            "line_l3_read_req_10_reduction_pct": pct(
                page["l3"]["read_req_10_maa"] - line["l3"]["read_req_10_maa"],
                page["l3"]["read_req_10_maa"],
            ),
            "total_line_credit_capacity": 4 * 16,
            "observed_line_credit_fraction": (
                line["direct"]["credit_high_water"] / (4 * 16)
            ),
        },
        "source_findings": {
            "shared_alu": (
                "HybridConsumerContextQueue has one computeInFlight owner; "
                "MAA also requires aluUnitsIdle before each 64-byte launch."
            ),
            "line_alu_charge": (
                "startDirectLine charges ceil(8 FP64 elements / 16 lanes) * "
                "1 cycle for every 64-byte line and schedules one completion event."
            ),
            "credits": (
                "Each of four contexts owns 16 fixed 64-byte buffers; the fixed "
                "request-record array has 64 entries."
            ),
            "address_exclusion": (
                "Every direct request is rejected while the same physical address "
                "is present in generic, direct, or deferred ownership."
            ),
            "retry_serialization": (
                "One global directRetirementRetryPacket blocks scheduling until its "
                "selected cache port accepts it, even though four ports exist."
            ),
            "readback_write_path": (
                "The producer writes coherent backing; each consumer line then "
                "issues ReadReq, transforms the fixed buffer in place, and retains "
                "it through an acknowledged destination WriteReq."
            ),
        },
        "interpretation": {
            "measured": [
                "The line arm is exact and terminal with four live contexts, 8,192 "
                "line acknowledgements, and exact read/ALU/write closure.",
                "Virtual indirect fill and request cycles dominate the delta; DRAM "
                "read commands and queue pressure are lower than native, not higher.",
                "Only 16 of 64 possible line credits/request records were observed, "
                "with zero credit and exact-address stalls.",
                "Line handoff reduces latency and observed L3 read traffic despite "
                "many more downstream cache-send retries than page handoff.",
            ],
            "inference": [
                "The primary gap is serialized request generation and coherent "
                "intermediate materialization, not DRAM bandwidth, credit capacity, "
                "or exact-address exclusion.",
                "Early line handoff likely converts some later L3 reads into closer "
                "producer/consumer transfers, but the available counters do not "
                "identify every response source.",
                "Per-line ALU launches waste half the 16-lane FP64 width and add "
                "8,192 scheduling events, but the charged compute-cycle delta is too "
                "small to explain most of the 2.417x slowdown.",
            ],
        },
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "campaign", nargs="?", type=Path, default=DEFAULT_CAMPAIGN
    )
    parser.add_argument(
        "--verify-binary",
        action="store_true",
        help="recompute SHA-256 of the frozen 871 MB simulator binary",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze(args.campaign.resolve(), args.verify_binary)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered)


if __name__ == "__main__":
    main()
