"""Run one mixed-ABI dense/oracle UMT guest through terminal Error drain."""

import argparse
import json

import m5
from m5.objects import (
    LANLMAA,
    AddrRange,
    Cache,
    Process,
    Root,
    SEWorkload,
    SimpleMemory,
    SrcClockDomain,
    System,
    SystemXBar,
    VoltageDomain,
    X86TimingSimpleCPU,
)

DATA_VADDR = 0x1000000000
DATA_PADDR = 0x10000000
DATA_BYTES = 0x00100000
CONTROL_VADDR = DATA_VADDR + DATA_BYTES
CONTROL_PADDR = 0x20000000
CONTROL_BYTES = 0x1000
EXPECTED_CASES = [
    (4, 32, False),
    (5, 64, False),
    (4, 9, False),
    (5, 33, False),
    (4, 8, True),
]
EXPECTED_EDGE_MASK = "0x0a54a18b"

parser = argparse.ArgumentParser()
parser.add_argument("--binary", required=True)
parser.add_argument("--metadata", required=True)
args = parser.parse_args()


class L1Cache(Cache):
    assoc = 8
    tag_latency = 2
    data_latency = 2
    response_latency = 2
    mshrs = 8
    tgts_per_mshr = 20


class MAACoherenceCache(Cache):
    size = "4KiB"
    assoc = 2
    tag_latency = 2
    data_latency = 2
    response_latency = 2
    mshrs = 8
    tgts_per_mshr = 2
    write_buffers = 2


with open(args.metadata, encoding="utf-8") as stream:
    metadata = json.load(stream)
if metadata.get("schema") != "lanl-maa-umt-ordered-wave-mixed-evidence-v3":
    raise ValueError("mixed UMT evidence metadata schema changed")
if metadata.get("validation_mode") not in ("confirmation", "calibration"):
    raise ValueError("mixed UMT evidence validation mode changed")
build_manifest_sha256 = metadata.get("build_manifest_sha256")
if (
    not isinstance(build_manifest_sha256, str)
    or len(build_manifest_sha256) != 64
    or any(
        character not in "0123456789abcdef"
        for character in build_manifest_sha256
    )
):
    raise ValueError("mixed UMT evidence lacks a build-manifest SHA-256")
cell = metadata.get("cell")
valid_cells = {
    (24, 1, "X86_UMT_T24_W1"),
    (24, 2, "X86_UMT_T24_W2"),
    (32, 1, "X86_UMT_T32_W1"),
    (32, 2, "X86_UMT_T32_W2"),
}
if (
    not isinstance(cell, dict)
    or (
        cell.get("compute_tokens"),
        cell.get("fp_issue_width"),
        cell.get("variant"),
    )
    not in valid_cells
):
    raise ValueError("mixed UMT evidence cell is invalid")
if metadata.get("build_manifest", {}).get("cell") != cell:
    raise ValueError("mixed UMT manifest and metadata cells differ")
timing_contract_sha256 = metadata.get("timing_contract_sha256")
if metadata["validation_mode"] == "confirmation":
    if (
        not isinstance(timing_contract_sha256, str)
        or len(timing_contract_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in timing_contract_sha256
        )
    ):
        raise ValueError(
            "mixed UMT confirmation lacks timing-contract SHA-256"
        )
elif timing_contract_sha256 is not None:
    raise ValueError(
        "mixed UMT calibration may not predeclare a timing contract"
    )
observed_cases = [
    (case.get("abi_version"), case.get("groups"), case.get("expect_error"))
    for case in metadata.get("cases", [])
]
if observed_cases != EXPECTED_CASES:
    raise ValueError("mixed UMT interleaved descriptor sequence changed")
if any(case.get("oracle_sha256") is None for case in metadata["cases"][:-1]):
    raise ValueError("successful mixed UMT case lacks an oracle fingerprint")
if metadata["cases"][-1].get("oracle_sha256") is not None:
    raise ValueError("error case may not claim a completion oracle")
if metadata.get("edge_count") != 12:
    raise ValueError("mixed UMT evidence no longer uses exactly 12 edges")
if metadata.get("edge_mask") != EXPECTED_EDGE_MASK:
    raise ValueError("mixed UMT evidence edge mask changed")
bad_value = metadata.get("bad_active_value", {})
if bad_value != {
    "case_index": 4,
    "plane": 8,
    "group": 7,
    "bits": "0x7ff0000000000001",
    "expected_error": 18,
}:
    raise ValueError("mixed UMT fail-closed active value changed")

system = System(
    cache_line_size=64,
    mem_mode="timing",
    mem_ranges=[AddrRange(CONTROL_PADDR)],
)
system.voltage_domain = VoltageDomain(voltage="1V")
system.clk_domain = SrcClockDomain(
    clock="1GHz", voltage_domain=system.voltage_domain
)
system.cpu = X86TimingSimpleCPU()
system.membus = SystemXBar()
system.icache = L1Cache(size="32KiB")
system.dcache = L1Cache(size="32KiB")
system.cpu.icache_port = system.icache.cpu_sides
system.cpu.dcache_port = system.dcache.cpu_sides
system.icache.mem_sides = system.membus.cpu_side_ports
system.dcache.mem_sides = system.membus.cpu_side_ports
system.cpu.createInterruptController()
system.cpu.interrupts[0].pio = system.membus.mem_side_ports
system.cpu.interrupts[0].int_requestor = system.membus.cpu_side_ports
system.cpu.interrupts[0].int_responder = system.membus.mem_side_ports

system.memory = SimpleMemory(range=system.mem_ranges[0], latency="20ns")
system.memory.port = system.membus.mem_side_ports
system.lanl_maa = LANLMAA(
    descriptor_mode=True,
    descriptor_table_base=DATA_PADDR,
    descriptor_slots=8,
    max_descriptor_items=64,
    control_addr=CONTROL_PADDR,
    control_size=CONTROL_BYTES,
    operation_entries=64,
    continuation_entries=64,
    line_entries=32,
    update_entries=64,
    update_banks=8,
    update_issue_width=1,
    logical_admission_width=2,
    line_issue_width=1,
    retirement_width=2,
    model_payload_overlay_ports=True,
    exit_on_completion=False,
)
system.maa_cache = MAACoherenceCache()
system.lanl_maa.mem_side = system.maa_cache.cpu_sides
system.maa_cache.mem_sides = system.membus.cpu_side_ports
system.lanl_maa.control = system.membus.mem_side_ports
system.system_port = system.membus.cpu_side_ports

process = Process(cmd=[args.binary])
system.workload = SEWorkload.init_compatible(args.binary)
system.cpu.workload = process
system.cpu.createThreads()

root = Root(full_system=False, system=system)
m5.instantiate()
process.map(DATA_VADDR, DATA_PADDR, DATA_BYTES, True)
process.map(CONTROL_VADDR, CONTROL_PADDR, CONTROL_BYTES, False)
event = m5.simulate()
m5.stats.dump()
print(
    "LANLMAA_UMT_MIXED_EVIDENCE_TERMINAL "
    f"code={event.getCode()} cause={event.getCause()}"
)
if event.getCode() != 0:
    raise RuntimeError(
        "mixed UMT evidence guest failed: "
        f"cause={event.getCause()} code={event.getCode()}"
    )
