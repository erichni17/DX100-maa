from m5.objects.ClockedObject import ClockedObject
from m5.params import *
from m5.proxy import *


class LANLMAAUpdateOperation(Enum):
    vals = [
        "uint64_add",
        "uint64_min",
        "uint64_max",
        "fp64_add_relaxed",
        "fp64_add_strict",
    ]


class LANLMAA(ClockedObject):
    type = "LANLMAA"
    cxx_header = "mem/LANLMAA/lanl_maa.hh"
    cxx_class = "gem5::lanlmaa::LANLMAA"

    mem_side = RequestPort("Coherent request port for accelerator traffic")
    control = ResponsePort(
        "Optional CPU-visible descriptor doorbell and status"
    )
    system = Param.System(Parent.any, "System that owns the requestor ID")

    descriptor_mode = Param.Bool(
        False, "Wait for a CPU-visible doorbell and fetch a bounded descriptor"
    )
    descriptor_table_base = Param.Addr(
        0, "Physical base of the 64-byte descriptor slot table"
    )
    descriptor_slots = Param.Unsigned(8, "Number of fixed descriptor slots")
    max_descriptor_items = Param.Unsigned(
        64,
        "Maximum v1 items; must fit the configured operation window",
    )
    control_addr = Param.Addr(
        0x100000, "Physical base of the CPU-visible control register page"
    )
    control_size = Param.Addr(0x1000, "Control register aperture size")
    control_latency = Param.Latency(
        "10ns", "CPU-visible control access latency"
    )

    addresses = VectorParam.Addr([], "Ordered 64-bit direct-gather addresses")
    expected_values = VectorParam.UInt64(
        [], "Optional expected value for every gather item"
    )
    dependent_mode = Param.Bool(
        False, "Interpret each address as a next-address/payload record"
    )
    continuation_entries = Param.Unsigned(
        64, "Maximum dependent operations with retained continuation state"
    )
    max_continuation_steps = Param.Unsigned(
        8, "Maximum records visited by one dependent operation"
    )
    terminal_address = Param.UInt64(
        0xFFFFFFFFFFFFFFFF, "Next-address value that terminates a cell walk"
    )
    update_mode = Param.Bool(
        False, "Interpret addresses and update_values as unsigned updates"
    )
    update_values = VectorParam.UInt64(
        [], "Unsigned 64-bit operand for every update address"
    )
    update_fp_values = VectorParam.Float(
        [], "Finite FP64 operand for every floating update address"
    )
    update_operation = Param.LANLMAAUpdateOperation(
        "uint64_add", "Unsigned 64-bit atomic update operation"
    )
    verification_addresses = VectorParam.Addr(
        [], "Addresses read after acknowledged update drains"
    )
    verification_values = VectorParam.UInt64(
        [], "Expected final value for every verification address"
    )
    verification_fp_values = VectorParam.Float(
        [], "Expected finite FP64 value for every verification address"
    )
    verification_abs_tolerance = Param.Float(
        0.0, "Absolute tolerance for FP64 post-drain verification"
    )
    verification_rel_tolerance = Param.Float(
        0.0, "Relative tolerance for FP64 post-drain verification"
    )
    update_entries = Param.Unsigned(64, "Banked update-combiner entries")
    update_banks = Param.Unsigned(8, "Update-combiner banks")
    update_issue_width = Param.Unsigned(
        1, "Maximum combined timing atomic requests issued per cycle"
    )
    face_compute_latency = Param.Cycles(
        0,
        "Final live internal-face interpolation issue-to-result latency; "
        "zero preserves untimed behavior",
    )
    face_compute_initiation_interval = Param.Cycles(
        1, "Minimum cycles between issues to one face-compute unit"
    )
    face_compute_units = Param.Unsigned(
        1, "Replicated abstract face-interpolation pipelines"
    )
    branson_event_compute_latency = Param.Cycles(
        4,
        "Staged Branson event decode/control issue-to-result latency; this "
        "does not represent native RNG, log/exp, or geometry",
    )
    branson_event_compute_initiation_interval = Param.Cycles(
        1, "Minimum cycles between issues to one Branson event unit"
    )
    branson_event_compute_units = Param.Unsigned(
        1, "Replicated staged Branson event decode/control pipelines"
    )
    branson_context_quantum = Param.Unsigned(
        4, "Preferred consecutive events before rotating continuation context"
    )
    branson_active_context_limit = Param.Unsigned(
        0,
        "Maximum contexts active for opcode 5; zero uses every physical "
        "continuation entry",
    )

    operation_entries = Param.Unsigned(64, "Logical operation-window entries")
    line_entries = Param.Unsigned(32, "Coherent line-merge entries")
    line_banks = Param.Unsigned(
        4, "Single-distinct-line-access banks in the line-merge table"
    )
    logical_admission_width = Param.Unsigned(
        2, "Maximum logical items admitted per cycle"
    )
    line_issue_width = Param.Unsigned(
        1, "Maximum new coherent line requests issued per cycle"
    )
    retirement_width = Param.Unsigned(
        2, "Maximum ordered logical completions retired per cycle"
    )
    line_bytes = Param.Unsigned(64, "Coherent request and merge granularity")
    start_cycle = Param.Cycles(
        1, "Cycle at which the synthetic descriptor starts"
    )
    exit_on_completion = Param.Bool(
        True, "Exit the simulation when the descriptor completes"
    )


class LANLMAAControlTester(ClockedObject):
    type = "LANLMAAControlTester"
    cxx_header = "mem/LANLMAA/control_tester.hh"
    cxx_class = "gem5::lanlmaa::LANLMAAControlTester"

    port = RequestPort("Test-only upstream timing port for MMIO doorbells")
    system = Param.System(Parent.any, "System that owns the requestor ID")
    control_addr = Param.Addr("LANL-MAA control aperture base")
    doorbell_slot = Param.Unsigned("Descriptor slot encoded by the address")
    writes = Param.Unsigned(1, "Doorbell writes to issue")
    start_cycle = Param.Cycles(1, "First doorbell issue cycle")


class LANLMAAControlSequencer(ClockedObject):
    type = "LANLMAAControlSequencer"
    cxx_header = "mem/LANLMAA/control_sequencer.hh"
    cxx_class = "gem5::lanlmaa::LANLMAAControlSequencer"

    port = RequestPort(
        "Test-only timing port for status-driven descriptor submission"
    )
    system = Param.System(Parent.any, "System that owns the requestor ID")
    control_addr = Param.Addr("LANL-MAA control aperture base")
    doorbell_slots = VectorParam.UInt64("Ordered descriptor slots to submit")
    expected_terminal_errors = VectorParam.UInt64(
        [],
        "Expected error per slot; zero requires Completed",
    )
    start_cycle = Param.Cycles(1, "First doorbell issue cycle")
    poll_interval = Param.Cycles(2, "Cycles between status polls")
