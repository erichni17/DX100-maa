from m5.objects.ClockedObject import ClockedObject
from m5.params import *
from m5.proxy import *


class LANLMAA(ClockedObject):
    type = "LANLMAA"
    cxx_header = "mem/LANLMAA/lanl_maa.hh"
    cxx_class = "gem5::lanlmaa::LANLMAA"

    mem_side = RequestPort("Coherent request port for accelerator traffic")
    system = Param.System(Parent.any, "System that owns the requestor ID")

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

    operation_entries = Param.Unsigned(64, "Logical operation-window entries")
    line_entries = Param.Unsigned(32, "Coherent line-merge entries")
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
