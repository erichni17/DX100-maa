from m5.objects.ClockedObject import ClockedObject
from m5.objects.X86MMU import X86MMU
from m5.params import *
from m5.proxy import *
from m5.SimObject import *


class MAA(ClockedObject):
    type = "MAA"
    cxx_header = "mem/MAA/MAA.hh"
    cxx_class = "gem5::MAA"
    cxx_exports = [PyBindMethod("addRamulator")]

    num_tiles_per_core = Param.Unsigned(
        8, "Number of SPD tiles per core attached to the DX100 instance"
    )
    num_tile_elements = Param.Unsigned(
        16384, "Number of elements in each tile"
    )
    physical_tile_elements = Param.Unsigned(
        0, "Physical elements allocated per SPD tile (0 matches logical size)"
    )
    transparent_spd_mode = Param.Unsigned(
        0,
        "Transparent SPD arm: 0=serial-4K, 1=serial-2K, 2=two-half ping-pong",
    )
    num_regs_per_core = Param.Unsigned(
        8,
        "Number of 32-bit scalar registers per core attached to the DX100 instance",
    )
    num_instructions_per_core = Param.Unsigned(
        8,
        "Number of instructions in the instruction file per core attached to the DX100 instance",
    )
    num_row_table_rows_per_slice = Param.Unsigned(
        64, "Number of rows in each row table slice"
    )
    num_offset_table_entries = Param.Unsigned(
        0,
        "Live Offset-Table entries per indirect unit (0 matches logical tile)",
    )
    num_offset_table_epoch_entries = Param.Unsigned(
        0, "Offset entries per drain epoch (0 matches Offset-Table capacity)"
    )
    num_row_table_entries_per_subslice_row = Param.Unsigned(
        8,
        "Number of row table entries (bursts) per each sub-slice of row table",
    )
    num_row_table_config_cache_entries = Param.Unsigned(
        16, "Number of row table entry history in the configuration cache"
    )
    num_request_table_addresses = Param.Unsigned(
        128, "Number of addresses in the request table"
    )
    num_request_table_entries_per_address = Param.Unsigned(
        16, "Number of entries in the request table per address"
    )
    reconfigure_row_table = Param.Bool(False, "Reconfigure row table")
    no_reorder = Param.Bool(False, "Do not reorder accesses using row table")
    force_cache_access = Param.Bool(
        False,
        "Force cache access instead of direct memory access for the indirect access unit",
    )
    num_initial_row_table_slices = Param.Unsigned(
        32,
        "Number of initial row table slices if row table is not reconfigurable",
    )
    virtual_combine_slots = Param.Unsigned(
        16, "Tagged cache-line slots used by virtual gather retirement"
    )
    virtual_combine_words = Param.Unsigned(
        0,
        "Resident virtual gather data words (0 reserves every line slot fully)",
    )
    virtual_combine_ways = Param.Unsigned(
        0,
        "Virtual destination-combiner associativity (0 is fully associative)",
    )
    virtual_combine_victim_policy = Param.Unsigned(
        0,
        "Virtual combiner victim policy: 0=round-robin, 1=fewest words, 2=most words",
    )
    virtual_response_slots = Param.Unsigned(
        8, "Retained source responses used by virtual gather retirement"
    )
    virtual_response_words = Param.Unsigned(
        0,
        "Packed useful words per retained response (0 stores the full source line)",
    )
    virtual_response_word_pool = Param.Unsigned(
        0, "Total useful words retained across packed source responses"
    )
    virtual_max_outstanding_writes = Param.Unsigned(
        32, "Acknowledged virtual retirement writes allowed in flight"
    )
    virtual_masked_writes = Param.Bool(
        False, "Retire partial virtual lines as masked cache-line writes"
    )
    virtual_index_buffer_lines = Param.Unsigned(
        1,
        "Cache lines buffered or in flight for direct virtual-index ingestion",
    )
    virtual_index_force_cache = Param.Bool(
        False,
        "Route direct virtual-index feeder reads through the cache hierarchy",
    )
    virtual_index_partitions = Param.Unsigned(
        1, "Modulo DRAM-grow partitions scanned by a direct virtual-index load"
    )
    virtual_index_range_passes = Param.Bool(
        False,
        "Select contiguous DRAM-grow ranges with explicit exact-once tracking",
    )
    virtual_index_range_policy = Param.Unsigned(
        0,
        "Range bounds: 0=full hardware grow space, 1=A source endpoints",
    )
    virtual_index_filter_words_per_cycle = Param.Unsigned(
        0,
        "Partition-filter index words examined per cycle (0 is unlimited)",
    )
    virtual_partition_keep_combiner = Param.Bool(
        False,
        "Retain partial destination-combiner lines across index partitions",
    )
    virtual_grow_order = Param.Bool(
        False, "Group virtual source claims by DRAM grow address"
    )
    virtual_native_issue_order = Param.Bool(
        False,
        "Attribution-only bounded claims following native row-table order",
    )
    spd_read_latency = Param.Cycles(1, "SPD read latency")
    spd_write_latency = Param.Cycles(1, "SPD write latency")
    num_spd_read_ports_per_maa = Param.Unsigned(
        4, "Number of SPD read ports per DX100 instance"
    )
    num_spd_write_ports_per_maa = Param.Unsigned(
        4, "Number of SPD write ports per DX100 instance"
    )
    rowtable_latency = Param.Cycles(1, "Row table latency")
    ALU_lane_latency = Param.Cycles(1, "ALU lane latency")
    num_ALU_lanes = Param.Unsigned(16, "Number of ALU lanes")
    cache_snoop_latency = Param.Cycles(1, "Cache snoop latency")
    max_outstanding_cache_side_packets = Param.Unsigned(
        512, "Maximum number of outstanding cache side packets"
    )
    max_outstanding_cpu_side_packets = Param.Unsigned(
        512, "Maximum number of outstanding cpu side packets"
    )
    num_memory_channels = Param.Unsigned(2, "Number of memory channels")
    num_cores = Param.Unsigned(4, "Number of cores")
    num_maas = Param.Unsigned(1, "Number of MAA instances")
    num_indirect_units_per_maa = Param.Unsigned(
        1, "Number of indirect access units per MAA instance"
    )

    cpu_sides = VectorResponsePort(
        "Vector port for connecting to the CPU and/or device"
    )
    mem_sides = VectorRequestPort("Vector port for connecting to DRAM memory")
    # master = DeprecatedParam(
    #     mem_sides, "`master` is now called `mem_sides`"
    # )
    cache_sides = VectorRequestPort("Vector port for connecting to to LLC")
    retirement_sides = VectorRequestPort(
        "Vector port for coherent virtual-gather retirement writes"
    )

    addr_ranges = VectorParam.AddrRange(
        [AllMemory],
        "Address range for scratchpad data, scratchpad size, scratchpad ready, scalar registers, and instruction file",
    )
    mmu = Param.BaseMMU(X86MMU(), "CPU memory management unit")

    system = Param.System(Parent.any, "System we belong to")
    virtual_combine_banks = Param.Unsigned(
        0, "Single-update combiner banks (0 disables bank conflicts)"
    )
    virtual_words_per_cycle = Param.Unsigned(
        0, "Response-word combiner attempts per cycle (0 is unlimited)"
    )

    def addRamulatorInstance(self, simObj):
        self.getCCObject().addRamulator(simObj.getCCObject())
