"""Run one native Branson process with opcode-5 tally replacement."""

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
parser.add_argument("--input", required=True)
parser.add_argument("--cwd", required=True)
parser.add_argument("--metadata", required=True)
parser.add_argument("--submission-report", required=True)
arguments = parser.parse_args()


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


with open(arguments.metadata, encoding="utf-8") as stream:
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
    descriptor_table_base=metadata["descriptor_paddr"],
    descriptor_slots=1,
    max_descriptor_items=metadata["max_descriptor_items"],
    control_addr=metadata["control_paddr"],
    control_size=metadata["control_bytes"],
    operation_entries=metadata["operation_entries"],
    continuation_entries=64,
    branson_active_context_limit=16,
    branson_context_quantum=4,
    branson_event_compute_latency=4,
    branson_event_compute_initiation_interval=1,
    branson_event_compute_units=1,
    line_entries=32,
    update_entries=64,
    update_banks=8,
    update_issue_width=1,
    logical_admission_width=2,
    line_issue_width=1,
    retirement_width=2,
    exit_on_completion=False,
)
system.maa_cache = MAACoherenceCache()
system.lanl_maa.mem_side = system.maa_cache.cpu_sides
system.maa_cache.mem_sides = system.membus.cpu_side_ports
system.lanl_maa.control = system.membus.mem_side_ports
system.system_port = system.membus.cpu_side_ports

process = Process()
process.cmd = [arguments.binary, arguments.input]
process.cwd = arguments.cwd
process.env = [
    "LANG=C",
    "LC_ALL=C",
    "OMP_NUM_THREADS=1",
    "LD_HWCAP_MASK=0",
    (
        "GLIBC_TUNABLES=glibc.cpu.hwcaps="
        "-SSE4_2,-AVX,-AVX2,-AVX512F,-AVX512VL"
    ),
    "OMPI_MCA_btl=self",
    "OMPI_MCA_pml=ob1",
    "OMPI_MCA_shmem=mmap",
    "BRANSON_LANL_MAA_SUBMIT=1",
    "BRANSON_LANL_MAA_REPLACE_TALLIES=1",
    "BRANSON_LANL_MAA_SUBMIT_TIMESTEP=1",
    "BRANSON_LANL_MAA_MAPPING_COOKIE=branson-lanl-maa-opcode5-mapped-v1",
    f"BRANSON_LANL_MAA_SUBMIT_REPORT={arguments.submission_report}",
]
system.workload = SEWorkload.init_compatible(arguments.binary)
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
print(
    f"LANLMAA_SIM_TERMINAL code={event.getCode()} " f"cause={event.getCause()}"
)
if event.getCode() != 0:
    raise RuntimeError(
        "native Branson process submission failed: "
        f"cause={event.getCause()} code={event.getCode()}"
    )
