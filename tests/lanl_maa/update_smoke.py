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
parser.add_argument("--invalid-banks", action="store_true")
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

verification_values = [106, 230, 309, 415, 519, 623, 4]
if args.corrupt_oracle:
    verification_values[0] += 1

system.lanl_maa = LANLMAA(
    addresses=[
        0x100,
        0x108,
        0x100,
        0x110,
        0x108,
        0x118,
        0x100,
        0x110,
        0x118,
        0x120,
        0x128,
        0x128,
        0x130,
    ],
    update_mode=True,
    update_values=[1, 10, 2, 4, 20, 7, 3, 5, 8, 19, 11, 12, 10],
    verification_addresses=[
        0x100,
        0x108,
        0x110,
        0x118,
        0x120,
        0x128,
        0x130,
    ],
    verification_values=verification_values,
    operation_entries=6,
    update_entries=4,
    update_banks=3 if args.invalid_banks else 2,
    update_issue_width=1,
    line_entries=2,
    logical_admission_width=2,
    retirement_width=2,
)

system.lanl_maa.mem_side = system.membus.cpu_side_ports
system.memory.port = system.membus.mem_side_ports
system.system_port = system.membus.cpu_side_ports

root = Root(full_system=False, system=system)
m5.instantiate()
event = m5.simulate()
m5.stats.dump()

expected_cause = (
    "LANLMAA update verification failed"
    if args.corrupt_oracle
    else "LANLMAA update complete"
)
expected_code = 2 if args.corrupt_oracle else 0
if event.getCause() != expected_cause or event.getCode() != expected_code:
    raise RuntimeError(
        f"unexpected exit: cause={event.getCause()} code={event.getCode()}"
    )
