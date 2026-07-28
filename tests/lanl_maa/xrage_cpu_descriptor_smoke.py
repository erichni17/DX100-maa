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

parser = argparse.ArgumentParser()
parser.add_argument("--binary", required=True)
parser.add_argument("--metadata", required=True)
parser.add_argument("--l1-caches", action="store_true")
parser.add_argument("--maa-cache-size", default="4KiB")
parser.add_argument("--maa-cache-assoc", type=int, default=4)
parser.add_argument("--maa-cache-mshrs", type=int, default=8)
parser.add_argument("--maa-cache-targets-per-mshr", type=int, default=2)
parser.add_argument("--maa-cache-write-buffers", type=int, default=2)
args = parser.parse_args()


class L1Cache(Cache):
    assoc = 8
    tag_latency = 2
    data_latency = 2
    response_latency = 2
    mshrs = 8
    tgts_per_mshr = 20


class L1ICache(L1Cache):
    size = "32KiB"


class L1DCache(L1Cache):
    size = "32KiB"


class MAACoherenceCache(L1Cache):
    # Selected for the XRAGE/SPARTA-derived/Branson-derived microbenchmark
    # envelope; this is explicit accelerator hardware, not a free system cache.
    pass


with open(args.metadata, encoding="utf-8") as stream:
    metadata = json.load(stream)

system = System(
    cache_line_size=64,
    mem_mode="timing",
    mem_ranges=[AddrRange(metadata["control_paddr"])],
)
system.voltage_domain = VoltageDomain(voltage="1V")
system.clk_domain = SrcClockDomain(
    clock="1GHz", voltage_domain=system.voltage_domain
)
system.cpu = X86TimingSimpleCPU()
system.membus = SystemXBar()
if args.l1_caches:
    system.icache = L1ICache()
    system.dcache = L1DCache()
    system.cpu.icache_port = system.icache.cpu_sides
    system.cpu.dcache_port = system.dcache.cpu_sides
    system.icache.mem_sides = system.membus.cpu_side_ports
    system.dcache.mem_sides = system.membus.cpu_side_ports
else:
    system.cpu.icache_port = system.membus.cpu_side_ports
    system.cpu.dcache_port = system.membus.cpu_side_ports
system.cpu.createInterruptController()
system.cpu.interrupts[0].pio = system.membus.mem_side_ports
system.cpu.interrupts[0].int_requestor = system.membus.cpu_side_ports
system.cpu.interrupts[0].int_responder = system.membus.mem_side_ports

system.memory = SimpleMemory(range=system.mem_ranges[0], latency="20ns")
system.memory.port = system.membus.mem_side_ports
system.lanl_maa = LANLMAA(
    descriptor_mode=True,
    descriptor_table_base=metadata["descriptor_paddr"],
    descriptor_slots=1,
    max_descriptor_items=metadata["items"],
    control_addr=metadata["control_paddr"],
    control_size=metadata["control_bytes"],
    operation_entries=metadata["items"],
    continuation_entries=4,
    line_entries=32,
    logical_admission_width=2,
    line_issue_width=1,
    retirement_width=2,
    exit_on_completion=False,
)
if args.l1_caches:
    system.maa_cache = MAACoherenceCache(
        size=args.maa_cache_size,
        assoc=args.maa_cache_assoc,
        mshrs=args.maa_cache_mshrs,
        tgts_per_mshr=args.maa_cache_targets_per_mshr,
        write_buffers=args.maa_cache_write_buffers,
    )
    system.lanl_maa.mem_side = system.maa_cache.cpu_sides
    system.maa_cache.mem_sides = system.membus.cpu_side_ports
else:
    system.lanl_maa.mem_side = system.membus.cpu_side_ports
system.lanl_maa.control = system.membus.mem_side_ports
system.system_port = system.membus.cpu_side_ports

process = Process(cmd=[args.binary])
system.workload = SEWorkload.init_compatible(args.binary)
system.cpu.workload = process
system.cpu.createThreads()

root = Root(full_system=False, system=system)
m5.instantiate()
process.map(
    metadata["data_vaddr"],
    metadata["data_paddr"],
    metadata["data_bytes"],
    True,
)
process.map(
    metadata["control_vaddr"],
    metadata["control_paddr"],
    metadata["control_bytes"],
    False,
)
event = m5.simulate()
m5.stats.dump()

if event.getCode() != 0:
    raise RuntimeError(
        f"CPU descriptor program failed: cause={event.getCause()} "
        f"code={event.getCode()}"
    )
