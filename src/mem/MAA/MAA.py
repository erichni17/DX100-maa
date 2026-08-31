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
        "Transparent SPD arm: 0=serial-4K, 1=serial-2K, "
        "2=two-half ping-pong, 3=narrow terminal FP64 "
        "gather/multiply/dense-store direct retirement",
    )
    logical_spd_cache_mode = Param.Unsigned(
        0,
        "Logical SPD cache mode: 0=Serial4K, 1=PingPong2K",
    )
    logical_tile_page_scheduler = Param.Bool(
        False,
        "Opt in to four-page logical instructions over four reserved native "
        "4K SPD frame spans",
    )
    page_fed_soa_jit = Param.Bool(
        False,
        "Opt in to bounded four-page direct SPD-index admission for one "
        "logical-16K SoA/JIT RMW",
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
    virtual_combine_set_xor_shift = Param.Unsigned(
        0, "XOR-fold shift for virtual-combiner set indexing (0 disables)"
    )
    virtual_combine_victim_policy = Param.Unsigned(
        0,
        "Virtual combiner victim policy: 0=round-robin, 1=fewest words, 2=most words",
    )
    virtual_page_ordered_combiner_drain = Param.Bool(
        False,
        "Prioritize full virtual-combiner lines by 4K logical output page",
    )
    virtual_complete_line_drain_lines_per_cycle = Param.Unsigned(
        0,
        "Complete virtual-combiner lines issued per MAA cycle "
        "(0 is unlimited; finite values are 1/2/4/8)",
    )
    virtual_complete_line_payload_words_per_cycle = Param.Unsigned(
        0,
        "Complete-line payload words staged per MAA cycle "
        "(0 is unlimited; finite values are 1/2/4/8)",
    )
    virtual_complete_line_payload_active_lines = Param.Unsigned(
        1,
        "Maximum line identities sharing the finite payload-read port",
    )
    virtual_complete_line_payload_banks = Param.Unsigned(
        0,
        "Finite payload RAM banks (0 models conflict-free aggregate width)",
    )
    virtual_complete_line_payload_stage_partial = Param.Bool(
        False,
        "Apply the finite payload port to masked partial-line writes",
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
    virtual_combine_lookup_latency_cycles = Param.Unsigned(
        0,
        "Pipelined ordinary virtual-combiner tag-lookup latency in MAA cycles",
    )
    virtual_max_outstanding_writes = Param.Unsigned(
        32, "Acknowledged virtual retirement writes allowed in flight"
    )
    virtual_masked_writes = Param.Bool(
        False, "Retire partial virtual lines as masked cache-line writes"
    )
    virtual_dense_write_allocate = Param.Bool(
        False,
        "Initialize the first dense backing fragment with a no-read full-line write",
    )
    virtual_complete_line_only = Param.Bool(
        False,
        "Retire complete virtual-result lines plus only the exact final tail",
    )
    virtual_idealized_write_ack = Param.Bool(
        False,
        "Diagnostic upper bound: expose virtual pages at final write issue",
    )
    direct_retirement_line_handoff = Param.Bool(
        False,
        "Expose direct-retirement backing lines after all word WriteResp events",
    )
    page_materialization_wakeup_batches = Param.Unsigned(
        0,
        "Early dependent wakeups per materialized 4K-element page (0 keeps page-level wakeup only; 1..16 are evenly spaced line milestones)",
    )
    page_materialization_fragment_buffers = Param.Unsigned(
        0,
        "Charged active-page line buffers allowed to accumulate authenticated masked producer fragments (0 disables; maximum 16)",
    )
    page_materialization_direct_spd_fragments = Param.Bool(
        False,
        "Stage authenticated active-page masked producer words directly in SPD",
    )
    inactive_page_payload_capture_lines = Param.Unsigned(
        0,
        "Fixed inactive-page full WriteResp payload capture lines (0 disables; maximum 512)",
    )
    inactive_page_payload_capture_conflict_policy = Param.String(
        "first-owner",
        "Direct-index collision policy; only first-owner is supported",
    )
    inactive_page_masked_fragment_retention_lines = Param.Unsigned(
        0,
        "Fixed inactive masked-fragment entries across four lifetime "
        "partitions (0 disables; 512/1024/2048/4096)",
    )
    soa_jit_predicate_active_credits = Param.Unsigned(
        1,
        "Active SoA/JIT predicate-line credits (one of 1/4/8/16)",
    )
    virtual_index_buffer_lines = Param.Unsigned(
        1,
        "Cache lines buffered or in flight for direct virtual-index "
        "ingestion (1..128)",
    )
    virtual_index_issue_lines_per_cycle = Param.Unsigned(
        1,
        "Direct virtual-index request-generation width "
        "(1, 2, or 4 lines/cycle)",
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
    virtual_index_descriptor_spool = Param.Bool(
        False,
        "Materialize pass-grouped direct-index descriptors in timed backing",
    )
    virtual_descriptor_spool_read_ahead = Param.Bool(
        False,
        "Read ahead the next descriptor pass in the configured credit window",
    )
    virtual_descriptor_spool_read_credits = Param.Unsigned(
        4,
        "Bounded 64-byte descriptor lines allowed in flight",
    )
    virtual_descriptor_spool_write_credits = Param.Unsigned(
        16,
        "Bounded descriptor backing writes allowed in flight",
    )
    virtual_descriptor_spool_source_bypass_cache = Param.Bool(
        False,
        "Bypass cache only for descriptor-spooled A-source reads",
    )
    virtual_bounded_global_merge = Param.Bool(
        False,
        "Materialize four RowTable-sorted runs and merge four finite heads",
    )
    virtual_strict_two_phase = Param.Bool(
        False,
        "Default-off diagnostic reference: ingest one 16K B/index stream "
        "through bounded feeder state, retain all Row/Offset descriptors, "
        "then issue A into coherent 4K-bounded result storage",
    )
    virtual_index_range_policy = Param.Unsigned(
        0,
        "Range bounds: 0=full grow, 1=A endpoints, 2=explicit oracle, "
        "3=bounded translated-DRAM-grow quantiles",
    )
    virtual_index_range_boundaries = VectorParam.Addr(
        [],
        "Explicit contiguous grow boundaries for policy 2",
    )
    virtual_index_filter_words_per_cycle = Param.Unsigned(
        0,
        "Partition-filter index words examined per cycle (0 is unlimited)",
    )
    soa_jit_active_contexts = Param.Unsigned(
        8,
        "Active SoA/JIT A-line contexts (8, 16, 32, or default-off 64; "
        "fixed maximum hardware is 64)",
    )
    soa_jit_old_result_partial_credits = Param.Unsigned(
        8,
        "Concurrent partial old-result pressure writes (1, 2, 4, or 8; "
        "uses the fixed eight-line buffer)",
    )
    soa_jit_old_result_pressure_policy = Param.String(
        "original_oldest",
        "Partial old-result pressure selection: original_oldest or densest",
    )
    soa_jit_value_lookahead = Param.Unsigned(
        1,
        "Ordered alias value-read credits per SoA/JIT context "
        "(1, 2, 4, or 8; fixed maximum hardware is eight)",
    )
    soa_jit_value_cache_enable = Param.Bool(
        False,
        "Retain ready lines in the fixed 128-owner SoA/JIT value pool",
    )
    soa_jit_pre_a_value_lookahead = Param.Bool(
        False,
        "Issue exact ordered SoA/JIT value lookahead while the claimed "
        "A-line read is outstanding",
    )
    soa_jit_value_prefetch_credits = Param.Unsigned(
        0,
        "Active sequential SoA/JIT value-line prefetch credits "
        "(0 disables; fixed maximum hardware is eight)",
    )
    soa_jit_active_value_owners = Param.Unsigned(
        4,
        "Active SoA/JIT value owners (4, 8, 16, 32, 64, 96, or 128; "
        "physical maximum is 128)",
    )
    soa_jit_apply_lanes = Param.Unsigned(
        1,
        "Active independent SoA/JIT A-line apply lanes "
        "(1, 2, or 4; physical maximum is four)",
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
