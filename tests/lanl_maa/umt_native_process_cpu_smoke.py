"""Run native UMT with optional opcode-10 corner or opcode-11 wave offload."""

import argparse

import m5
from m5.objects import (
    LANLMAA,
    AddrRange,
    Cache,
    Process,
    Root,
    SEWorkload,
    SimpleMemory,
    SrcClockDomain,
    System,
    SystemXBar,
    VoltageDomain,
    X86TimingSimpleCPU,
)

DATA_VADDR = 0x1000000000
DATA_PADDR = 0x10000000
DATA_BYTES = 0x00100000
CONTROL_VADDR = DATA_VADDR + DATA_BYTES
CONTROL_PADDR = 0x20000000
CONTROL_BYTES = 0x1000

parser = argparse.ArgumentParser()
parser.add_argument("--binary", required=True)
parser.add_argument("--cwd", required=True)
parser.add_argument("--output-dir", required=True)
parser.add_argument("--app-stdout", required=True)
parser.add_argument("--app-stderr", required=True)
parser.add_argument("--submission-report", required=True)
parser.add_argument("--problem", choices=("1", "2"), required=True)
parser.add_argument("--maa", action="store_true")
parser.add_argument("--umt-mode", choices=("corner", "wave"), default="corner")
parser.add_argument("--max-insts", type=int, default=0)
parser.add_argument("--max-ticks", type=int, default=0)
args = parser.parse_args()

if args.max_insts < 0 or args.max_ticks < 0:
    raise ValueError("instruction and tick bounds must be nonnegative")


class L1Cache(Cache):
    assoc = 8
    tag_latency = 2
    data_latency = 2
    response_latency = 2
    mshrs = 8
    tgts_per_mshr = 20


class MAACoherenceCache(Cache):
    size = "4KiB"
    assoc = 2
    tag_latency = 2
    data_latency = 2
    response_latency = 2
    mshrs = 8
    tgts_per_mshr = 2
    write_buffers = 2


system = System(
    cache_line_size=64,
    mem_mode="timing",
    mem_ranges=[AddrRange(CONTROL_PADDR)],
)
system.voltage_domain = VoltageDomain(voltage="1V")
system.clk_domain = SrcClockDomain(
    clock="1GHz", voltage_domain=system.voltage_domain
)
system.cpu = X86TimingSimpleCPU()
if args.max_insts:
    system.cpu.max_insts_any_thread = args.max_insts
system.membus = SystemXBar()
system.icache = L1Cache(size="32KiB")
system.dcache = L1Cache(size="32KiB")
system.cpu.icache_port = system.icache.cpu_sides
system.cpu.dcache_port = system.dcache.cpu_sides
system.icache.mem_sides = system.membus.cpu_side_ports
system.dcache.mem_sides = system.membus.cpu_side_ports
system.cpu.createInterruptController()
system.cpu.interrupts[0].pio = system.membus.mem_side_ports
system.cpu.interrupts[0].int_requestor = system.membus.cpu_side_ports
system.cpu.interrupts[0].int_responder = system.membus.mem_side_ports

system.memory = SimpleMemory(range=system.mem_ranges[0], latency="20ns")
system.memory.port = system.membus.mem_side_ports
system.lanl_maa = LANLMAA(
    descriptor_mode=True,
    descriptor_table_base=DATA_PADDR,
    descriptor_slots=8,
    max_descriptor_items=32,
    control_addr=CONTROL_PADDR,
    control_size=CONTROL_BYTES,
    operation_entries=64,
    continuation_entries=32,
    line_entries=32,
    update_entries=64,
    update_banks=8,
    update_issue_width=1,
    logical_admission_width=2,
    line_issue_width=1,
    retirement_width=2,
    model_payload_overlay_ports=True,
    exit_on_completion=False,
)
system.maa_cache = MAACoherenceCache()
system.lanl_maa.mem_side = system.maa_cache.cpu_sides
system.maa_cache.mem_sides = system.membus.cpu_side_ports
system.lanl_maa.control = system.membus.mem_side_ports
system.system_port = system.membus.cpu_side_ports

process = Process()
process.cmd = [
    args.binary,
    "-B",
    "global",
    "-d",
    "1,1,1",
    "-b",
    args.problem,
    "-c",
    "1",
    "-l",
    f"lanl_maa_umt_spp{args.problem}_{'maa' if args.maa else 'scalar'}",
    "-o",
    args.output_dir,
]
process.cwd = args.cwd
process.output = args.app_stdout
process.errout = args.app_stderr
process_environment = [
    "LANG=C",
    "LC_ALL=C",
    "OMP_NUM_THREADS=1",
    "LD_HWCAP_MASK=0",
    (
        "GLIBC_TUNABLES=glibc.cpu.hwcaps="
        "-SSE4_2,-AVX,-AVX2,-AVX512F,-AVX512VL"
    ),
    "OMPI_MCA_btl=self",
    "OMPI_MCA_pml=ob1",
    "OMPI_MCA_shmem=mmap",
    "OMPI_MCA_shmem_mmap_backing_file_base_dir=/tmp",
]
if args.maa:
    mapping_cookie = (
        "umt-lanl-maa-opcode11-wave-mapped-v2"
        if args.umt_mode == "wave"
        else "umt-lanl-maa-opcode10-mapped-v1"
    )
    process_environment.extend(
        [
            "LANL_MAA_UMT_SUBMIT=1",
            f"LANL_MAA_UMT_MODE={args.umt_mode}",
            f"LANL_MAA_UMT_MAPPING_COOKIE={mapping_cookie}",
            f"LANL_MAA_UMT_SUBMIT_REPORT={args.submission_report}",
        ]
    )
process.env = process_environment
system.workload = SEWorkload.init_compatible(args.binary)
system.cpu.workload = process
system.cpu.createThreads()

root = Root(full_system=False, system=system)
m5.instantiate()
process.map(DATA_VADDR, DATA_PADDR, DATA_BYTES, True)
process.map(CONTROL_VADDR, CONTROL_PADDR, CONTROL_BYTES, False)
event = m5.simulate(args.max_ticks) if args.max_ticks else m5.simulate()
m5.stats.dump()
print(
    f"LANLMAA_UMT_PROCESS_TERMINAL code={event.getCode()} "
    f"cause={event.getCause()}"
)
if event.getCode() != 0:
    raise RuntimeError(
        "native UMT process failed: "
        f"cause={event.getCause()} code={event.getCode()}"
    )
