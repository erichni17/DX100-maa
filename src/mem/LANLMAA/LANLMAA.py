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
