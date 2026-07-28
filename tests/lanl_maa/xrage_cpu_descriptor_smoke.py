import argparse
import json

import m5
from m5.objects import (
    LANLMAA,
    AddrRange,
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
args = parser.parse_args()

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
