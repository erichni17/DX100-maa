#!/usr/bin/env python3
"""Create one read-only successor for the strict line-combined full-CG run."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "experiments/scripts/run_cg_strict_line_combined_full.py"
SPEC = importlib.util.spec_from_file_location(
    "cg_strict_line_combined_full_successor_gate", RUNNER_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load strict full runner: {RUNNER_PATH}")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

RAW_ROOT = Path(
    "/data1/nier/dx100-runs/"
    "2026-08-27-cg-strict-line-combined-full-r1"
)
RAW_SOURCE_COMMIT = "b3ce3d2a04866cf946b4c990ad330d1f76ac9cbe"
REGISTERED_RUNNER_PID = 261_899
REGISTERED_RUNNER_START_TICKS = 322_863_791
REGISTERED_RESTORE_PID = 283_865
REGISTERED_RESTORE_START_TICKS = 322_952_505
PROCESS_EXIT_CALLBACK = Path(
    "/data1/nier/worktrees/codex-coordination/sessions/"
    "full-cg-linecombined-promotion-20260827-20260827-221514-7b54c618/"
    "cg-strict-line-combined-full-r1-process-exit.callback"
)
RAW_HASHES = {
    "manifest.json": (
        "72fa9cd6a353358cc754c49c9f23e2538dec5a22b5e32d2f9bb2e42bed23659d"
    ),
    "checkpoint.log": (
        "464e313a17108512e8dac91fa2b6fa399dd10e8078a9a3a366a4d990fe99b848"
    ),
    "checkpoint.log.exit": (
        "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa"
    ),
    "run/restore.log": (
        "782a54492de250c9d8f43ed39361126681beb2a0d9c19c17f0b2f5f2f59125ed"
    ),
    "run/restore.log.exit": (
        "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa"
    ),
    "run/stats.txt": (
        "486414f1d68a16d1b28b283bdd45433f4d84b5a8a300b831aa7439b82d87ad57"
    ),
    "run/config.ini": (
        "3d0c81ffdabb0919912d49f5c1df385be6ff31b52b22c1b474b9f8cd9e90f7d9"
    ),
    "runtime_terminal.json": (
        "275f0d3779ddd94b48d872e31f4191e7f7aa60f7ea27eeed3aa65059ae05815c"
    ),
    "progress.json": (
        "78448ffaf4fa5b4c7a39150a7499aefb1b20081c1e3ad2259390196137a6d3fd"
    ),
    "input/artifact_sha256.before": (
        "962164200265f1a5fbb5bf3594f9526da186c36d61baa07c4d87c0cb95339974"
    ),
    "input/artifact_sha256.after": (
        "962164200265f1a5fbb5bf3594f9526da186c36d61baa07c4d87c0cb95339974"
    ),
    "input/checkpoint.files.sha256.before": (
        "189c8c38f046277e64a491d72d8bb59cf03d541eabdcdf955df3674da34615ed"
    ),
    "input/checkpoint.files.sha256.after": (
        "189c8c38f046277e64a491d72d8bb59cf03d541eabdcdf955df3674da34615ed"
    ),
    "input/compile_command.json": (
        "3a1a5ec48c0398bd5c1c11823785ad0e43c1bb198b1a50b07ce3c412441ed2b9"
    ),
    "input/checkpoint_command.json": (
        "5eb0c2387f3d4873068756a27f1bd0898b4408e40131e917fbd3216efd931c4d"
    ),
    "input/restore_command.json": (
        "701f6e4d31ed3418cb8a91ca9374ca44e8903b82f43e011f37999772f8c54e30"
    ),
    "input/source_status.before": (
        "fefcd1083d70bf92f8aa4d21f21a3fcebca2986c4d701eaa85b8fbb09fa297ae"
    ),
    "input/source_status.after": (
        "fefcd1083d70bf92f8aa4d21f21a3fcebca2986c4d701eaa85b8fbb09fa297ae"
    ),
    "input/source_commit.before": (
        "82982b3afe81b72c0140af95a5f8c6ab5dfd56de79ba43148d8ee96579a55e4f"
    ),
    "input/source_commit.after": (
        "82982b3afe81b72c0140af95a5f8c6ab5dfd56de79ba43148d8ee96579a55e4f"
    ),
}
MANIFEST_SCHEMA = "dx100.cg.strict_line_combined_full.successor_manifest.v1"
CERTIFICATE_SCHEMA = (
    "dx100.cg.strict_line_combined_full.successor_certificate.v1"
)
VERDICT = "PASS_NUMERICAL_MECHANISM_CORRECT"


class CertificateError(RuntimeError):
    """The read-only successor rejected its raw input."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CertificateError(message)


def sha256_file(path: Path) -> str:
    return runner.sha256_file(path)


def exact_hash(path: Path, expected: str, description: str) -> None:
    require(
        path.is_file()
        and not path.is_symlink()
        and sha256_file(path) == expected,
        f"hash mismatch for {description}: {path}",
    )


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def raw_stat_state() -> dict[str, tuple[int, int, int, int]]:
    result = {}
    for path in sorted(RAW_ROOT.rglob("*")):
        if path.is_file():
            info = path.stat()
            result[str(path.relative_to(RAW_ROOT))] = (
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                stat.S_IMODE(info.st_mode),
            )
    return result


def git_blob_hash(commit: str, relative: Path) -> str:
    data = subprocess.check_output(
        ["git", "show", f"{commit}:{relative.as_posix()}"], cwd=ROOT
    )
    return hashlib.sha256(data).hexdigest()


def verify_artifact_ledger(ledger: Path) -> int:
    lines = ledger.read_text(encoding="utf-8").splitlines()
    require(bool(lines), "empty raw artifact ledger")
    seen: set[Path] = set()
    for number, line in enumerate(lines, 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (/.+)", line)
        require(match is not None, f"bad artifact ledger line {number}")
        expected = match.group(1)  # type: ignore[union-attr]
        path = Path(match.group(2))  # type: ignore[union-attr]
        require(path not in seen, f"duplicate artifact ledger path: {path}")
        seen.add(path)
        if path == ROOT or ROOT in path.parents:
            relative = path.relative_to(ROOT)
            actual = git_blob_hash(RAW_SOURCE_COMMIT, relative)
        else:
            require(
                path.is_file() and not path.is_symlink(),
                f"missing raw artifact: {path}",
            )
            actual = sha256_file(path)
        require(actual == expected, f"raw artifact changed: {path}")
    return len(lines)


def validate_raw() -> dict[str, Any]:
    require(RAW_ROOT.is_dir() and not RAW_ROOT.is_symlink(), "bad raw root")
    for name, digest in RAW_HASHES.items():
        exact_hash(RAW_ROOT / name, digest, f"raw {name}")
    require(
        PROCESS_EXIT_CALLBACK.is_file()
        and not PROCESS_EXIT_CALLBACK.is_symlink()
        and PROCESS_EXIT_CALLBACK.stat().st_size == 0,
        "process-exit callback did not fire",
    )
    terminal = load_json(RAW_ROOT / "runtime_terminal.json")
    progress = load_json(RAW_ROOT / "progress.json")
    require(
        terminal
        == {
            "accepted": False,
            "error": "duplicate p generation: 1",
            "error_type": "GateError",
            "schema": "dx100.cg.strict_line_combined_full_runtime.v1",
            "terminal": True,
        }
        and progress.get("stage") == "rejected"
        and progress.get("error") == "duplicate p generation: 1",
        "raw obsolete-gate failure identity changed",
    )
    manifest = load_json(RAW_ROOT / "manifest.json")
    require(
        manifest.get("schema")
        == "dx100.cg.strict_line_combined_full_manifest.v1"
        and manifest.get("terminal") is False
        and manifest.get("candidate_only") is True
        and manifest.get("candidate_restores") == 1
        and manifest.get("checkpoint_creations") == 1
        and manifest.get("native_runs") == 0
        and manifest.get("direct4_runs") == 0
        and manifest.get("fused_runs") == 0
        and manifest.get("control_runs") == 0
        and manifest.get("source_commit") == RAW_SOURCE_COMMIT,
        "raw manifest identity changed",
    )
    require(
        not (RAW_ROOT / "result.json").exists()
        and not (RAW_ROOT / "gate.complete").exists()
        and not (RAW_ROOT / "certified_artifacts.sha256").exists(),
        "raw root unexpectedly contains a success seal",
    )
    require(
        (RAW_ROOT / "checkpoint.log.exit").read_text() == "0\n"
        and (RAW_ROOT / "run/restore.log.exit").read_text() == "0\n",
        "raw checkpoint/restore wrapper exit changed",
    )
    checkpoint_lines = (RAW_ROOT / "checkpoint.log").read_text(
        errors="replace"
    ).splitlines()
    runner.base.exactly_one(
        checkpoint_lines,
        r"^Exiting @ tick [0-9]+ because checkpoint$",
        "raw checkpoint terminal",
    )
    require(
        not any(
            line.startswith(
                ("CG_FINGERPRINT ", "CG_LOGICAL16_RMW_TERMINAL ", "ROI End!!!")
            )
            for line in checkpoint_lines
        ),
        "raw checkpoint crossed the treatment boundary",
    )
    restore_lines = (RAW_ROOT / "run/restore.log").read_text(
        errors="replace"
    ).splitlines()
    require(
        not any(runner.base.FATAL_RE.search(line) for line in restore_lines),
        "raw restore contains fatal text",
    )
    runner.base.exactly_one(
        restore_lines,
        r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$",
        "raw m5 terminal",
    )
    require(
        sum(line == "ROI End!!!" for line in restore_lines) == 1,
        "raw ROI terminal changed",
    )
    before_artifacts = (
        RAW_ROOT / "input/artifact_sha256.before"
    ).read_text()
    after_artifacts = (RAW_ROOT / "input/artifact_sha256.after").read_text()
    before_checkpoint = (
        RAW_ROOT / "input/checkpoint.files.sha256.before"
    ).read_text()
    after_checkpoint = (
        RAW_ROOT / "input/checkpoint.files.sha256.after"
    ).read_text()
    require(before_artifacts == after_artifacts, "raw artifact ledgers differ")
    require(
        before_checkpoint == after_checkpoint,
        "raw checkpoint ledgers differ",
    )
    artifact_entries = verify_artifact_ledger(
        RAW_ROOT / "input/artifact_sha256.before"
    )
    checkpoint_entries = runner.verify_tree_ledger(
        RAW_ROOT / "checkpoint",
        RAW_ROOT / "input/checkpoint.files.sha256.before",
    )
    for name in ("source_status", "source_commit"):
        require(
            (RAW_ROOT / f"input/{name}.before").read_text()
            == (RAW_ROOT / f"input/{name}.after").read_text(),
            f"raw {name} changed during execution",
        )
    require(
        (RAW_ROOT / "input/source_commit.before").read_text()
        == RAW_SOURCE_COMMIT + "\n",
        "raw source commit changed",
    )
    command = load_json(RAW_ROOT / "input/restore_command.json")
    for flag in (
        "--maa_virtual_strict_two_phase",
        "--maa_virtual_masked_writes",
        "--maa_soa_jit_value_cache_enable",
        "--maa_soa_jit_apply_lanes=4",
    ):
        require(command.count(flag) == 1, f"raw command changed: {flag}")
    require(
        "direct4_product_page_fed_q16" not in " ".join(command),
        "raw command contains direct4",
    )
    return {
        "manifest": manifest,
        "artifact_ledger_entries": artifact_entries,
        "checkpoint_ledger_entries": checkpoint_entries,
        "wrapper_outcome": "obsolete_global_generation_key_gate_failure",
        "simulation_terminal_prerequisites": True,
    }


def write_progress(path: Path | None, stage: str, **fields: Any) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    runner.atomic_json(
        path,
        {
            "schema": "dx100.cg.strict_line_combined_full_classifier.v1",
            "stage": stage,
            "updated_unix_ns": time.time_ns(),
            **fields,
        },
    )


def build_documents(
    progress: Path | None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    before = raw_stat_state()
    raw = validate_raw()
    numerical_authority = runner.base.validate_certificate()
    lane_authority = runner.lane.validate_lane_selection_authority()
    strict_authority = runner.validate_strict_selection_authority()
    frozen_full = runner.validate_frozen_full_certificate()
    _, authority_fields = runner.base.fingerprint_fields(
        runner.base.NATIVE_LOG
    )

    def trace_progress(lines: int, windows: int) -> None:
        write_progress(
            progress,
            "validating_strict_trace",
            trace_lines_scanned=lines,
            whole_windows_seen=windows,
        )

    candidate, deltas = runner.validate_restore(
        RAW_ROOT / "run", authority_fields, progress=trace_progress
    )
    after = raw_stat_state()
    require(before == after, "read-only classification mutated the raw root")
    stats = candidate.get("stats")
    trace = candidate.get("strict_trace")
    require(isinstance(stats, dict), "candidate stats are not a mapping")
    require(isinstance(trace, dict), "trace summary is not a mapping")
    require(
        trace.get("p_timing")
        == trace.get("q_timing")
        == trace.get("whole_windows")
        == runner.EXPECTED_WINDOWS
        and trace.get("product_pages") == runner.EXPECTED_PRODUCT_PAGES
        and candidate.get("all_p_writes_64_bytes") is True
        and isinstance(trace.get("trace_sha256"), str),
        "terminal strict trace identity changed",
    )
    sim_ticks = stats.get("simTicks")
    require(isinstance(sim_ticks, int) and sim_ticks > 0, "invalid simTicks")
    input_lines = [
        f"{digest}  {RAW_ROOT / name}"
        for name, digest in sorted(RAW_HASHES.items())
    ]
    input_lines.extend(
        (
            f"{trace['trace_sha256']}  {RAW_ROOT / 'run/strict_trace.log'}",
            f"{sha256_file(RUNNER_PATH)}  {RUNNER_PATH}",
            f"{sha256_file(Path(__file__).resolve())}  "
            f"{Path(__file__).resolve()}",
        )
    )
    input_ledger = "\n".join(sorted(input_lines)) + "\n"
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "read_only_inputs": True,
        "gem5_runs_launched": 0,
        "raw_root_modified": False,
        "raw_root": str(RAW_ROOT),
        "raw_source_commit": RAW_SOURCE_COMMIT,
        "raw_pinned_sha256": RAW_HASHES,
        "trace_sha256": trace["trace_sha256"],
        "trace_bytes": trace["trace_bytes"],
        "registered_runtime": {
            "runner_pid": REGISTERED_RUNNER_PID,
            "runner_start_ticks": REGISTERED_RUNNER_START_TICKS,
            "restore_pid": REGISTERED_RESTORE_PID,
            "restore_start_ticks": REGISTERED_RESTORE_START_TICKS,
            "process_exit_callback": str(PROCESS_EXIT_CALLBACK),
        },
        "raw_validation": raw,
    }
    certificate = {
        "schema": CERTIFICATE_SCHEMA,
        "terminal": True,
        "verdict": VERDICT,
        "read_only_successor": True,
        "gem5_runs_launched": 0,
        "raw_root_modified": False,
        "raw_wrapper_exit": 1,
        "raw_wrapper_outcome": raw["wrapper_outcome"],
        "candidate_only": True,
        "observations": 1,
        "native_runs": 0,
        "direct4_runs": 0,
        "fused_runs": 0,
        "native_speedup_claim": False,
        "direct4_claim": False,
        "official_nas_verification": False,
        "numerical_authority": numerical_authority,
        "numerical_relative_deltas_vs_authority": deltas,
        "lane_authority": lane_authority,
        "strict_line_combined_authority": strict_authority,
        "frozen_full_certificate": frozen_full,
        "source_commit": RAW_SOURCE_COMMIT,
        "gem5_sha256": runner.STRICT_GEM5_SHA256,
        "guest_sha256": runner.sha256_file(
            RAW_ROOT / "bin/cg_strict_line_combined_full"
        ),
        "first_roi_simTicks": sim_ticks,
        "performance_comparison_claim": False,
        "candidate": candidate,
    }
    return manifest, certificate, input_ledger


def write_exclusive(path: Path, contents: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(contents)
        stream.flush()
        os.fsync(stream.fileno())


def create_certificate(output: Path, progress: Path | None) -> dict[str, Any]:
    require(
        not output.exists(),
        f"refusing existing certificate root: {output}",
    )
    require(
        output.parent == Path("/data1/nier/dx100-runs"),
        "certificate root must be directly under the runs root",
    )
    write_progress(progress, "validating_raw")
    manifest, certificate, input_ledger = build_documents(progress)
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    certificate_text = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    output.mkdir(mode=0o755)
    write_exclusive(output / "manifest.json", manifest_text)
    write_exclusive(output / "certificate.json", certificate_text)
    write_exclusive(output / "input_sha256.txt", input_ledger)
    gate = (
        VERDICT
        + "\nread_only_successor=true\nraw_root_modified=false\n"
        + "manifest_sha256="
        + hashlib.sha256(manifest_text.encode()).hexdigest()
        + "\n"
        + "certificate_sha256="
        + hashlib.sha256(certificate_text.encode()).hexdigest()
        + "\ninput_sha256="
        + hashlib.sha256(input_ledger.encode()).hexdigest()
        + "\n"
    )
    write_exclusive(output / "gate.complete", gate)
    write_progress(
        progress,
        "accepted",
        certificate_root=str(output),
        verdict=VERDICT,
    )
    return certificate


def validate_seal(output: Path) -> dict[str, Any]:
    require(
        output.is_dir() and not output.is_symlink(),
        "bad certificate root",
    )
    require(
        {path.name for path in output.iterdir()}
        == {
            "manifest.json",
            "certificate.json",
            "input_sha256.txt",
            "gate.complete",
        },
        "certificate artifact set changed",
    )
    manifest_text = (output / "manifest.json").read_text()
    certificate_text = (output / "certificate.json").read_text()
    input_text = (output / "input_sha256.txt").read_text()
    gate = (output / "gate.complete").read_text()
    expected = (
        VERDICT
        + "\nread_only_successor=true\nraw_root_modified=false\n"
        + "manifest_sha256="
        + hashlib.sha256(manifest_text.encode()).hexdigest()
        + "\n"
        + "certificate_sha256="
        + hashlib.sha256(certificate_text.encode()).hexdigest()
        + "\ninput_sha256="
        + hashlib.sha256(input_text.encode()).hexdigest()
        + "\n"
    )
    require(gate == expected, "certificate gate seal changed")
    certificate = json.loads(certificate_text)
    require(
        certificate.get("verdict") == VERDICT
        and certificate.get("read_only_successor") is True
        and certificate.get("gem5_runs_launched") == 0,
        "certificate claims changed",
    )
    return certificate


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--validate-seal", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output = args.output.resolve()
    progress = args.progress.resolve() if args.progress else None
    try:
        if args.validate_seal:
            certificate = validate_seal(output)
        else:
            require(
                len(runner.source_status().splitlines()) == 1,
                "classifier requires a clean source worktree",
            )
            certificate = create_certificate(output, progress)
    except Exception as error:
        write_progress(
            progress,
            "rejected",
            error_type=type(error).__name__,
            error=str(error),
        )
        raise
    print(
        json.dumps(
            {
                "terminal": True,
                "verdict": certificate["verdict"],
                "certificate_root": str(output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
