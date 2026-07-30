#!/usr/bin/env python3
"""Freeze workload-derived top-input SAIF sensitivity results."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

PROFILES = (
    "umt_32_context",
    "sparta_64_particle",
    "amg_sparse_normalized",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_power(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    total = value.get("Total")
    if not isinstance(total, dict) or set(total) != {
        "internal",
        "switching",
        "leakage",
        "total",
    }:
        raise ValueError(f"{path}: malformed OpenSTA power JSON")
    if any(float(value) < 0 for value in total.values()):
        raise ValueError(f"{path}: negative power")
    return {name: float(value) for name, value in total.items()}


def parse_annotations(path: Path, design_prefix: str) -> dict[str, int]:
    text = path.read_text(encoding="utf-8")
    if "Build completed successfully" not in text:
        raise ValueError(f"{path}: successful power build marker is absent")
    pattern = re.compile(
        r"read_saif[^\n]*" + re.escape(design_prefix) +
        r"_(?P<profile>[a-z0-9_]+)\.saif\n"
        r"[^\n]*Annotated (?P<pins>[0-9]+) pin activities\."
    )
    result: dict[str, int] = {}
    for match in pattern.finditer(text):
        profile = match.group("profile")
        if profile in result:
            raise ValueError(f"{path}: duplicate annotation record {profile}")
        result[profile] = int(match.group("pins"))
    if set(result) != set(PROFILES):
        raise ValueError(f"{path}: incomplete annotation records")
    return result


def collect(power_dir: Path, service_log: Path,
            design_prefix: str = "fp64_portfolio",
            expected_pins: int = 139) -> dict[str, Any]:
    if not design_prefix or expected_pins <= 0:
        raise ValueError("invalid design prefix or expected pin count")
    annotations = parse_annotations(service_log, design_prefix)
    profiles = {}
    for profile in PROFILES:
        paths = {
            "saif": power_dir / f"{design_prefix}_{profile}.saif",
            "vectorless": power_dir
            / f"{design_prefix}_{profile}_vectorless_power.json",
            "vector_driven": power_dir
            / f"{design_prefix}_{profile}_vector-driven_power.json",
        }
        if not all(path.is_file() for path in paths.values()):
            raise ValueError(f"{profile}: incomplete power outputs")
        profiles[profile] = {
            "annotated_top_input_pins": annotations[profile],
            "expected_top_input_pins": expected_pins,
            "all_top_input_pins_annotated": (
                annotations[profile] == expected_pins),
            "vectorless_total_w": load_power(paths["vectorless"]),
            "vector_driven_total_w": load_power(paths["vector_driven"]),
            "raw_evidence": {
                name: {"path": str(path), "sha256": sha256(path)}
                for name, path in paths.items()
            },
        }
    if not all(
        value["all_top_input_pins_annotated"] for value in profiles.values()
    ):
        raise ValueError("top-input SAIF coverage is incomplete")
    return {
        "schema_version": 1,
        "status": "top-input-saif-sensitivity-complete",
        "profiles": profiles,
        "service_log": {
            "path": str(service_log),
            "sha256": sha256(service_log),
        },
        "power_claim_eligible": False,
        "claim_boundary": (
            f"All {expected_pins} top-input pins were annotated, but internal workload "
            "activity was propagated from a deterministic operand proxy. "
            "These values are sensitivity screens, not native workload power "
            "or energy measurements."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--power-dir", type=Path, required=True)
    parser.add_argument("--service-log", type=Path, required=True)
    parser.add_argument("--design-prefix", default="fp64_portfolio")
    parser.add_argument("--expected-pins", type=int, default=139)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = collect(
        args.power_dir,
        args.service_log,
        design_prefix=args.design_prefix,
        expected_pins=args.expected_pins,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(sha256(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
