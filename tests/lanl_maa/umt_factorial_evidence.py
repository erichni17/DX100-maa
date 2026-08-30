#!/usr/bin/env python3
"""Fail-closed UMT factorial build identity and stat expectations."""

import datetime
import hashlib
import pathlib
import re
import subprocess
from dataclasses import dataclass

BUILD_MANIFEST_SCHEMA = "lanl-maa-reproducible-gem5-build-v2"
CELL_VARIANTS = {
    (24, 1): "X86_UMT_T24_W1",
    (24, 2): "X86_UMT_T24_W2",
    (32, 1): "X86_UMT_T32_W1",
    (32, 2): "X86_UMT_T32_W2",
}
CONFIG_SYMBOLS = {
    "compute_tokens": "LANL_MAA_UMT_COMPUTE_TOKENS",
    "fp_issue_width": "LANL_MAA_UMT_FP_ISSUE_WIDTH",
}
MANIFEST_KEYS = {
    "schema",
    "status",
    "cell",
    "source_commit",
    "source_tree",
    "source_clean_before_and_after",
    "source_identity_unchanged",
    "command",
    "returncode",
    "started_at",
    "ended_at",
    "required_relink_observed",
    "build_opts",
    "build_opts_sha256",
    "kconfig_state",
    "kconfig_state_sha256",
    "generated_config_headers",
    "target",
    "target_size",
    "target_mtime_ns",
    "gem5_sha256",
    "frozen_gem5",
    "frozen_gem5_sha256",
    "stdout_sha256",
    "stderr_sha256",
    "builder_sha256",
    "claim_boundary",
}
CELL_KEYS = {"compute_tokens", "fp_issue_width", "variant"}
HEADER_KEYS = {"path", "sha256", "symbol", "value"}
FACTORIAL_HARNESS_PATHS = {
    "tests/lanl_maa/run_umt_ordered_wave_mixed_evidence_smoke.py",
    "tests/lanl_maa/run_umt_ordered_wave_poison_tail_smoke.py",
    "tests/lanl_maa/test_umt_factorial_evidence.py",
    "tests/lanl_maa/test_umt_ordered_wave_mixed_evidence.py",
    "tests/lanl_maa/test_umt_ordered_wave_poison_tail_evidence.py",
    "tests/lanl_maa/umt_factorial_evidence.py",
    "tests/lanl_maa/umt_ordered_wave_mixed_evidence_smoke.py",
    "tests/lanl_maa/umt_ordered_wave_poison_tail_smoke.py",
}


@dataclass(frozen=True)
class FactorialCell:
    compute_tokens: int
    fp_issue_width: int
    variant: str

    @property
    def key(self):
        return f"t{self.compute_tokens}w{self.fp_issue_width}"

    def document(self):
        return {
            "compute_tokens": self.compute_tokens,
            "fp_issue_width": self.fp_issue_width,
            "variant": self.variant,
        }


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)


def is_sha1(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)


def parse_assignments(path):
    assignments = {}
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("=") != 1:
            raise RuntimeError(f"malformed assignment in {path}: {line}")
        name, value = line.split("=", 1)
        if name in assignments:
            raise RuntimeError(f"duplicate assignment in {path}: {name}")
        assignments[name] = value.strip('"')
    return assignments


def parse_generated_define(path, symbol):
    match = re.fullmatch(
        rf"#define\s+{re.escape(symbol)}\s+([0-9]+)\s*",
        pathlib.Path(path).read_text(encoding="utf-8"),
    )
    if match is None:
        raise RuntimeError(f"malformed generated config header: {path}")
    return int(match.group(1))


def parse_manifest_timestamp(value, label):
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"build manifest {label} is empty")
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError as error:
        raise RuntimeError(
            f"build manifest {label} is not an ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError(
            f"build manifest {label} lacks an explicit UTC offset"
        )
    return parsed


def timestamp_ns(value):
    epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    delta = value.astimezone(datetime.timezone.utc) - epoch
    return (
        delta.days * 86400 + delta.seconds
    ) * 1_000_000_000 + delta.microseconds * 1000


def cell_from_document(document):
    if not isinstance(document, dict) or set(document) != CELL_KEYS:
        raise RuntimeError("build manifest cell has missing or unknown fields")
    tokens = document.get("compute_tokens")
    width = document.get("fp_issue_width")
    if type(tokens) is not int or type(width) is not int:
        raise RuntimeError("build manifest cell parameters must be integers")
    variant = CELL_VARIANTS.get((tokens, width))
    if variant is None:
        raise RuntimeError("build manifest names an unsupported UMT cell")
    if document.get("variant") != variant:
        raise RuntimeError("build manifest cell variant mismatches parameters")
    return FactorialCell(tokens, width, variant)


def validate_header_document(document, label, cell):
    if not isinstance(document, dict) or set(document) != HEADER_KEYS:
        raise RuntimeError(
            f"build manifest {label} header has missing or unknown fields"
        )
    symbol = CONFIG_SYMBOLS[label]
    expected_value = getattr(cell, label)
    if document["symbol"] != symbol or document["value"] != expected_value:
        raise RuntimeError(
            f"build manifest {label} header mismatches its cell"
        )
    if not is_sha256(document["sha256"]):
        raise RuntimeError(
            f"build manifest {label} header hash is not SHA-256"
        )
    if not isinstance(document["path"], str) or not document["path"]:
        raise RuntimeError(f"build manifest {label} header path is empty")


def validate_build_manifest_document(document):
    if not isinstance(document, dict) or set(document) != MANIFEST_KEYS:
        raise RuntimeError("build manifest has missing or unknown fields")
    if document["schema"] != BUILD_MANIFEST_SCHEMA:
        raise RuntimeError("build manifest schema changed")
    cell = cell_from_document(document["cell"])
    headers = document["generated_config_headers"]
    if not isinstance(headers, dict) or set(headers) != set(CONFIG_SYMBOLS):
        raise RuntimeError("build manifest generated-header set changed")
    for label in CONFIG_SYMBOLS:
        validate_header_document(headers[label], label, cell)

    for name in (
        "build_opts_sha256",
        "kconfig_state_sha256",
        "gem5_sha256",
        "frozen_gem5_sha256",
        "stdout_sha256",
        "stderr_sha256",
        "builder_sha256",
    ):
        if not is_sha256(document[name]):
            raise RuntimeError(f"build manifest {name} is not SHA-256")
    for name in ("source_commit", "source_tree"):
        if not is_sha1(document[name]):
            raise RuntimeError(f"build manifest {name} is not a full SHA-1")
    if document["status"] != "passed" or document["returncode"] != 0:
        raise RuntimeError("build manifest does not record a passed build")
    for name in (
        "source_clean_before_and_after",
        "source_identity_unchanged",
        "required_relink_observed",
    ):
        if document[name] is not True:
            raise RuntimeError(f"build manifest {name} is not true")
    expected_command = [
        str(pathlib.Path("/usr/bin/scons").resolve()),
        "--ignore-style",
        f"build/{cell.variant}/gem5.opt",
        "-j4",
    ]
    if document["command"] != expected_command:
        raise RuntimeError(
            "build manifest command is not the cell-specific capped J4 build"
        )
    if document["gem5_sha256"] != document["frozen_gem5_sha256"]:
        raise RuntimeError("build manifest target and frozen hashes differ")
    for name in ("target_size", "target_mtime_ns"):
        if type(document[name]) is not int or document[name] <= 0:
            raise RuntimeError(f"build manifest {name} is not positive")
    for name in (
        "started_at",
        "ended_at",
        "build_opts",
        "kconfig_state",
        "target",
        "frozen_gem5",
        "claim_boundary",
    ):
        if not isinstance(document[name], str) or not document[name]:
            raise RuntimeError(f"build manifest {name} is empty")
    return cell


def source_root_from_target(document, cell):
    target = pathlib.Path(document["target"]).resolve()
    if (
        target.name != "gem5.opt"
        or target.parent.name != cell.variant
        or target.parent.parent.name != "build"
    ):
        raise RuntimeError(
            "build manifest target path does not match its cell"
        )
    return target.parent.parent.parent


def validate_build_manifest_files(document, build_manifest_path, gem5):
    cell = validate_build_manifest_document(document)
    manifest_path = pathlib.Path(build_manifest_path).resolve()
    gem5 = pathlib.Path(gem5).resolve()
    source_root = source_root_from_target(document, cell)
    expected_paths = {
        "build_opts": source_root / "build_opts" / cell.variant,
        "kconfig_state": (
            source_root / "build" / cell.variant / "gem5.build" / "config"
        ),
        "target": source_root / "build" / cell.variant / "gem5.opt",
        "frozen_gem5": gem5,
    }
    for name, expected in expected_paths.items():
        if pathlib.Path(document[name]).resolve() != expected.resolve():
            raise RuntimeError(f"build manifest {name} path changed")
        if not expected.is_file():
            raise RuntimeError(f"build manifest {name} artifact is absent")
    if manifest_path.parent != gem5.parent:
        raise RuntimeError("build manifest and frozen gem5 are separated")

    build_logs = {
        "stdout": manifest_path.parent / "build.stdout",
        "stderr": manifest_path.parent / "build.stderr",
    }
    for name, path in build_logs.items():
        if not path.is_file():
            raise RuntimeError(f"build manifest {name} log is absent")
        if sha256(path) != document[f"{name}_sha256"]:
            raise RuntimeError(f"build manifest {name} log hash mismatches")
    try:
        stdout_lines = (
            build_logs["stdout"].read_text(encoding="utf-8").splitlines()
        )
    except UnicodeDecodeError as error:
        raise RuntimeError("build manifest stdout log is not UTF-8") from error
    link_record = re.compile(
        rf"\s*\[    LINK\]\s+->\s+(?:build/)?"
        rf"{re.escape(cell.variant)}/gem5\.opt\s*"
    )
    if not any(link_record.fullmatch(line) for line in stdout_lines):
        raise RuntimeError(
            "build manifest stdout lacks the exact cell-specific relink "
            "record"
        )

    artifact_hashes = {
        "build_opts": "build_opts_sha256",
        "kconfig_state": "kconfig_state_sha256",
        "target": "gem5_sha256",
        "frozen_gem5": "frozen_gem5_sha256",
    }
    for path_name, hash_name in artifact_hashes.items():
        if sha256(expected_paths[path_name]) != document[hash_name]:
            raise RuntimeError(
                f"build manifest {path_name} artifact hash mismatches"
            )

    target_stat = expected_paths["target"].stat()
    if target_stat.st_size != document["target_size"]:
        raise RuntimeError("build manifest target size mismatches artifact")
    if target_stat.st_mtime_ns != document["target_mtime_ns"]:
        raise RuntimeError("build manifest target mtime mismatches artifact")
    started_at = parse_manifest_timestamp(document["started_at"], "started_at")
    ended_at = parse_manifest_timestamp(document["ended_at"], "ended_at")
    started_ns = timestamp_ns(started_at)
    ended_ns = timestamp_ns(ended_at)
    if ended_ns < started_ns:
        raise RuntimeError("build manifest build interval is reversed")
    if not started_ns <= target_stat.st_mtime_ns <= ended_ns:
        raise RuntimeError(
            "build manifest target mtime is outside the build interval"
        )

    expected_values = {
        CONFIG_SYMBOLS["compute_tokens"]: cell.compute_tokens,
        CONFIG_SYMBOLS["fp_issue_width"]: cell.fp_issue_width,
    }
    for path_name in ("build_opts", "kconfig_state"):
        assignments = parse_assignments(expected_paths[path_name])
        for symbol, expected in expected_values.items():
            if assignments.get(symbol) != str(expected):
                raise RuntimeError(
                    f"build manifest {path_name} mismatches cell {symbol}"
                )

    for label, symbol in CONFIG_SYMBOLS.items():
        header_document = document["generated_config_headers"][label]
        expected_header = (
            source_root
            / "build"
            / cell.variant
            / "config"
            / f"{symbol.lower()}.hh"
        )
        if pathlib.Path(header_document["path"]).resolve() != expected_header:
            raise RuntimeError(
                f"build manifest {label} generated-header path changed"
            )
        if not expected_header.is_file():
            raise RuntimeError(
                f"build manifest {label} generated header is absent"
            )
        if sha256(expected_header) != header_document["sha256"]:
            raise RuntimeError(
                f"build manifest {label} generated-header hash mismatches"
            )
        if parse_generated_define(expected_header, symbol) != getattr(
            cell, label
        ):
            raise RuntimeError(
                f"generated {label} header mismatches manifest cell"
            )
    return cell, source_root


def git(root, *arguments):
    return subprocess.check_output(
        ["git", *arguments], cwd=root, text=True
    ).strip()


def validate_repository_boundary(
    document, source_root, harness_root, allowed_harness_paths
):
    source_root = pathlib.Path(source_root).resolve()
    harness_root = pathlib.Path(harness_root).resolve()
    if git(source_root, "rev-parse", "HEAD") != document["source_commit"]:
        raise RuntimeError("build worktree HEAD differs from build manifest")
    if git(source_root, "rev-parse", "HEAD^{tree}") != document["source_tree"]:
        raise RuntimeError("build worktree tree differs from build manifest")
    if git(source_root, "status", "--porcelain=v1"):
        raise RuntimeError("build source worktree is no longer clean")
    builder = source_root / "tests/lanl_maa/run_reproducible_gem5_build.py"
    harness_builder = (
        harness_root / "tests/lanl_maa/run_reproducible_gem5_build.py"
    )
    if (
        sha256(builder) != document["builder_sha256"]
        or sha256(harness_builder) != document["builder_sha256"]
    ):
        raise RuntimeError("build manifest builder hash is stale")

    actual_tree = git(
        harness_root, "rev-parse", f"{document['source_commit']}^{{tree}}"
    )
    if actual_tree != document["source_tree"]:
        raise RuntimeError("manifest source commit and tree disagree")
    ancestry = subprocess.run(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            document["source_commit"],
            "HEAD",
        ],
        cwd=harness_root,
        check=False,
    )
    if ancestry.returncode != 0:
        raise RuntimeError("build source commit is not a harness ancestor")
    changed_paths = set(
        git(
            harness_root,
            "diff",
            "--name-only",
            f"{document['source_commit']}..HEAD",
        ).splitlines()
    )
    if not changed_paths.issubset(set(allowed_harness_paths)):
        raise RuntimeError(
            "harness commits after build changed production source: "
            f"{sorted(changed_paths)}"
        )
    if git(harness_root, "status", "--porcelain=v1"):
        raise RuntimeError("factorial evidence harness worktree is not clean")


def static_cell_stats(cell):
    token_bits = cell.compute_tokens * 471
    functional_bits = 656 if cell.compute_tokens == 24 else 657
    instrumentation_bits = 1169 if cell.compute_tokens == 24 else 1170
    auxiliary_bits = 13412 if cell.compute_tokens == 24 else 17182
    return {
        "descriptorUmtStateAllocatedStoreBytes": 4608,
        "descriptorUmtStatePhysicalStoreBytes": 5120,
        "descriptorUmtStateResidualStoreBytes": 512,
        "descriptorUmtStateTokenLogicalBitsFloor": token_bits,
        "descriptorUmtStateFunctionalControlLogicalBitsFloor": (
            functional_bits
        ),
        "descriptorUmtStateBankSchedulerLogicalBitsFloor": 283,
        "descriptorUmtStateInstrumentationLogicalBitsFloor": (
            instrumentation_bits
        ),
        "descriptorUmtStateAuxiliaryLogicalBitsFloor": auxiliary_bits,
        "descriptorUmtStatePhysicalStorePlusLogicalAuxiliaryBitsFloor": (
            40960 + auxiliary_bits
        ),
        "descriptorUmtStateFpIssueWidth": cell.fp_issue_width,
        "descriptorUmtStateFpIssueSelectionCandidateInputs": (
            cell.compute_tokens * cell.fp_issue_width
        ),
        "descriptorUmtStateFpIssueOperandRouteBits": (
            64 * cell.fp_issue_width
        ),
        "descriptorUmtStateIncrementalFpIssueSelectionCandidateInputs": (
            cell.compute_tokens * (cell.fp_issue_width - 1)
        ),
        "descriptorUmtStateIncrementalFpIssueOperandRouteBits": (
            64 * (cell.fp_issue_width - 1)
        ),
    }


def validate_dual_issue(stats, cell, require_exercised):
    observed = stats.get("descriptorUmtStateDualIssueCycles")
    if type(observed) is not int or observed < 0:
        raise RuntimeError(
            "UMT dual-issue counter is absent, noninteger, or negative: "
            f"{observed}"
        )
    if cell.fp_issue_width == 1 and observed != 0:
        raise RuntimeError(f"W1 cell reported dual issue: {observed}")
    if cell.fp_issue_width == 2 and require_exercised and observed <= 0:
        raise RuntimeError(
            f"W2 evidence did not exercise dual issue: {observed}"
        )
    return {
        "expected": (
            "exactly_zero"
            if cell.fp_issue_width == 1
            else "positive"
            if require_exercised
            else "nonnegative"
        ),
        "observed": observed,
    }


def validate_unique_cycle_counters(stats, cell, require_exercised):
    active_name = "descriptorUmtBatchCycles"
    active_cycles = stats.get(active_name)
    fp_operations = stats.get("descriptorUmtStateFpOperationsIssued")
    fp_stall_cycles = stats.get("descriptorUmtStateFpIssueStallCycles")
    if type(active_cycles) is not int or active_cycles < 0:
        raise RuntimeError(
            "UMT pipeline-active cycle counter is absent, noninteger, or "
            f"negative: {active_name}={active_cycles}"
        )
    if type(fp_operations) is not int or fp_operations < 0:
        raise RuntimeError(
            "UMT issued-operation counter is absent, noninteger, or "
            f"negative: {fp_operations}"
        )
    if type(fp_stall_cycles) is not int or fp_stall_cycles < 0:
        raise RuntimeError(
            "UMT zero-issue stall counter is absent, noninteger, or "
            f"negative: {fp_stall_cycles}"
        )

    dual_issue = validate_dual_issue(stats, cell, require_exercised)
    dual_cycles = dual_issue["observed"]
    if dual_cycles > active_cycles or 2 * dual_cycles > fp_operations:
        raise RuntimeError(
            "UMT dual-issue cycles exceed unique-cycle or issued-operation "
            f"bounds: dual={dual_cycles}, active={active_cycles}, "
            f"fp_operations={fp_operations}"
        )
    minimum_issue_cycles = fp_operations - dual_cycles
    if minimum_issue_cycles + fp_stall_cycles > active_cycles:
        raise RuntimeError(
            "UMT issue-cycle ledger exceeds pipeline-active cycles: "
            f"minimum_issue_cycles={minimum_issue_cycles}, "
            f"zero_issue_stalls={fp_stall_cycles}, "
            f"active={active_cycles}, fp_operations={fp_operations}, "
            f"dual={dual_cycles}"
        )

    names = {
        "bank": "descriptorUmtStateBankReadConflictCycles",
        "writeback": "descriptorUmtStateWritebackStallCycles",
        "combined": "descriptorUmtStatePipelineResultBankStallCycles",
        "divider": "descriptorUmtStateDividerNoLaneCycles",
    }
    observed = {}
    for label, name in names.items():
        value = stats.get(name)
        if type(value) is not int or value < 0:
            raise RuntimeError(
                "UMT unique-cycle counter is absent, noninteger, or "
                f"negative: {name}={value}"
            )
        if value > active_cycles:
            raise RuntimeError(
                "UMT unique-cycle counter exceeds pipeline-active cycles: "
                f"{name}={value}, active={active_cycles}"
            )
        observed[label] = value

    if (
        observed["bank"] > observed["combined"]
        or observed["writeback"] > observed["combined"]
        or observed["combined"] > observed["bank"] + observed["writeback"]
    ):
        raise RuntimeError(
            "UMT split result-bank accounting did not close: "
            f"bank={observed['bank']}, writeback={observed['writeback']}, "
            f"combined={observed['combined']}"
        )
    return {
        active_name: active_cycles,
        "descriptorUmtStateFpOperationsIssued": fp_operations,
        "descriptorUmtStateFpIssueStallCycles": fp_stall_cycles,
        "minimumFpIssueCycles": minimum_issue_cycles,
        "dual_issue": dual_issue,
        **{name: observed[label] for label, name in names.items()},
    }
