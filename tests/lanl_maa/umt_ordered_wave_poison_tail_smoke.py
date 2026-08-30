"""Run the dual-ABI UMT ordered-wave full/partial poison-tail CPU smoke."""

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
if metadata.get("schema") != "lanl-maa-umt64-poison-tail-v2":
    raise ValueError("UMT poison-tail metadata schema changed")
allowed_group_counts = {1, 7, 8, 9, 16, 24, 31, 32, 33, 63, 64}
if (
    not metadata["group_counts"]
    or len(set(metadata["group_counts"])) != len(metadata["group_counts"])
    or not set(metadata["group_counts"]).issubset(allowed_group_counts)
):
    raise ValueError("UMT64 poison-tail group matrix changed")
if metadata.get("abi_version") not in (4, 5):
    raise ValueError("UMT poison-tail ABI version is invalid")
if metadata["abi_version"] == 4 and max(metadata["group_counts"]) > 32:
    raise ValueError("UMT ABI-v4 poison-tail group count exceeds D32")
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
    raise ValueError("UMT poison-tail metadata cell is invalid")
if metadata.get("build_manifest", {}).get("cell") != cell:
    raise ValueError("UMT poison-tail manifest and metadata cells differ")
build_manifest_sha256 = metadata.get("build_manifest_sha256")
if (
    not isinstance(build_manifest_sha256, str)
    or len(build_manifest_sha256) != 64
    or any(
        character not in "0123456789abcdef"
        for character in build_manifest_sha256
    )
):
    raise ValueError("UMT poison-tail lacks a build-manifest SHA-256")
mode = metadata.get("validation_mode")
line_contract_sha256 = metadata.get("line_read_contract_sha256")
if mode == "d32_line_read_calibration":
    if metadata["abi_version"] != 4 or line_contract_sha256 is not None:
        raise ValueError("UMT poison-tail calibration boundary changed")
elif mode == "confirmation":
    if metadata["abi_version"] == 4 and (
        not isinstance(line_contract_sha256, str)
        or len(line_contract_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in line_contract_sha256
        )
    ):
        raise ValueError("D32 confirmation lacks a line-contract SHA-256")
    if metadata["abi_version"] == 5 and line_contract_sha256 is not None:
        raise ValueError("D64 may not carry a D32 line contract")
else:
    raise ValueError("UMT poison-tail validation mode changed")

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
    "LANLMAA_UMT64_POISON_TAIL_TERMINAL "
    f"code={event.getCode()} cause={event.getCause()}"
)
if event.getCode() != 0:
    raise RuntimeError(
        "UMT64 poison-tail program failed: "
        f"cause={event.getCause()} code={event.getCode()}"
    )
