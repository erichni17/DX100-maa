#!/usr/bin/env python3
"""Parameterized direct opcode-11 SE process for the ingress harness."""
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

p = argparse.ArgumentParser()
for value in (
    "binary",
    "cwd",
    "output-dir",
    "app-stdout",
    "app-stderr",
    "submission-report",
    "label",
    "umt-mode",
):
    p.add_argument("--" + value, required=True)
p.add_argument("--groups", type=int, choices=(16, 31, 32), required=True)
a = p.parse_args()


class L1(Cache):
    assoc = 8
    tag_latency = 2
    data_latency = 2
    response_latency = 2
    mshrs = 8
    tgts_per_mshr = 20


class MC(Cache):
    size = "4KiB"
    assoc = 2
    tag_latency = 2
    data_latency = 2
    response_latency = 2
    mshrs = 8
    tgts_per_mshr = 2
    write_buffers = 2


s = System(
    cache_line_size=64, mem_mode="timing", mem_ranges=[AddrRange(0x20000000)]
)
s.voltage_domain = VoltageDomain(voltage="1V")
s.clk_domain = SrcClockDomain(clock="1GHz", voltage_domain=s.voltage_domain)
s.cpu = X86TimingSimpleCPU()
s.membus = SystemXBar()
s.icache = L1(size="32KiB")
s.dcache = L1(size="32KiB")
s.cpu.icache_port = s.icache.cpu_sides
s.cpu.dcache_port = s.dcache.cpu_sides
s.icache.mem_sides = s.membus.cpu_side_ports
s.dcache.mem_sides = s.membus.cpu_side_ports
s.cpu.createInterruptController()
s.cpu.interrupts[0].pio = s.membus.mem_side_ports
s.cpu.interrupts[0].int_requestor = s.membus.cpu_side_ports
s.cpu.interrupts[0].int_responder = s.membus.mem_side_ports
s.memory = SimpleMemory(range=s.mem_ranges[0], latency="20ns")
s.memory.port = s.membus.mem_side_ports
s.lanl_maa = LANLMAA(
    descriptor_mode=True,
    descriptor_table_base=0x10000000,
    descriptor_slots=8,
    max_descriptor_items=64,
    control_addr=0x20000000,
    control_size=0x1000,
    operation_entries=64,
    continuation_entries=64,
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
s.maa_cache = MC()
s.lanl_maa.mem_side = s.maa_cache.cpu_sides
s.maa_cache.mem_sides = s.membus.cpu_side_ports
s.lanl_maa.control = s.membus.mem_side_ports
s.system_port = s.membus.cpu_side_ports
q = Process()
q.cmd = [
    a.binary,
    "-B",
    "global",
    "-d",
    "1,1,1",
    "-b",
    "3",
    "-P",
    "2",
    "-A",
    "2",
    "-G",
    str(a.groups),
    "-c",
    "1",
    "-l",
    a.label,
    "-o",
    a.output_dir,
]
q.cwd = a.cwd
q.output = a.app_stdout
q.errout = a.app_stderr
q.env = [
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
    "LANL_MAA_UMT_SUBMIT=1",
    f"LANL_MAA_UMT_MODE={a.umt_mode}",
    "LANL_MAA_UMT_MAPPING_COOKIE=umt-lanl-maa-opcode11-wave-soa-arena-adaptive-v1",
    f"LANL_MAA_UMT_SUBMIT_REPORT={a.submission_report}",
]
s.workload = SEWorkload.init_compatible(a.binary)
s.cpu.workload = q
s.cpu.createThreads()
r = Root(full_system=False, system=s)
m5.instantiate()
q.map(0x1000000000, 0x10000000, 0x100000, True)
q.map(0x1000100000, 0x20000000, 0x1000, False)
e = m5.simulate()
m5.stats.dump()
print(f"LANLMAA_UMT_INGRESS_TERMINAL code={e.getCode()} cause={e.getCause()}")
if e.getCode() != 0:
    raise RuntimeError(e.getCause())
