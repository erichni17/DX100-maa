import argparse

import m5
from m5.objects import (
    LANLMAA,
    AddrRange,
    Root,
    SimpleMemory,
    SrcClockDomain,
    System,
    SystemXBar,
    VoltageDomain,
)

parser = argparse.ArgumentParser()
parser.add_argument("--image", required=True)
parser.add_argument("--corrupt-oracle", action="store_true")
args = parser.parse_args()

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

common_update = {
    "update_mode": True,
    "verification_addresses": [0x120],
    "verification_values": [500],
    "operation_entries": 4,
    "update_entries": 4,
    "update_banks": 2,
    "update_issue_width": 1,
    "line_entries": 2,
    "logical_admission_width": 2,
    "retirement_width": 2,
    "start_cycle": 1,
    "exit_on_completion": False,
}

system.lanl_maa_a = LANLMAA(
    addresses=[0x100, 0x108, 0x100, 0x110],
    update_values=[1, 10, 2, 4],
    **common_update,
)
system.lanl_maa_b = LANLMAA(
    addresses=[0x100, 0x108, 0x118, 0x100],
    update_values=[5, 20, 7, 8],
    **common_update,
)

expected = [116, 230, 304, 407]
if args.corrupt_oracle:
    expected[0] += 1
system.final_verifier = LANLMAA(
    addresses=[0x100, 0x108, 0x110, 0x118],
    expected_values=expected,
    operation_entries=4,
    line_entries=2,
    logical_admission_width=2,
    line_issue_width=1,
    retirement_width=2,
    start_cycle=250,
)

system.lanl_maa_a.mem_side = system.membus.cpu_side_ports
system.lanl_maa_b.mem_side = system.membus.cpu_side_ports
system.final_verifier.mem_side = system.membus.cpu_side_ports
system.memory.port = system.membus.mem_side_ports
system.system_port = system.membus.cpu_side_ports

root = Root(full_system=False, system=system)
m5.instantiate()
event = m5.simulate()
m5.stats.dump()

expected_cause = (
    "LANLMAA gather verification failed"
    if args.corrupt_oracle
    else "LANLMAA gather complete"
)
expected_code = 2 if args.corrupt_oracle else 0
if event.getCause() != expected_cause or event.getCode() != expected_code:
    raise RuntimeError(
        f"unexpected exit: cause={event.getCause()} code={event.getCode()}"
    )
