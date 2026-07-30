#!/usr/bin/env python3
"""Freeze strict post-route LANL-MAA FP64 metrics and raw-file hashes."""

import argparse
import hashlib
import json
from pathlib import Path

TOPS = {
    "add": "LanlFp64Add",
    "mul": "LanlFp64Mul",
    "fma": "LanlFp64Fma",
    "div1": "LanlFp64Div1",
    "div4": "LanlFp64Div4",
    "div8": "LanlFp64Div8",
}

EXPECTED_LIBERTY_MARKERS = (
    "Characterization Corner : typical",
    "Process                 : TypTyp",
    "Temperature             : 25C",
    "Voltage                 : 1.1V",
    'revision                \t\t: "revision 1.0";',
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unique(root: Path, pattern: str) -> Path:
    matches = sorted(root.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one match for {pattern!r}, found {len(matches)}: {matches}"
        )
    return matches[0]


def required(data: dict, key: str):
    if key not in data:
        raise RuntimeError(f"required ORFS metric is absent: {key}")
    return data[key]


def relative(path: Path, root: Path) -> str:
    # Keep the Bazel-workspace path stable even when bazel-bin is a symlink to
    # an output base outside the source tree.  Resolving symlinks here made
    # valid Bazel artifacts appear to escape ``root``.  The paths supplied by
    # this collector originate from root.glob(), so a lexical containment
    # check is the correct boundary.
    try:
        return str(path.absolute().relative_to(root.absolute()))
    except ValueError as exc:
        raise RuntimeError(
            f"evidence path is outside Bazel root: {path}"
        ) from exc


def summarize_platform(platform_root: Path) -> dict:
    files = {
        "liberty": platform_root / "lib/NangateOpenCellLibrary_typical.lib",
        "technology_lef": platform_root
        / "lef/NangateOpenCellLibrary.tech.lef",
        "cell_lef": platform_root / "lef/NangateOpenCellLibrary.macro.mod.lef",
    }
    for path in files.values():
        if not path.is_file():
            raise RuntimeError(f"required platform file is absent: {path}")

    liberty_text = files["liberty"].read_text(encoding="utf-8")
    for marker in EXPECTED_LIBERTY_MARKERS:
        if marker not in liberty_text:
            raise RuntimeError(
                f"required Nangate45 Liberty marker is absent: {marker}"
            )

    return {
        "name": "NangateOpenCellLibrary",
        "library_revision": "revision 1.0",
        "characterization_corner": "typical",
        "process_corner": "TypTyp",
        "nominal_process": 1.0,
        "nominal_temperature_c": 25.0,
        "nominal_voltage_v": 1.1,
        "files": {
            name: {
                "path_within_platform": str(path.relative_to(platform_root)),
                "sha256": sha256(path),
            }
            for name, path in files.items()
        },
    }


def summarize_top(bazel_root: Path, top: str) -> dict:
    logs = unique(bazel_root, f"bazel-bin/lanl_fp64/logs/**/{top}/base")
    reports = unique(bazel_root, f"bazel-bin/lanl_fp64/reports/**/{top}/base")
    results = unique(bazel_root, f"bazel-bin/lanl_fp64/results/**/{top}/base")

    final_metrics_path = logs / "6_report.json"
    route_metrics_path = logs / "5_2_route.json"
    drc_path = reports / "5_route_drc.rpt"
    spef_path = results / "6_final.spef"
    for path in (final_metrics_path, route_metrics_path, drc_path, spef_path):
        if not path.is_file():
            raise RuntimeError(f"required final artifact is absent: {path}")

    final = json.loads(final_metrics_path.read_text(encoding="utf-8"))
    route = json.loads(route_metrics_path.read_text(encoding="utf-8"))
    setup_slack_ns = required(final, "finish__timing__setup__ws")
    hold_slack_ns = required(final, "finish__timing__hold__ws")
    route_drc_errors = required(route, "detailedroute__route__drc_errors")
    final_errors = required(final, "finish__flow__errors__count")
    route_errors = required(route, "detailedroute__flow__errors__count")

    raw_paths = (
        final_metrics_path,
        route_metrics_path,
        drc_path,
        spef_path,
    )
    return {
        "top": top,
        "physical_metrics": {
            "instance_count": required(
                final, "finish__design__instance__count"
            ),
            "instance_area_um2": required(
                final, "finish__design__instance__area"
            ),
            "die_area_um2": required(final, "finish__design__die__area"),
            "core_area_um2": required(final, "finish__design__core__area"),
            "utilization": required(
                final, "finish__design__instance__utilization"
            ),
            "setup_slack_ns": setup_slack_ns,
            "setup_tns_ns": required(final, "finish__timing__setup__tns"),
            "hold_slack_ns": hold_slack_ns,
            "hold_tns_ns": required(final, "finish__timing__hold__tns"),
            "reported_fmax_hz": required(final, "finish__timing__fmax"),
            "detailed_route_drc_errors": route_drc_errors,
            "detailed_route_wirelength_um": required(
                route, "detailedroute__route__wirelength"
            ),
            "detailed_route_vias": required(
                route, "detailedroute__route__vias"
            ),
            "final_flow_errors": final_errors,
            "route_flow_errors": route_errors,
        },
        "default_activity_power_not_workload_derived_w": {
            "internal": required(final, "finish__power__internal__total"),
            "switching": required(final, "finish__power__switching__total"),
            "leakage": required(final, "finish__power__leakage__total"),
            "total": required(final, "finish__power__total"),
            "eligible_for_energy_claim": False,
        },
        "checks": {
            "target_10ns_setup_met": setup_slack_ns >= 0,
            "hold_met": hold_slack_ns >= 0,
            "detailed_route_drc_clean": route_drc_errors == 0,
            "flow_error_free": final_errors == 0 and route_errors == 0,
        },
        "raw_evidence": [
            {
                "path": relative(path, bazel_root),
                "sha256": sha256(path),
            }
            for path in raw_paths
        ],
    }


def parse_top_overrides(values: list[str] | None) -> dict[str, str]:
    if not values:
        return dict(TOPS)
    result = {}
    for value in values:
        if value.count("=") != 1:
            raise ValueError("--top must use NAME=MODULE")
        name, module = value.split("=", 1)
        if not name or not module or name in result:
            raise ValueError(
                "--top names and modules must be nonempty and unique"
            )
        result[name] = module
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel-root", type=Path, required=True)
    parser.add_argument("--orfs-platform-root", type=Path, required=True)
    parser.add_argument("--created-utc", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--top",
        action="append",
        metavar="NAME=MODULE",
        help="collect only the named top; repeat for multiple custom tops",
    )
    args = parser.parse_args()

    selected_tops = parse_top_overrides(args.top)
    blocks = {
        name: summarize_top(args.bazel_root, top)
        for name, top in selected_tops.items()
    }
    derived = {}
    if {"div1", "div4", "div8"}.issubset(blocks):
        div1_area = blocks["div1"]["physical_metrics"]["instance_area_um2"]
        derived = {
            "div4_to_div1_area_ratio": (
                blocks["div4"]["physical_metrics"]["instance_area_um2"]
                / div1_area
            ),
            "div8_to_div1_area_ratio": (
                blocks["div8"]["physical_metrics"]["instance_area_um2"]
                / div1_area
            ),
        }
    result = {
        "schema_version": 1,
        "created_utc": args.created_utc,
        "status": "common-corner-physical-screen",
        "corner": {
            "platform": "Nangate45",
            "pdk_library": summarize_platform(args.orfs_platform_root),
            "clock_period_ns": 10.0,
            "input_delay_ns": 0.25,
            "output_delay_ns": 0.25,
            "target_core_utilization": 0.40,
            "placement_density": 0.60,
            "endpoint": "ORFS final after detailed route and parasitic extraction",
        },
        "toolchain": {
            "hardfloat": {
                "release": "Release 1",
                "archive_sha256": "6b3757c9fbfa2230c6a2b84605e39372cb589dd7500e979c4f0b8ecc8a03b14b",
            },
            "bazel_orfs": {
                "revision": "6b55b049a5e753a234151578a3b3424388660db7",
                "archive_sha256": "5ac89aea9c35fbdbbe118b6cb415510dd97c7e59adebcf46593239e734b6b809",
            },
            "openroad_flow_scripts_revision": "c90beac0945588840cf07b7b4cc6d1b20ac66ddf",
            "openroad_revision": "c724b7da0f4f8b2abc86b0b31c5d51d3740804e0",
            "yosys_version": "0.64",
            "bazel_version": "8.6.0",
            "bazel_mode": "batch",
        },
        "blocks": blocks,
        "derived": derived,
        "claim_boundary": (
            "This is an open Nangate45 common-corner P&R screen, not signoff. "
            "The recorded default-activity power is not workload-derived and "
            "cannot support energy claims. Separate block results do not price "
            "a jointly placed subsystem, prove FMA contraction safety, select "
            "a topology or unit count, or establish application performance."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
