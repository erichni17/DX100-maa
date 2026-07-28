import argparse

import m5
from m5.objects import (
    LANLMAA,
    AddrRange,
    LANLMAAControlTester,
    Root,
    SimpleMemory,
    SrcClockDomain,
    System,
    SystemXBar,
    VoltageDomain,
)

parser = argparse.ArgumentParser()
parser.add_argument("--image", required=True)
parser.add_argument(
    "--case",
    choices=("reserved-start", "over-max", "bad-neighbor", "bad-record"),
    required=True,
)
args = parser.parse_args()

slots = {
    "reserved-start": 0,
    "over-max": 1,
    "bad-neighbor": 2,
    "bad-record": 3,
}
control_addr = 0x100000

system = System(
    cache_line_size=64,
    mem_mode="timing",
    mem_ranges=[AddrRange("1MiB")],
)
system.voltage_domain = VoltageDomain(voltage="1V")
system.clk_domain = SrcClockDomain(
    clock="1GHz", voltage_domain=system.voltage_domain
)
system.membus = SystemXBar()
system.memory = SimpleMemory(
    range=system.mem_ranges[0], latency="20ns", image_file=args.image
)
system.lanl_maa = LANLMAA(
    descriptor_mode=True,
    descriptor_table_base=0x800,
    descriptor_slots=4,
    max_descriptor_items=8,
    control_addr=control_addr,
    control_size=0x1000,
    operation_entries=8,
    continuation_entries=2,
    line_entries=2,
    logical_admission_width=2,
    line_issue_width=1,
    retirement_width=2,
    exit_on_completion=True,
)
system.submitter = LANLMAAControlTester(
    control_addr=control_addr,
    doorbell_slot=slots[args.case],
    writes=1,
    start_cycle=1,
)

system.lanl_maa.mem_side = system.membus.cpu_side_ports
system.lanl_maa.control = system.membus.mem_side_ports
system.submitter.port = system.membus.cpu_side_ports
system.memory.port = system.membus.mem_side_ports
system.system_port = system.membus.cpu_side_ports

root = Root(full_system=False, system=system)
m5.instantiate()
event = m5.simulate()
m5.stats.dump()

if event.getCause() != "LANLMAA descriptor rejected" or event.getCode() != 2:
    raise RuntimeError(
        f"unexpected exit: cause={event.getCause()} code={event.getCode()}"
    )
