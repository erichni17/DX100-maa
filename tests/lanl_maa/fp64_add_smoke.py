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
parser.add_argument("--corrupt-fp-oracle", action="store_true")
parser.add_argument("--corrupt-final-oracle", action="store_true")
parser.add_argument("--invalid-tolerance", action="store_true")
parser.add_argument("--nonfinite-operand", action="store_true")
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
    "operation_entries": 2,
    "update_entries": 2,
    "update_banks": 1,
    "update_issue_width": 1,
    "line_entries": 2,
    "logical_admission_width": 2,
    "retirement_width": 2,
    "start_cycle": 1,
    "exit_on_completion": False,
    "verification_abs_tolerance": -1.0 if args.invalid_tolerance else 0.0,
    "verification_rel_tolerance": 0.0,
}

relaxed_operands = [1.0e16, -1.0e16]
if args.nonfinite_operand:
    relaxed_operands[0] = float("inf")
system.relaxed_reducer = LANLMAA(
    addresses=[0x100, 0x100],
    update_fp_values=relaxed_operands,
    update_operation="fp64_add_relaxed",
    verification_addresses=[0x100],
    verification_fp_values=[2.0 if args.corrupt_fp_oracle else 1.0],
    **common_update,
)
system.strict_reducer = LANLMAA(
    addresses=[0x108, 0x108],
    update_fp_values=[1.0e16, -1.0e16],
    update_operation="fp64_add_strict",
    verification_addresses=[0x108],
    verification_fp_values=[0.0],
    **common_update,
)

final_values = [0x3FF0000000000000, 0x0000000000000000]
if args.corrupt_final_oracle:
    final_values[0] += 1
system.final_verifier = LANLMAA(
    addresses=[0x100, 0x108],
    expected_values=final_values,
    operation_entries=2,
    line_entries=1,
    logical_admission_width=2,
    line_issue_width=1,
    retirement_width=2,
    start_cycle=250,
)

system.relaxed_reducer.mem_side = system.membus.cpu_side_ports
system.strict_reducer.mem_side = system.membus.cpu_side_ports
system.final_verifier.mem_side = system.membus.cpu_side_ports
system.memory.port = system.membus.mem_side_ports
system.system_port = system.membus.cpu_side_ports

root = Root(full_system=False, system=system)
m5.instantiate()
event = m5.simulate()
m5.stats.dump()

expected_cause = (
    "LANLMAA gather verification failed"
    if args.corrupt_final_oracle
    else "LANLMAA gather complete"
)
expected_code = 2 if args.corrupt_final_oracle else 0
if event.getCause() != expected_cause or event.getCode() != expected_code:
    raise RuntimeError(
        f"unexpected exit: cause={event.getCause()} code={event.getCode()}"
    )
