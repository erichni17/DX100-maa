import argparse
import json

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
parser.add_argument("--metadata", required=True)
args = parser.parse_args()

with open(args.metadata, encoding="utf-8") as stream:
    metadata = json.load(stream)

items = metadata["descriptor_items"]
result_base = metadata["result_vector"]
completion = metadata["completion_record"]
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
    descriptor_table_base=metadata["descriptor_address"],
    descriptor_slots=1,
    max_descriptor_items=items,
    control_addr=control_addr,
    control_size=0x1000,
    operation_entries=items,
    continuation_entries=4,
    line_entries=4,
    logical_admission_width=2,
    line_issue_width=1,
    retirement_width=2,
    exit_on_completion=False,
)
system.submitter = LANLMAAControlTester(
    control_addr=control_addr,
    doorbell_slot=0,
    writes=2,
    start_cycle=1,
)

expected = metadata["expected_results"]
completion_values = [0x0002000143414D4C, 0, items, items]
system.final_verifier = LANLMAA(
    addresses=[result_base + index * 8 for index in range(items)]
    + [completion + index * 8 for index in range(4)],
    expected_values=expected + completion_values,
    operation_entries=items + 4,
    line_entries=4,
    logical_admission_width=2,
    line_issue_width=1,
    retirement_width=2,
    start_cycle=5000,
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

if event.getCause() != "LANLMAA gather complete" or event.getCode() != 0:
    raise RuntimeError(
        f"unexpected exit: cause={event.getCause()} code={event.getCode()}"
    )
