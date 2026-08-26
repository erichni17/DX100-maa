#!/usr/bin/env python3
"""Seal or validate the read-only NAS IS scalar-SoA full correctness record.

This is deliberately a successor certificate: it reads the frozen historical
root and writes only an external certificate directory.  It never launches a
workload, and it refuses to write under either the source tree or raw root.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

HISTORICAL_ROOT = Path(
    "/data1/nier/dx100-runs/2026-08-24-is-scalar-soa-full-a44aaa60-r5"
)
OUTPUT_ROOT = Path(
    "/data1/nier/dx100-runs/2026-08-26-is-scalar-soa-full-certificate-r1"
)
SOURCE_COMMIT = "f7d268fff1e6a86d0d61bab86d546bb677f9b68b"
SOURCE_RELATIVE = "benchmarks/NAS/is/is.cpp"
SOURCE_SHA256 = (
    "5d9af5cc71fd972e24d24590e8d1f1bdf36b04e1fa338dcf8637260377c26510"
)
GEM5_SHA256 = (
    "2d02fa40568d3ed374258d717f15cad3afeca62343fc1ccaa1640215a8586152"
)
GUEST_SHA256 = (
    "c76e84ca3c1a5f29ffa2e9727ccda559cffed44fe6cb5cabb79587da344a1ff1"
)
INPUT_SHA256 = (
    "b70a33ed1a5017425c85ba664618f0dabac520df96b95894ec5657270cf75479"
)
BASELINE_SHA256 = (
    "d8cd2afe18de4f7983b1d9d59a0ea04e102a51bc7146a9d85c3c9a19cc73d069"
)
HEX = re.compile(r"[0-9a-f]{64}")
EXIT = re.compile(
    r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$", re.M
)
FATAL = re.compile(r"panic|fatal|assert|abort|segmentation fault|error:", re.I)
BEGIN = "---------- Begin Simulation Statistics"
END = "---------- End Simulation Statistics"

RAW_HASHES = {
    "manifest.txt": "fa81be80bff08c49af6e6cd82e06260b1fa560e0fc01bfb1b0a1e16c34bcb8bb",
    "result.tsv": "d823e2fe78ffc51d50b9de57798aaf2268c067183e0be74abc662665b0eba368",
    "terminal.status": "c26de83abdc9496cd1301470918ec39ecca1cf389ef0ae1c6504da1800d1c431",
    "checkpoint.exit": "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa",
    "checkpoint.log": "ec9543a15411560129000aaaae2f47c55294095f239e19045697dbfd3221c5cf",
    "checkpoint.command.txt": "35ece20db7fb8b4c99bfadc652c3ab9ba5dfb1c6cfd5972512246fd385679bb4",
    "runtime_gem5_recovery.manifest": "aa1ad131163b8386073672689d2244903b258a0a770b275eff6f40ee62bb65e7",
    "run/restore.exit": "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa",
    "run/restore.log": "164463e7f5043b8aa60c727cb0db087333f9818d92b9fce3c595885ec440b261",
    "run/stats.txt": "8c9d75f8c115837df914f8b41e4b8e3acaa4d0cb4229f9e035d822be8bd4325e",
    "run/config.ini": "473b1eb82d15691baa45f65b3673bc62057e80acd66586183821f2ecdc38be0d",
    "run/config.json": "42c138242bc1276b91856c702ff9c812ff0b8d1a8def39a8dbcb6bc18630b244",
    "run/command.txt": "ed04092e4365c3698434959e3c34d45d567c48d2c157bec60c6cf6b3414e2023",
    "checkpoint/citations.bib": "107591e44a9aaf00921aa25e1ff6542570e2a7eb81536764c828f6646f2a8dd5",
    "checkpoint/config.dot": "6972ca12a815aa2891536ac4d140fe734055e9a7d861c9286c889818caf16b9d",
    "checkpoint/config.dot.pdf": "9e787eb86c63f6a31face2dce3cb118c5555f72a067b04b81e2db04efb078c02",
    "checkpoint/config.dot.svg": "f02611ec44001d9ac643f95ae23099a8f275c3a6f698daaa6467f1d9a2656b97",
    "checkpoint/config.ini": "45f0ffca0f79f66667c75e79fcdd24bffd603046a02990b9b2bccd813ab92257",
    "checkpoint/config.json": "dea6ca3d1b2405a7cf95f9259cb9f77b6ff59e95ff5002e40632ff83f037f875",
    "checkpoint/stats.txt": "fc370e4903d835913350d264c613d0d27915bd9e59249875fc3378efcdceff93",
    "checkpoint/cpt.6471777500/m5.cpt": "dd9c2c8fd7cf8f2790868d0fe1bb4eb42690d4cea78ce10cc609be7e38ba6078",
    "checkpoint/cpt.6471777500/system.physmem.store0.pmem": "8ddb4c0b4561117ec904ecb540609793cd78431694b89ff6c937010e98ae683c",
    "checkpoint/fs/proc/cpuinfo": "dd54b96851250c297b943eda4273783818507a6dd6fcd2fa34fa62b70f2d00c4",
    "checkpoint/fs/proc/stat": "77f984b7e5466538244ea688c0225d6ce4be8c9571c181dd59f63990e028ea51",
    "checkpoint/fs/sys/devices/system/cpu/online": "f0e7dd3ebbfd0bb1bfeca50048635775fe0cc4eae4280a9a924de9450351064b",
    "checkpoint/fs/sys/devices/system/cpu/possible": "f0e7dd3ebbfd0bb1bfeca50048635775fe0cc4eae4280a9a924de9450351064b",
}
EXTERNAL_HASHES = {
    "/data1/nier/dx100-binaries/gem5-2d02fa40568d3ed374258d717f15cad3afeca62343fc1ccaa1640215a8586152.opt": GEM5_SHA256,
    "/data1/nier/dx100-runs/2026-08-24-is-scalar-soa-full-a44aaa60-r5/bin/is_maa_16K_scalar_soa_roi_verify": GUEST_SHA256,
    "/data1/nier/worktrees/DX100-full-tile-sweep-20260720/benchmarks/NAS/is/key_array_4C.h": INPUT_SHA256,
    "/data1/nier/worktrees/DX100-virtualization-line-handoff-20260812/experiments/analysis/physical_tile_sweep_baseline_20260822.json": BASELINE_SHA256,
}


class CertificateError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CertificateError(message)


def digest(path: Path) -> str:
    require(
        path.is_file() and not path.is_symlink(),
        f"missing regular file: {path}",
    )
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def kv(path: Path) -> dict[str, str]:
    return {
        key: value
        for line in path.read_text().splitlines()
        if "=" in line
        for key, value in (line.split("=", 1),)
    }


def fields(line: str) -> dict[str, str]:
    return {
        key: value
        for token in line.split()
        if "=" in token
        for key, value in (token.split("=", 1),)
    }


def first_window(path: Path) -> str:
    value = path.read_text()
    start = value.find(BEGIN)
    stop = value.find(END, start)
    require(start >= 0 and stop > start, "missing first statistics window")
    return value[start:stop]


def stat_sum(window: str, suffix: str) -> int:
    values = re.findall(
        rf"^\S*_{re.escape(suffix)}\s+([0-9]+)\b", window, re.M
    )
    require(values, f"missing first-window counter: {suffix}")
    return sum(map(int, values))


def exact_line(log: str, prefix: str) -> str:
    matches = [line for line in log.splitlines() if line.startswith(prefix)]
    require(len(matches) == 1, f"requires exactly one {prefix.strip()} marker")
    return matches[0]


def source_at_commit(repository: Path) -> str:
    try:
        content = subprocess.check_output(
            [
                "git",
                "-C",
                str(repository),
                "show",
                f"{SOURCE_COMMIT}:{SOURCE_RELATIVE}",
            ],
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise CertificateError(
            "cannot reconstruct IS source from source_commit"
        ) from error
    value = hashlib.sha256(content).hexdigest()
    require(value == SOURCE_SHA256, "Git-reconstructed IS source hash differs")
    return value


def independent_classifier(root: Path) -> None:
    script = Path(__file__).with_name("classify_hybrid_full_results.py")
    spec = importlib.util.spec_from_file_location(
        "hybrid_full_classifier", script
    )
    require(
        spec is not None and spec.loader is not None,
        "cannot load independent classifier",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.classify_is(root)
    require(
        result.get("status") == "terminal-valid",
        "independent hybrid classifier rejected evidence",
    )


def validate_evidence(
    root: Path,
    repository: Path,
    *,
    raw_hashes: dict[str, str] = RAW_HASHES,
    external_hashes: dict[str, str] = EXTERNAL_HASHES,
    use_independent_classifier: bool = True,
) -> dict[str, Any]:
    require(
        root.is_dir() and not root.is_symlink(), "historical root is missing"
    )
    for relative, expected in raw_hashes.items():
        require(
            digest(root / relative) == expected,
            f"pinned raw hash mismatch: {relative}",
        )
    for name, expected in external_hashes.items():
        require(
            digest(Path(name)) == expected,
            f"pinned external hash mismatch: {name}",
        )
    manifest = kv(root / "manifest.txt")
    for key, expected in (
        ("action", "full"),
        ("source_commit", SOURCE_COMMIT),
        ("source_sha256", SOURCE_SHA256),
        ("gem5_sha256", GEM5_SHA256),
        ("guest_sha256", GUEST_SHA256),
        ("input_sha256", INPUT_SHA256),
        ("frozen_native_sha256", BASELINE_SHA256),
        ("logical_elements", "16384"),
        ("physical_tile_elements", "4096"),
        ("memory_channels", "2"),
        ("row_table_slices", "32"),
        ("native_runs", "0"),
    ):
        require(manifest.get(key) == expected, f"manifest {key} mismatch")
    source_at_commit(repository)
    require(
        manifest.get("source_path") is not None, "manifest lacks source_path"
    )
    require(
        digest(Path(manifest["source_path"])) == SOURCE_SHA256,
        "mutable source differs from reconstructed source",
    )
    recovery = kv(root / "runtime_gem5_recovery.manifest")
    require(
        recovery
        == {
            "schema": "dx100.runtime_executable_recovery.v1",
            "reason": "lead_build_path_replaced_after_process_start",
            "unit": "dx100-is-scalar-soa-full-a44aaa60-r5",
            "main_pid": "1753022",
            "gem5_pid": "1755099",
            "gem5_pid_start_ticks": "289995712",
            "live_exe_link": "/data1/nier/worktrees/DX100-virtualization-line-handoff-20260812/build/X86/gem5.opt (deleted)",
            "live_exe_sha256": GEM5_SHA256,
            "archived_gem5_path": "/data1/nier/dx100-binaries/gem5-2d02fa40568d3ed374258d717f15cad3afeca62343fc1ccaa1640215a8586152.opt",
            "archived_gem5_sha256": GEM5_SHA256,
            "cmdline_sha256": "9e31231374914268a8989dd62ab09ce20ad4ffa1e640e612c40f67297cdeba42",
            "cgroup": "/user.slice/user-114457255.slice/user@114457255.service/app.slice/dx100-is-scalar-soa-full-a44aaa60-r5.service",
            "simulation_state_changed": "false",
        },
        "recovery identity mismatch",
    )
    require(
        (root / "checkpoint.exit").read_text().strip() == "0",
        "checkpoint exit is not zero",
    )
    require(
        (root / "run/restore.exit").read_text().strip() == "0",
        "restore exit is not zero",
    )
    require(
        (root / "terminal.status").read_text().strip() == "PASS",
        "terminal.status is not PASS",
    )
    log = (root / "run/restore.log").read_text()
    require(
        FATAL.search(log) is None
        and len(EXIT.findall(log)) == 1
        and log.count("ROI End!!!") == 1,
        "restore completion markers are invalid",
    )
    require(
        log.count("successfull: passed verification 6") == 1,
        "NAS verification evidence missing",
    )
    selection = fields(exact_line(log, "IS_SCALAR_SOA_JIT_SELECTION "))
    terminal = fields(exact_line(log, "IS_SCALAR_SOA_JIT_TERMINAL "))
    require(
        selection
        == {
            "compiled": "1",
            "treatment": "scalar_soa_jit",
            "legacy_default": "0",
        },
        "scalar source selection mismatch",
    )
    expected_terminal = {
        "treatment": "scalar_soa_jit",
        "logical": "16384",
        "scalar": "1",
        "predicate": "null",
        "min": "0",
        "max": "exact_count",
        "stride": "1",
        "generations": "2048",
        "full_windows": "2048",
        "tail_words": "0",
        "index_words": "33554432",
        "predicate_words": "0",
        "value_words": "0",
        "host_spd_reads": "0",
        "staging_bytes": "0",
        "result": "PASS",
    }
    require(
        terminal == expected_terminal, "IS terminal mechanism closure mismatch"
    )
    rows = (root / "result.tsv").read_text().splitlines()
    expected_row = "full\t379831843258\t2048\t2048\t33554432\t0\t2099200\t33554432\t0\t0\t0\t0\t31020345\t31020345\t31020345\t31020345\t33554432"
    require(
        len(rows) == 2 and rows[1] == expected_row,
        "result.tsv exact row mismatch",
    )
    window = first_window(root / "run/stats.txt")
    require(
        re.search(r"^simTicks\s+379831843258\b", window, re.M) is not None,
        "first simTicks differs",
    )
    required_stats = {
        "IND_SoaJitInstructions": 2048,
        "IND_SoaJitTerminalCompletions": 2048,
        "IND_SoaJitSelected": 33554432,
        "IND_SoaJitAliasesApplied": 33554432,
        "IND_SoaJitPredicateRejected": 0,
        "IND_SoaJitPredicateLineReads": 0,
        "IND_SoaJitPredicateLineResponses": 0,
        "IND_SoaJitValueReadIssues": 0,
        "IND_SoaJitValueReadResponses": 0,
        "IND_SoaJitAReadIssues": 31020345,
        "IND_SoaJitAReadResponses": 31020345,
        "IND_SoaJitAWriteIssues": 31020345,
        "IND_SoaJitAWriteResponses": 31020345,
        "DescriptorSpoolControlBytes": 0,
        "DescriptorSpoolBackingBytes": 0,
    }
    for suffix, expected in required_stats.items():
        require(
            stat_sum(window, suffix) == expected, f"stats {suffix} mismatch"
        )
    for suffix in (
        "cpu_spd_data_read_deferrals",
        "cpu_spd_data_read_retry_signals",
        "cpu_spd_data_read_retry_attempts",
        "cpu_spd_data_read_retry_acceptances",
    ):
        values = re.findall(
            rf"^\S*{re.escape(suffix)}\s+([0-9]+)\b", window, re.M
        )
        require(
            values and sum(map(int, values)) == 0, f"stats {suffix} mismatch"
        )
    config = set((root / "run/config.ini").read_text().splitlines())
    for item in (
        "num_cores=4",
        "num_tiles_per_core=8",
        "num_tile_elements=16384",
        "physical_tile_elements=4096",
        "num_offset_table_entries=16384",
        "num_offset_table_epoch_entries=16384",
        "num_initial_row_table_slices=32",
        "num_memory_channels=2",
    ):
        require(item in config, f"configuration geometry mismatch: {item}")
    require(
        sum(
            item in {"[system.mem_ctrls0]", "[system.mem_ctrls1]"}
            for item in config
        )
        == 2,
        "config lacks two channels",
    )
    if use_independent_classifier:
        independent_classifier(root)
    return {
        "verdict": "PASS_FULL_IS_CORRECTNESS",
        "performance_promoted": False,
        "native_rerun": False,
        "official_nas_verification": True,
        "correctness_provenance_simTicks": 379831843258,
        "physical_spd_payload_bytes": 4 * 8 * 4096 * 4,
        "staging_payload_bytes": 0,
        "source_commit": SOURCE_COMMIT,
        "source_sha256": SOURCE_SHA256,
        "raw_evidence_root": str(root),
    }


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    require(
        not temporary.exists() and not path.exists(),
        f"refusing existing output: {path}",
    )
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def validate_output(output: Path, certificate: dict[str, Any]) -> None:
    names = (
        "manifest.json",
        "certificate.json",
        "input_sha256.txt",
        "gate.complete",
    )
    require(
        output.is_dir()
        and all(
            (output / name).is_file() and not (output / name).is_symlink()
            for name in names
        ),
        "certificate output is incomplete",
    )
    require(
        json.loads((output / "certificate.json").read_text()) == certificate,
        "certificate content mismatch",
    )
    manifest = json.loads((output / "manifest.json").read_text())
    require(
        manifest.get("schema") == "dx100.is.scalar_soa_full.certificate.v1"
        and manifest.get("gate_written_last") is True,
        "certificate manifest invalid",
    )
    entries = (output / "input_sha256.txt").read_text().splitlines()
    parsed = {
        name: value
        for line in entries
        if len(line.split(maxsplit=1)) == 2
        for value, name in (line.split(maxsplit=1),)
    }
    expected = {
        str(HISTORICAL_ROOT / name): value
        for name, value in RAW_HASHES.items()
    }
    expected.update(EXTERNAL_HASHES)
    expected[f"git:{SOURCE_COMMIT}:{SOURCE_RELATIVE}"] = SOURCE_SHA256
    require(
        len(parsed) == len(entries)
        and parsed == expected
        and all(HEX.fullmatch(value) for value in parsed.values()),
        "input ledger mismatch",
    )
    require(
        (output / "gate.complete").read_text() == "PASS_FULL_IS_CORRECTNESS\n",
        "certificate gate mismatch",
    )


def publish(
    output: Path, certificate: dict[str, Any], root: Path, repository: Path
) -> None:
    resolved = output.resolve()
    require(
        not resolved.is_relative_to(root.resolve())
        and not resolved.is_relative_to(repository.resolve()),
        "output must be external to raw/source roots",
    )
    require(
        not output.exists(),
        "refusing existing or prematurely gated certificate output",
    )
    output.mkdir(parents=True)
    manifest = {
        "schema": "dx100.is.scalar_soa_full.certificate.v1",
        "historical_root": str(root),
        "gate_written_last": True,
        "write_policy": "external successor only; historical evidence is read-only",
    }
    inputs = {
        str(root / relative): digest(root / relative)
        for relative in RAW_HASHES
    }
    inputs.update({name: digest(Path(name)) for name in EXTERNAL_HASHES})
    inputs[f"git:{SOURCE_COMMIT}:{SOURCE_RELATIVE}"] = SOURCE_SHA256
    atomic_write(
        output / "manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    atomic_write(
        output / "certificate.json",
        json.dumps(certificate, indent=2, sort_keys=True) + "\n",
    )
    atomic_write(
        output / "input_sha256.txt",
        "".join(
            f"{value}  {name}\n" for name, value in sorted(inputs.items())
        ),
    )
    atomic_write(output / "gate.complete", "PASS_FULL_IS_CORRECTNESS\n")
    validate_output(output, certificate)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--historical-root", type=Path, default=HISTORICAL_ROOT
    )
    parser.add_argument("--output", type=Path, default=OUTPUT_ROOT)
    parser.add_argument(
        "--source-repo", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument(
        "--validate", action="store_true", help="validate only; never mutate"
    )
    args = parser.parse_args()
    try:
        certificate = validate_evidence(args.historical_root, args.source_repo)
        if args.validate:
            if args.output.exists():
                validate_output(args.output, certificate)
        else:
            publish(
                args.output,
                certificate,
                args.historical_root,
                args.source_repo,
            )
    except CertificateError as error:
        print(f"REJECT_FULL_IS_CERTIFICATE: {error}")
        return 2
    print(f"{certificate['verdict']} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
