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
    choices=(
        "positive",
        "bad-initial",
        "bad-continuation",
        "overlap",
        "exhaust",
    ),
    default="positive",
)
args = parser.parse_args()

slots = {
    "positive": 0,
    "bad-initial": 1,
    "bad-continuation": 2,
    "overlap": 3,
    "exhaust": 4,
}
negative = args.case != "positive"
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
    descriptor_slots=5,
    max_descriptor_items=8,
    control_addr=control_addr,
    control_size=0x1000,
    operation_entries=8,
    continuation_entries=2,
    line_entries=2,
    line_banks=2,
    logical_admission_width=2,
    line_issue_width=1,
    retirement_width=2,
    exit_on_completion=negative,
)
system.submitter = LANLMAAControlTester(
    control_addr=control_addr,
    doorbell_slot=slots[args.case],
    writes=1 if negative else 2,
    start_cycle=1,
)

if not negative:
    expected = [60, 33, 18, 3, 60, 18]
    completion = [0x0002000143414D4C, 0, len(expected), len(expected)]
    system.final_verifier = LANLMAA(
        addresses=[0xA00 + index * 8 for index in range(len(expected))]
        + [0xB00 + index * 8 for index in range(4)],
        expected_values=expected + completion,
        operation_entries=8,
        line_entries=2,
        line_banks=2,
        logical_admission_width=2,
        line_issue_width=1,
        retirement_width=2,
        start_cycle=1000,
    )
    system.final_verifier.mem_side = system.membus.cpu_side_ports

system.lanl_maa.mem_side = system.membus.cpu_side_ports
system.lanl_maa.control = system.membus.mem_side_ports
system.submitter.port = system.membus.cpu_side_ports
system.memory.port = system.membus.mem_side_ports
system.system_port = system.membus.cpu_side_ports

root = Root(full_system=False, system=system)
m5.instantiate()
event = m5.simulate()
m5.stats.dump()

expected_cause = (
    "LANLMAA descriptor rejected" if negative else "LANLMAA gather complete"
)
expected_code = 2 if negative else 0
if event.getCause() != expected_cause or event.getCode() != expected_code:
    raise RuntimeError(
        f"unexpected exit: cause={event.getCause()} code={event.getCode()}"
    )
