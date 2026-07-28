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
parser.add_argument("--max-steps", type=int, default=4)
parser.add_argument("--expect-exhaustion", action="store_true")
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

system.lanl_maa = LANLMAA(
    addresses=[0x000, 0x010, 0x030, 0x000],
    expected_values=[33, 35, 32, 33],
    dependent_mode=True,
    continuation_entries=2,
    max_continuation_steps=args.max_steps,
    operation_entries=4,
    line_entries=2,
    logical_admission_width=2,
    line_issue_width=1,
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
    "LANLMAA cell walk verification failed"
    if args.expect_exhaustion
    else "LANLMAA cell walk complete"
)
expected_code = 2 if args.expect_exhaustion else 0
if event.getCause() != expected_cause or event.getCode() != expected_code:
    raise RuntimeError(
        f"unexpected exit: cause={event.getCause()} code={event.getCode()}"
    )
