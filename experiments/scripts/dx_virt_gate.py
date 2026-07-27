#!/usr/bin/env python3
"""Build and run fail-closed virtual direct-gather correctness gates."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class GateCase:
    name: str
    elements: int
    pattern: str
    physical_elements: int
    index_lines: int = 4
    rt_rows: int = 64
    rt_entries: int = 8
    response_slots: int = 96
    response_word_pool: int = 480


CASES = (
    GateCase("fanout_preissue", 128, "fanout", 4096,
             response_slots=16, response_word_pool=128),
    GateCase("same_line_preissue", 128, "same_line", 4096,
             response_slots=16, response_word_pool=128),
    GateCase(
        "line_revisit_retry",
        4096,
        "line_revisit",
        4096,
        rt_rows=32,
        rt_entries=4,
    ),
    GateCase("random_physical_4k", 4096, "random", 4096),
    GateCase("random_physical_16k", 4096, "random", 16384),
)


class GateError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_test(root: Path, output: Path) -> None:
    source = root / "benchmarks/API/test_virtual_index_gather.cpp"
    m5op = root / "util/m5/build/x86/abi/x86/m5op.S"
    if not m5op.is_file():
        raise GateError(f"missing generated m5op assembly: {m5op}")
    command = [
        os.environ.get("CXX", "g++"),
        f"-I{root / 'benchmarks/API'}",
        f"-I{root / 'include'}",
        f"-I{root / 'util/m5/src'}",
        "-std=c++11",
        "-O3",
        "-Wall",
        "-g3",
        "-fopenmp",
        "-DGEM5",
        "-DTILE_SIZE=16384",
        "-DNUM_CORES=4",
        "-DMAA_MEM_SIZE=0x80000000",
        str(m5op),
        str(source),
        "-o",
        str(output),
    ]
    subprocess.run(command, cwd=root, check=True)


def parse_result(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if len(rows) != 1:
        raise GateError(f"expected one result row in {path}, found {len(rows)}")
    return rows[0]


def run_case(root: Path, gem5: Path, binary: Path, output: Path,
             case: GateCase) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        VIRTUAL_INDEX_PATTERN=case.pattern,
        VIRTUAL_PHYSICAL_TILE_ELEMENTS=str(case.physical_elements),
        VIRTUAL_RT_ROWS_PER_SLICE=str(case.rt_rows),
        VIRTUAL_RT_ENTRIES_PER_SUBSLICE_ROW=str(case.rt_entries),
        VIRTUAL_RESPONSE_SLOTS=str(case.response_slots),
        VIRTUAL_RESPONSE_WORD_POOL=str(case.response_word_pool),
    )
    command = [
        str(root / "experiments/scripts/run_virtual_index_prefetch_case.sh"),
        str(gem5),
        str(binary),
        str(case.elements),
        str(case.index_lines),
        str(output),
    ]
    subprocess.run(command, cwd=root, env=environment, check=True)
    marker = output / "virtual_index_prefetch_case.pass"
    if not marker.is_file():
        raise GateError(f"case {case.name} did not publish its pass marker")
    result = parse_result(output / "result.tsv")
    if case.pattern in {"fanout", "same_line"} and result["source_reads"] != "1":
        raise GateError(
            f"case {case.name} issued {result['source_reads']} source reads, expected 1"
        )
    if case.name == "line_revisit_retry" and int(result["rt_full"]) <= 0:
        raise GateError("line_revisit_retry did not force a Row-Table drain")
    return result


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--test-bin", type=Path)
    parser.add_argument("--plan", action="store_true",
                        help="print the gate matrix without executing it")
    parser.add_argument("--case", action="append", choices=[c.name for c in CASES],
                        help="run only selected cases (repeatable)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = [case for case in CASES
                if not args.case or case.name in args.case]
    if args.plan:
        print(json.dumps([asdict(case) for case in selected], indent=2))
        return 0

    root = Path(__file__).resolve().parents[2]
    gem5 = args.gem5.resolve(strict=True)
    output = args.out.resolve()
    if output.exists():
        raise SystemExit(f"dx-virt-gate: refusing to overwrite {output}")
    output.mkdir(parents=True)
    binary = (args.test_bin.resolve(strict=True) if args.test_bin
              else output / "test_virtual_index_gather_T16K")
    try:
        if args.test_bin is None:
            build_test(root, binary)
        manifest = {
            "schema_version": 1,
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip(),
            "source_status": subprocess.check_output(
                ["git", "status", "--short"], cwd=root, text=True
            ).splitlines(),
            "gem5": str(gem5),
            "gem5_sha256": sha256(gem5),
            "test_binary": str(binary),
            "test_binary_sha256": sha256(binary),
            "cases": [asdict(case) for case in selected],
        }
        write_json(output / "manifest.json", manifest)
        results = {}
        for case in selected:
            results[case.name] = run_case(
                root, gem5, binary, output / case.name, case
            )
        if {"random_physical_4k", "random_physical_16k"} <= results.keys():
            if (results["random_physical_4k"]["output_hash"] !=
                    results["random_physical_16k"]["output_hash"]):
                raise GateError("4K/16K physical controls produced different hashes")
        write_json(output / "results.json", results)
        (output / "dx_virt_gate.pass").touch()
    except (GateError, OSError, subprocess.CalledProcessError) as exc:
        write_json(output / "failure.json", {"error": str(exc)})
        raise SystemExit(f"dx-virt-gate: {exc}") from exc
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
