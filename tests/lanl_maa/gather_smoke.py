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
system.memory = SimpleMemory(range=system.mem_ranges[0], latency="20ns")

addresses = [
    0x000,
    0x008,
    0x010,
    0x008,
    0x040,
    0x048,
    0x000,
    0x080,
    0x088,
    0x090,
    0x098,
    0x080,
]
system.lanl_maa = LANLMAA(
    addresses=addresses,
    expected_values=[0] * len(addresses),
    operation_entries=8,
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

if event.getCause() != "LANLMAA gather complete" or event.getCode() != 0:
    raise RuntimeError(
        f"unexpected exit: cause={event.getCause()} code={event.getCode()}"
    )
