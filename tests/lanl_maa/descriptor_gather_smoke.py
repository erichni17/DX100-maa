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
parser.add_argument("--bad-magic", action="store_true")
parser.add_argument("--overlap-output", action="store_true")
parser.add_argument("--bad-target", action="store_true")
parser.add_argument("--unmapped-target", action="store_true")
args = parser.parse_args()

if (
    sum(
        (
            args.bad_magic,
            args.overlap_output,
            args.bad_target,
            args.unmapped_target,
        )
    )
    > 1
):
    raise RuntimeError("descriptor negative cases are mutually exclusive")

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

if args.bad_magic:
    slot = 3
elif args.overlap_output:
    slot = 1
elif args.bad_target:
    slot = 0
elif args.unmapped_target:
    slot = 4
else:
    slot = 2
negative = (
    args.bad_magic
    or args.overlap_output
    or args.bad_target
    or args.unmapped_target
)

system.lanl_maa = LANLMAA(
    descriptor_mode=True,
    descriptor_table_base=0x800,
    descriptor_slots=5,
    max_descriptor_items=16,
    control_addr=control_addr,
    control_size=0x1000,
    operation_entries=16,
    line_entries=2,
    line_banks=2,
    logical_admission_width=2,
    line_issue_width=1,
    retirement_width=2,
    exit_on_completion=negative,
)
system.submitter = LANLMAAControlTester(
    control_addr=control_addr,
    doorbell_slot=slot,
    writes=1 if negative else 2,
    start_cycle=1,
)

if not negative:
    result_values = [
        0x0123456789ABCDEF,
        0xFEDCBA9876543210,
        0x1122334455667788,
        0xFEDCBA9876543210,
        0x8877665544332211,
        0xA5A5A5A55A5A5A5A,
        0x0123456789ABCDEF,
        0xDEADBEEFCAFEBABE,
        0x0F1E2D3C4B5A6978,
        0x13579BDF2468ACE0,
        0x55AA55AAAA55AA55,
        0xDEADBEEFCAFEBABE,
    ]
    system.final_verifier = LANLMAA(
        addresses=[0xA00 + index * 8 for index in range(len(result_values))]
        + [0xB00, 0xB08, 0xB10, 0xB18],
        expected_values=result_values
        + [0x0001000143414D4C, 2, len(result_values), len(result_values)],
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
