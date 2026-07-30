"""Design-local SAIF power rule with an active-low reset preamble."""

load("//:openroad.bzl", "orfs_run")


def portfolio_power_data(
        name,
        flow_target,
        stage,
        saif,
        verilog,
        spef,
        saif_scope,
        out_template):
    """Run vectorless and SAIF-driven power on the joint portfolio top."""
    stage_stem = {
        "floorplan": "2_floorplan",
        "place": "3_place",
        "cts": "4_cts",
        "grt": "6_final",
        "final": "6_final",
    }[stage]
    power_types = ["vectorless", "vector-driven"]
    outs = [out_template.format(power = power) for power in power_types]
    base = ":scripts/portfolio_power_base.tcl"
    arguments = {
        "SAIF_STIMULI": "$(location {})".format(saif),
        "POWER_STAGE": stage_stem,
        "POWER_BASE_TCL": "$(location {})".format(base),
        "SAIF_SCOPE": saif_scope,
        "SPEFS_AND_NETLISTS": "$(location {verilog}) $(location {spef})".format(
            verilog = verilog,
            spef = spef,
        ),
    } | {
        "{}_POWER_JSON".format(power.upper()): "$(location {})".format(
            out_template.format(power = power),
        )
        for power in power_types
    }
    orfs_run(
        name = name,
        src = flow_target,
        outs = outs,
        arguments = arguments,
        data = [
            verilog,
            spef,
            saif,
            base,
        ],
        script = "@bazel-orfs//:power.tcl",
        tags = ["manual"],
    )
