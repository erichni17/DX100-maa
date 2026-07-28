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
control_addr = 0x1000000

system = System(
    cache_line_size=64,
    mem_mode="timing",
    mem_ranges=[AddrRange("16MiB")],
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
    descriptor_table_base=metadata["descriptor_table"],
    descriptor_slots=2,
    max_descriptor_items=items,
    control_addr=control_addr,
    control_size=0x1000,
    operation_entries=items,
    continuation_entries=4,
    line_entries=32,
    logical_admission_width=2,
    line_issue_width=1,
    retirement_width=2,
    exit_on_completion=False,
)
system.submitter0 = LANLMAAControlTester(
    control_addr=control_addr,
    doorbell_slot=0,
    writes=1,
    start_cycle=1,
)
if metadata["busy_submission"]:
    system.busy_submitter = LANLMAAControlTester(
        control_addr=control_addr,
        doorbell_slot=1,
        writes=1,
        start_cycle=2,
    )
system.submitter1 = LANLMAAControlTester(
    control_addr=control_addr,
    doorbell_slot=1,
    writes=1,
    start_cycle=metadata["second_submission_cycle"],
)

verification_addresses = []
verification_values = []
completion_qword0 = 0x0001000143414D4C
for window in metadata["verified_windows"]:
    verification_addresses.extend(
        window["result_vector"] + index * 8 for index in range(items)
    )
    verification_values.extend(window["expected_results"])
for window in metadata["verified_windows"]:
    completion = window["completion_record"]
    verification_addresses.extend(completion + index * 8 for index in range(4))
    verification_values.extend(
        [completion_qword0, window["slot"], items, items]
    )

system.final_verifier = LANLMAA(
    addresses=verification_addresses,
    expected_values=verification_values,
    operation_entries=len(verification_addresses),
    line_entries=32,
    logical_admission_width=2,
    line_issue_width=1,
    retirement_width=2,
    start_cycle=metadata["verification_start_cycle"],
)

system.final_verifier.mem_side = system.membus.cpu_side_ports
system.lanl_maa.mem_side = system.membus.cpu_side_ports
system.lanl_maa.control = system.membus.mem_side_ports
system.submitter0.port = system.membus.cpu_side_ports
if metadata["busy_submission"]:
    system.busy_submitter.port = system.membus.cpu_side_ports
system.submitter1.port = system.membus.cpu_side_ports
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
