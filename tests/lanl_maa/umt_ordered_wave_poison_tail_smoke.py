"""Run the ABI-v5 UMT64 full/partial poison-tail CPU smoke."""

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
if metadata["group_counts"] != [1, 7, 8, 9, 31, 32, 33, 63, 64]:
    raise ValueError("UMT64 poison-tail group matrix changed")

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
