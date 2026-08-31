#!/usr/bin/env python3
"""Fail-closed v16 consumer of build-v19 and arm-v7 evidence contracts.

This program only freezes, validates, and records launch commands.  It never
builds, invokes systemd, or executes gem5.  A future launcher must first
produce the separately validated, canonical-source build proof.
"""

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shlex
import stat
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
TRACE_BUILD_DEFINE = "LANL_MAA_UMT_INGRESS_TRACE_TEST"
CONFORMANCE_BUILD_DEFINE = "LANL_MAA_UMT_PKI4_CONFORMANCE_TEST"
LABEL_PREFIX = "lanl_maa_umt_ingress_micro"
SCHEMA_BUILD_PROOF = "lanl-maa-umt-pki4-dual-gem5-build-proof-v19"
SCHEMA_SUBMISSION = "umt-lanl-maa-submission-v1"
SCHEMA_CONTRACT = "lanl-maa-umt-ingress-contract-v16"
SCHEMA_DISPATCH_PLAN = "lanl-maa-umt-ingress-dispatch-plan-v16"
SCHEMA_ARM_REPORT = "lanl-maa-umt-ingress-arm-report-v16"
CONTRACT_FILENAME = "ingress-contract-v16.json"
DISPATCH_FILENAME = "ingress-dry-dispatch-v16.json"
CANONICAL_SOURCE_ROOT = (
    "/data1/nier/worktrees/DX100-umt-pki4-conformance-source-v3-20260831"
)
CANONICAL_SOURCE = pathlib.Path(CANONICAL_SOURCE_ROOT)
CANONICAL_SOURCE_COMMIT = "45e8e848ff6e1cd2be7901a32d58a93d7109b668"
CANONICAL_SOURCE_TREE = "0d937910257d088b87303a3ade6642442f9faf22"
CANONICAL_GEM5 = CANONICAL_SOURCE / "build/X86_UMT_T32_W2/gem5.opt"
BUILD_ROOT = CANONICAL_SOURCE / "build/X86_UMT_T32_W2"
GENERATED_ROOT_SENTINEL = BUILD_ROOT / ".lanl-maa-umt-build-owner-v19.json"
GENERATED_ROOT_OWNERSHIP_SCHEMA = "lanl-maa-umt-pki4-generated-root-owner-v19"
BUILD_UNIT = "umt-pki4-conformance-build-v19-20260831.service"
BUILD_EVIDENCE_NAME = "pki4-conformance-build-evidence-v19"
BUILD_PLAN_SCHEMA = "lanl-maa-umt-pki4-dual-build-plan-v19"
BUILD_ATTESTATION_SCHEMA = "lanl-maa-umt-pki4-dual-build-attestation-v19"
BUILD_CAMPAIGN_ROOT = pathlib.Path(
    "/data1/nier/dx100-runs/" "2026-08-31-umt-pki4-conformance-build-v19-live"
)
BUILD_PROOF_PATH = (
    BUILD_CAMPAIGN_ROOT / "identity" / "pki4-conformance-build-proof-v19.json"
)
BUILD_CLEAN_METHOD = "require-fresh-absent-exact-two-v1"
BUILD_OBJECT = CANONICAL_SOURCE / "build/X86_UMT_T32_W2/mem/LANLMAA/lanl_maa.o"
CONFIG_ARTIFACTS = {
    "config_compute_tokens": (
        CANONICAL_SOURCE
        / "build/X86_UMT_T32_W2/config/lanl_maa_umt_compute_tokens.hh",
        b"#define LANL_MAA_UMT_COMPUTE_TOKENS 32\n",
    ),
    "config_fp_issue_width": (
        CANONICAL_SOURCE
        / "build/X86_UMT_T32_W2/config/lanl_maa_umt_fp_issue_width.hh",
        b"#define LANL_MAA_UMT_FP_ISSUE_WIDTH 2\n",
    ),
}
EXPECTED_BUILD_ARGV = (
    "/usr/bin/scons",
    "--ignore-style",
    "build/X86_UMT_T32_W2/gem5.opt",
    "-j4",
)
BUILD_ARGV = EXPECTED_BUILD_ARGV
TRACE_DEFINE_FLAGS = (
    "-DLANL_MAA_UMT_INGRESS_TRACE_TEST",
    "-DLANL_MAA_UMT_PKI4_CONFORMANCE_TEST",
)
TRACE_DEFINE_VALUE = " ".join(TRACE_DEFINE_FLAGS)
OBJECT_PREBUILD_ARGV = (
    "/usr/bin/scons",
    "--ignore-style",
    "--verbose",
    "build/X86_UMT_T32_W2/mem/LANLMAA/lanl_maa.o",
    "-j1",
)
SANITIZED_CHILD_ENV_NAMES = ["CCFLAGS_EXTRA", "LANG", "LC_ALL", "PATH", "TZ"]
# Fixture template only.  Live proofs may record nonempty inherited names, but
# never their values; `validate_build_environment` checks that shape.
BUILD_ENVIRONMENT = {
    "sanitized": SANITIZED_CHILD_ENV_NAMES,
    "fixed_values": {"CCFLAGS_EXTRA": TRACE_DEFINE_VALUE},
    "inherited_tool_affecting_names": [],
    "inherited_tool_affecting_count": 0,
}
BUILD_WRAPPER = ROOT / "tests/lanl_maa/run_umt_ingress_build_attestation.py"
PROOF_BUILD_WRAPPER = pathlib.Path(
    "/data1/nier/worktrees/DX100-umt-pki4-live-build-harness-v19-20260831/"
    "tests/lanl_maa/run_umt_ingress_build_attestation.py"
)
PROOF_BUILD_WRAPPER_SHA256 = (
    "79ca2e97e2920881f1873bd1be8823ed669505489bfe0b756ab383a4efc9603e"
)
ARM_WRAPPER = ROOT / "tests/lanl_maa/run_umt_ingress_micro_arm.py"
ARM_WRAPPER_SHA256 = (
    "2d5e64568062ce391677f37cbf089b755729e174d8342c7688eeb22997f01faa"
)
ARM_LAUNCH_SCHEMA = "lanl-maa-umt-ingress-arm-launch-v7"
ARM_OWNERSHIP_SCHEMA = "lanl-maa-umt-ingress-output-ownership-v7"
ARM_TERMINAL_SCHEMA = "lanl-maa-umt-ingress-arm-terminal-v7"
GUEST_COMPATIBILITY_PREFIX = (
    "LANG=C",
    "LC_ALL=C",
    "OMP_NUM_THREADS=1",
    "LD_HWCAP_MASK=0",
    "GLIBC_TUNABLES=glibc.cpu.hwcaps=-SSE4_2,-AVX,-AVX2,-AVX512F,-AVX512VL",
    "OMPI_MCA_btl=self",
    "OMPI_MCA_pml=ob1",
    "OMPI_MCA_shmem=mmap",
    "OMPI_MCA_shmem_mmap_backing_file_base_dir=/tmp",
)
ARM_EVIDENCE_DIRECTORY = ".service-owned"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
ARM_CSV_HEADER = (
    b"# mpi ranks, Mem for PSI (kb), process rss mem (kb), "
    b"# solver unknowns (extents of PSI), total # flux iterations, "
    b"# time steps, walltime(seconds),energy check, "
    b"energy in radiation field, maximum electron temperature, "
    b"maximum radiation temperature, incident power, escaping power, "
    b"power absorbed, power emitted\n"
)
ARM_CSV_HEADER_SHA256 = hashlib.sha256(ARM_CSV_HEADER).hexdigest()
SYSTEMD_SHOW_PROPERTIES = (
    "Id",
    "InvocationID",
    "MainPID",
    "ExecMainPID",
    "ExecMainStartTimestampMonotonic",
    "WorkingDirectory",
    "CPUQuotaPerSecUSec",
    "CPUWeight",
    "MemoryHigh",
    "MemoryMax",
    "MemorySwapMax",
    "RuntimeMaxUSec",
    "ExecStart",
    "Environment",
    "ExecMainCode",
    "ExecMainStatus",
    "Result",
)
BUILD_SHOW_COMMAND = (
    "systemctl",
    "--user",
    "show",
    "--all",
    "--property=" + ",".join(SYSTEMD_SHOW_PROPERTIES),
    BUILD_UNIT,
)
BUILD_JOURNAL_COMMAND = (
    "journalctl",
    "--user",
    f"--unit={BUILD_UNIT}",
    "--no-pager",
    "--output=export",
)
JOURNAL_TERMINAL_PROTOCOL = "LANL_MAA_UMT_INGRESS_BUILD_ATTESTATION_V19"
DISPATCH_PROPERTIES = (
    ("CPUQuota", "400%"),
    ("CPUWeight", "1000"),
    ("MemoryHigh", str(14 * 1024**3)),
    ("MemoryMax", str(16 * 1024**3)),
    ("MemorySwapMax", "0"),
    ("RuntimeMaxSec", "4h"),
)
BUILD_DISPATCH_PROPERTIES = DISPATCH_PROPERTIES
BUILD_CLEANUP_COMMANDS = (
    ("systemctl", "--user", "stop", BUILD_UNIT),
    ("systemctl", "--user", "reset-failed", BUILD_UNIT),
)
BUILD_CLEANUP_RECEIPT_SCHEMA = "lanl-maa-umt-pki4-build-cleanup-v19"
BUILD_CLEANUP_SHOW_COMMAND = (
    "systemctl",
    "--user",
    "show",
    "--all",
    "--property=LoadState,ActiveState,SubState",
    BUILD_UNIT,
)
ADAPTIVE_NATIVE = pathlib.Path(
    "/data1/nier/dx100-runs/2026-08-09-umt-adaptive-streamed-successor-v2/"
    "identity/test_driver"
)
ADAPTIVE_NATIVE_SHA256 = (
    "7db125ac6d0846c50f98042e8b42696db81c7b1f89ae4ed88b7e341bb0873f2c"
)
ADAPTIVE_NATIVE_CWD = pathlib.Path(
    "/data1/nier/worktrees/umt-lanl-maa-adaptive-d32-d64-20260808"
)
ADAPTIVE_NATIVE_COMMIT = "4f5fc27952be563f43272bd61f46981238aff165"
ADAPTIVE_NATIVE_TREE = "57b2183c099b3c4a9140bcfa82681c58aaca0fd7"
ADAPTIVE_COOKIE = "umt-lanl-maa-opcode11-wave-soa-arena-adaptive-v1"
NATIVE_ABI_SOURCES = {
    "src/teton/snac/LanlMaaUmtSubmit.cc": "02b8a48ddebbb908879020ef627cc9b751041e5eaeb49f41557295e772afb423",
    "tests/lanl_maa/umt64_native_contract_test.cc": "a8b7c9d9004942d50e39f24edda06f1a19821c77e00b9397412d12eb05225384",
    "tests/lanl_maa/test_umt64_native_source.py": "7412b1aa861bbc68eabe7d083eb6cb6c69a679b6c49523893a29d052a03e02eb",
}
LEGACY_INSTRUMENTATION_SOURCES = {
    "src/base/cprintf.cc": "54e30cca948b267c8384b6c9f2e4d674c7cd79e1e54062d7223805aedb41bf72",
    "src/base/cprintf.hh": "3249e5f3f3b2de0ad5b5c92c75bb45dafb3f605a93ea814d7eba8c45be0fad0a",
    "src/base/cprintf_formats.hh": "c44eaae91d027e0b8cf9c083a15927867fd9be49d8fa4c5375ecb3d130839ae5",
    "src/mem/LANLMAA/UmtOrderedWaveIngressTrace.hh": "b6d3179f58e623c13b3b6afd7174c359085bddc4393d99702df81cf3ab5584bd",
    "src/mem/LANLMAA/UmtOrderedWaveStreamState.hh": "731e170b9ea29d34ad478381eac1d04fec8fc72948d4c16d274e17194f738ec5",
    "src/mem/LANLMAA/lanl_maa.hh": "a712bbdb786d5019d97cd0e9e98fec5b707a0293e38aa0b07a42b077f8fabb1c",
    "src/mem/LANLMAA/lanl_maa.cc": "9b58fe8bf2ceaf0d21dee3fc1d531c711de75f9ffb170072f4c4f490615fb39d",
    "tests/lanl_maa/umt_ingress_default_off_compile_test.cc": "7d3076bf4f8033e3dc11f54ef94bdcdc756469e816a0bf7425705d55122064c2",
    "tests/lanl_maa/umt_production_ingress_trace_test.cc": "07a8bdd412cba3d8e7afb4e86bceec4ad5765cb2e1c24a2e6f754436e4032e32",
    "tests/lanl_maa/run_umt_production_ingress_trace_gate.py": "67cd70ac8d057d5769b7e8e3f0a9e3dd42e05f01b9c432250b1edba0078bea28",
}
CONFORMANCE_INSTRUMENTATION_SOURCES = {
    "src/mem/LANLMAA/UmtPki4ConformanceTrace.hh": "800a85c1415b807e804baa41626e1d6ec657b483639cdf95517799d5595d97c7",
    "src/mem/LANLMAA/UmtOrderedWaveStreamState.hh": "731e170b9ea29d34ad478381eac1d04fec8fc72948d4c16d274e17194f738ec5",
    "src/mem/LANLMAA/lanl_maa.hh": "a712bbdb786d5019d97cd0e9e98fec5b707a0293e38aa0b07a42b077f8fabb1c",
    "src/mem/LANLMAA/lanl_maa.cc": "9b58fe8bf2ceaf0d21dee3fc1d531c711de75f9ffb170072f4c4f490615fb39d",
    "tests/lanl_maa/umt_pki4_conformance_model_test.cc": "80c2849ab5debe27afa9dc1abc4aa8304a768569e5397ecd22fd68b933708af1",
    "tests/lanl_maa/umt_pki4_conformance_normalizer.py": "de2c140c638884aa876756c81be3de832ac14ccb938ee863a69f84a006146fb7",
    "tests/lanl_maa/test_umt_pki4_conformance_normalizer.py": "927f438687a00abc401a98c52629e8d4a47f29b1514ca7b7431e455432ffee4e",
    "tests/lanl_maa/run_umt_pki4_conformance_gate.py": "3f342bccb058d271895427d51ee84fdfe823bf12ba5592c6cf50d62f1f4e0394",
}
INSTRUMENTATION_SOURCES = {
    **LEGACY_INSTRUMENTATION_SOURCES,
    **CONFORMANCE_INSTRUMENTATION_SOURCES,
}
LEGACY_COMPILED_MARKERS = (
    b"UMT_INGRESS kind=",
    b"d64_hold cycle=",
    b"waiters=%u token=%llu pre=",
)
COMPILED_MARKERS = LEGACY_COMPILED_MARKERS + (
    b"UMT_PKI4_CONFORMANCE ",
    b"lanl-maa-umt-pki4-conformance-v3",
    b"7ff5188835462202586fa44a3b0272e9c298aca745293abfae8354cc0988a15d",
)
CONFORMANCE_REPORT = pathlib.Path(
    "/data1/nier/dx100-runs/2026-08-31-umt-pki4-conformance-gate-v3/"
    "umt-pki4-conformance-host-gate-v3.json"
)
CONFORMANCE_REPORT_SHA256 = (
    "562fde3216e45521e1b289e885ef035983e299003dfb388cb7cf7ecf2461775b"
)
TEMPORAL_PLAN = pathlib.Path(
    "/data1/nier/dx100-runs/2026-08-31-umt-pki4-conformance-gate-v3/"
    "pki4-temporal-equivalence-plan-v2.json"
)
TEMPORAL_PLAN_SHA256 = (
    "7ff5188835462202586fa44a3b0272e9c298aca745293abfae8354cc0988a15d"
)
PROMOTION_REVIEW = pathlib.Path(
    "/data1/nier/dx100-runs/2026-08-31-umt-pki4-conformance-gate-v3/"
    "pki4-conformance-independent-promotion-review-v3.json"
)
PROMOTION_REVIEW_SHA256 = (
    "6fd0f46bb2e278e01bfc92502eb1aa4c93f24cf9f30633087475688e9efb5bc4"
)
BUILD_SYSTEM_SOURCES = {
    "SConstruct": "566ccd8621b168e9ef29c04f5bf5ba5414190afbb32bfcac4986843e3f476f19",
    "site_scons/gem5_scons/defaults.py": (
        "b10bb7b6aef8b6716a30af1560e8d8e55fae9cdb696cb4ccede7ba5d3a19ed25"
    ),
}
ABI_CONTRACTS = {
    "D32": {"version": 4, "max_groups": 32},
    "D64": {"version": 5, "max_groups": 64},
}
CASES = {
    "d32-g16": {"abi": "D32", "groups": 16, "mode": "wave_d32"},
    "d32-g31": {"abi": "D32", "groups": 31, "mode": "wave_d32"},
    "d32-g32": {"abi": "D32", "groups": 32, "mode": "wave_d32"},
    "d64-g31": {"abi": "D64", "groups": 31, "mode": "wave_d64"},
    "d64-g32": {"abi": "D64", "groups": 32, "mode": "wave_d64"},
}
RESOURCE_POLICY = {
    "CPUQuotaPerSecUSec": "4s",
    "CPUWeight": "1000",
    "MemoryHigh": str(14 * 1024**3),
    "MemoryMax": str(16 * 1024**3),
    "MemorySwapMax": "0",
    "RuntimeMaxUSec": "4h",
}
CONTRACT_FIELDS = frozenset(
    (
        "schema",
        "status",
        "campaign_root",
        "harness_source_root",
        "harness_source_commit",
        "harness_source_tree",
        "harness_reviewed_file_sha256",
        "gem5",
        "gem5_sha256",
        "instrumented_build_proof",
        "instrumented_build_proof_schema",
        "instrumented_build_proof_sha256",
        "required_define",
        "guest_compatibility_environment",
        "native_identity",
        "cases",
        "arms",
        "resource_policy",
        "predecessors",
        "claim_boundary",
    )
)
RAW_FILES = (
    "gem5.stdout",
    "gem5.stderr",
    "app.stdout",
    "app.stderr",
    "debug.log",
    "m5out/stats.txt",
    "m5out/config.ini",
    "m5out/config.json",
    "submission.json",
)
ARM_EVIDENCE_FILES = (
    f"{ARM_EVIDENCE_DIRECTORY}/arm-launch.json",
    f"{ARM_EVIDENCE_DIRECTORY}/arm-output-ownership.json",
    f"{ARM_EVIDENCE_DIRECTORY}/arm-terminal.json",
)
HARNESS_REVIEWED_FILES = (
    "docs/plans/umt_ingress_micro_harness_20260830.md",
    "docs/plans/umt_pki4_live_trace_campaign_20260831.md",
    "tests/lanl_maa/normalize_umt_pki4_live_trace.py",
    "tests/lanl_maa/run_umt_ingress_build_attestation.py",
    "tests/lanl_maa/run_umt_ingress_micro_arm.py",
    "tests/lanl_maa/test_umt_ingress_micro_harness.py",
    "tests/lanl_maa/test_umt_pki4_live_trace_harness.py",
    "tests/lanl_maa/umt_ingress_micro_harness.py",
    "tests/lanl_maa/umt_ingress_micro_process_cpu.py",
)
WORK_COUNTERS = (
    "descriptorDoorbells",
    "descriptorFetches",
    "descriptorCompletionWrites",
    "descriptorUmtD32Descriptors",
    "descriptorUmtD64Descriptors",
    "descriptorUmtGroupsLoaded",
    "descriptorUmtInputReads",
    "descriptorUmtStateInputWrites",
    "descriptorUmtStateDenominatorsConsumed",
    "descriptorUmtStateResultWrites",
    "descriptorUmtResultsComputed",
)
SUBMISSION_FIELDS = frozenset(
    (
        "schema",
        "opcode",
        "mode",
        "corner_calls",
        "wave_calls",
        "wave_corners",
        "direct_arena_submissions",
        "direct_sink_result_words",
        "direct_sink_phi_words",
        "wave_descriptor_sum_area_words",
        "wave_soa_arena_descriptors",
        "wave_d32_descriptors",
        "wave_d32_groups",
        "wave_d64_descriptors",
        "wave_d64_groups",
        "wave_d32_decisions",
        "wave_d64_decisions",
        "adaptive_wave_selector_threshold_groups",
        "wave_record_copy_bytes",
        "wave_result_clear_bytes",
        "wave_result_copy_bytes",
        "descriptor_submissions",
        "submitted_groups",
        "capability_probes",
        "scalar_direct_corners",
        "scalar_direct_groups",
        "scalar_ordered_fallback_corners",
        "scalar_ordered_fallback_groups",
        "post_completion_result_read_words",
        "last_error",
        "all_completions_valid",
        "ordered_corner_scalar_solves_replaced",
    )
)
SOURCE_RE = re.compile(
    r"^UMT_INGRESS kind=(source|denominator) cycle=(\d+) callback=(\d+) lane=(\d+) packet=(0x[0-9a-f]+) line=(0x[0-9a-f]+) abi=(\d+) stage=(\d+) group=(\d+) corner=(\d+) order=(\d+) waiters=(\d+) token=(\d+) pre=(0x[0-9a-f]+) post=(0x[0-9a-f]+) next_engine_tick=(\d+)$"
)
LINE_RE = re.compile(
    r"^UMT_INGRESS kind=(d32|d64)_(hold|release) cycle=(\d+) line=(0x[0-9a-f]+) abi=(\d+) stage=(\d+) group=(\d+) corner=(\d+) waiters=(\d+) pre=(0x[0-9a-f]+) post=(0x[0-9a-f]+)$"
)


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_sha256(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read_json(path):
    with pathlib.Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def exact_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise RuntimeError(f"{label} has an invalid exact schema")


def validate_build_environment(value, label):
    exact_keys(
        value,
        (
            "sanitized",
            "fixed_values",
            "inherited_tool_affecting_names",
            "inherited_tool_affecting_count",
        ),
        label,
    )
    names = value["inherited_tool_affecting_names"]
    if (
        value["sanitized"] != SANITIZED_CHILD_ENV_NAMES
        or value["fixed_values"] != {"CCFLAGS_EXTRA": TRACE_DEFINE_VALUE}
        or not isinstance(names, list)
        or names != sorted(set(names))
        or any(not isinstance(name, str) or not name for name in names)
        or value["inherited_tool_affecting_count"] != len(names)
    ):
        raise RuntimeError(
            f"{label} has an invalid sanitized environment record"
        )


def atomic_no_clobber(path, value):
    """Atomically publish JSON without replacing any concurrent winner."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link publication is atomic and fails with EEXIST instead of
        # replacing an adversary's concurrently published target.
        os.link(temporary, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def verify_hash(path, expected, label):
    path = pathlib.Path(path).resolve()
    if (
        not path.is_file()
        or not isinstance(expected, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected)
    ):
        raise RuntimeError(f"invalid {label} identity")
    if sha256(path) != expected:
        raise RuntimeError(f"{label} SHA-256 mismatch")
    return path


def expected_clean_stdout():
    """Return the exact build-v19 producer record for the fresh build root."""
    relatives = tuple(
        str(path.relative_to(CANONICAL_SOURCE))
        for path in (BUILD_ROOT, CANONICAL_GEM5, BUILD_OBJECT)
    )
    return (
        "clean_method="
        + BUILD_CLEAN_METHOD
        + "\ninitial_absent="
        + ",".join(relatives)
        + "\nstatus=0/SUCCESS\n"
    )


def validate_clean_stdout(path):
    """Require the ordered root, target, and object tuple emitted by v19."""
    if (
        pathlib.Path(path).read_text(encoding="ascii")
        != expected_clean_stdout()
    ):
        raise RuntimeError("clean stdout initial-absence tuple mismatch")


def git_output(cwd, *argv):
    return subprocess.check_output(["git", *argv], cwd=cwd, text=True).strip()


def verify_harness_identity(
    expected=None, *, root=ROOT, reviewed_files=HARNESS_REVIEWED_FILES
):
    root = pathlib.Path(root).resolve()
    if not root.is_dir() or git_output(
        root, "status", "--porcelain=v1", "--untracked-files=all"
    ):
        raise RuntimeError("harness source worktree is not clean")
    hashes = {}
    for relative in reviewed_files:
        path = (root / relative).resolve()
        try:
            tracked = git_output(
                root, "ls-files", "--error-unmatch", "--", relative
            )
        except subprocess.CalledProcessError as error:
            raise RuntimeError(
                f"reviewed harness file is not tracked: {relative}"
            ) from error
        if (
            tracked != relative
            or root not in path.parents
            or not path.is_file()
        ):
            raise RuntimeError(
                f"reviewed harness file path is invalid: {relative}"
            )
        hashes[relative] = sha256(path)
    identity = {
        "source_root": str(root),
        "source_commit": git_output(root, "rev-parse", "HEAD"),
        "source_tree": git_output(root, "rev-parse", "HEAD^{tree}"),
        "reviewed_file_sha256": hashes,
    }
    if expected is not None:
        exact_keys(
            expected,
            (
                "source_root",
                "source_commit",
                "source_tree",
                "reviewed_file_sha256",
            ),
            "harness source identity",
        )
        if expected != identity:
            raise RuntimeError(
                "harness commit/tree/reviewed-file identity mismatch"
            )
    return identity


def contract_harness_identity(contract, allow_external_producer=False):
    identity = {
        "source_root": contract["harness_source_root"],
        "source_commit": contract["harness_source_commit"],
        "source_tree": contract["harness_source_tree"],
        "reviewed_file_sha256": contract["harness_reviewed_file_sha256"],
    }
    producer_root = (
        pathlib.Path(identity["source_root"]).resolve()
        if allow_external_producer
        else ROOT
    )
    verify_harness_identity(
        identity,
        root=producer_root,
        reviewed_files=tuple(identity["reviewed_file_sha256"]),
    )
    if contract["guest_compatibility_environment"] != list(
        GUEST_COMPATIBILITY_PREFIX
    ):
        raise RuntimeError("contract guest compatibility environment mismatch")
    verify_guest_compatibility_source(root=producer_root)
    return identity


def frozen_producer_expected_contract(contract, campaign):
    producer_root = pathlib.Path(contract["harness_source_root"]).resolve()
    relative = "tests/lanl_maa/umt_ingress_micro_harness.py"
    harness_path = producer_root / relative
    expected_hash = contract["harness_reviewed_file_sha256"].get(relative)
    verify_hash(harness_path, expected_hash, "frozen producer harness")
    spec = importlib.util.spec_from_file_location(
        "umt_ingress_frozen_contract_producer", harness_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("frozen producer harness cannot be loaded")
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)
    return producer.expected_contract(
        campaign,
        pathlib.Path(contract["instrumented_build_proof"]),
        contract["instrumented_build_proof_sha256"],
        contract["gem5_sha256"],
    )


def verify_guest_compatibility_source(root=ROOT):
    runner = (
        pathlib.Path(root).resolve()
        / "tests/lanl_maa/umt_ingress_micro_process_cpu.py"
    )
    tree = ast.parse(runner.read_text(encoding="utf-8"))
    assignments = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "q"
            and target.attr == "env"
        ):
            assignments.append(node.value)
    if len(assignments) != 1 or not isinstance(assignments[0], ast.List):
        raise RuntimeError("ingress guest environment assignment is not exact")
    values = [
        item.value if isinstance(item, ast.Constant) else None
        for item in assignments[0].elts
    ]
    expected_prefix = list(GUEST_COMPATIBILITY_PREFIX)
    compatibility_keys = {value.split("=", 1)[0] for value in expected_prefix}
    compatibility = [
        value
        for value in values
        if isinstance(value, str)
        and value.split("=", 1)[0] in compatibility_keys
    ]
    if (
        values[: len(expected_prefix)] != expected_prefix
        or compatibility != expected_prefix
    ):
        raise RuntimeError("ingress guest compatibility environment mismatch")
    return expected_prefix


def validate_build_argv(argv):
    argv = tuple(argv)
    if argv != EXPECTED_BUILD_ARGV or any(
        "=" in item or "CPPDEFINES" in item for item in argv
    ):
        raise RuntimeError("SCons build argv must be assignment-free")


def validate_fixed_build_environment(value):
    if value != {"CCFLAGS_EXTRA": TRACE_DEFINE_VALUE}:
        raise RuntimeError("fixed instrumentation environment is not exact")


def validate_object_compile_transcript(raw):
    candidates = [
        re.sub(rb"\x1b\[[0-9;]*m", b"", line)
        for line in raw.splitlines()
        if b"src/mem/LANLMAA/lanl_maa.cc" in line
        and b"X86_UMT_T32_W2/mem/LANLMAA/lanl_maa.o" in line
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "object prebuild transcript lacks one exact MAA command"
        )
    try:
        tokens = shlex.split(candidates[0].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise RuntimeError(
            "object prebuild command is not parseable"
        ) from error
    defines = [token for token in tokens if token in TRACE_DEFINE_FLAGS]
    if (
        defines != list(TRACE_DEFINE_FLAGS)
        or tokens.count(TRACE_DEFINE_FLAGS[0]) != 1
        or tokens.count(TRACE_DEFINE_FLAGS[1]) != 1
        or any(
            token.startswith("-DLANL_MAA_UMT_INGRESS_TRACE_TEST")
            and token != TRACE_DEFINE_FLAGS[0]
            for token in tokens
        )
        or any(
            token.startswith("-DLANL_MAA_UMT_PKI4_CONFORMANCE_TEST")
            and token != TRACE_DEFINE_FLAGS[1]
            for token in tokens
        )
        or any("CPPDEFINES" in token for token in tokens)
        or any(token.startswith("CCFLAGS_EXTRA=") for token in tokens)
    ):
        raise RuntimeError(
            "object prebuild command lacks the exact ordered dual defines"
        )
    return candidates[0]


def validate_build_system_contract(source, expected=None):
    source = pathlib.Path(source).resolve()
    hashes = {
        relative: sha256(source / relative)
        for relative in BUILD_SYSTEM_SOURCES
    }
    if hashes != BUILD_SYSTEM_SOURCES or (
        expected is not None and expected != BUILD_SYSTEM_SOURCES
    ):
        raise RuntimeError("build-system source hash mismatch")

    sconstruct = (source / "SConstruct").read_text(encoding="utf-8")
    if sconstruct.count("env.Append(CCFLAGS='$CCFLAGS_EXTRA')") != 1:
        raise RuntimeError("SConstruct lacks the exact CCFLAGS_EXTRA append")
    defaults = ast.parse(
        (source / "site_scons/gem5_scons/defaults.py").read_text(
            encoding="utf-8"
        )
    )
    functions = [
        node
        for node in defaults.body
        if isinstance(node, ast.FunctionDef) and node.name == "EnvDefaults"
    ]
    if len(functions) != 1:
        raise RuntimeError("defaults.py lacks exact EnvDefaults declaration")
    expected_flow = ast.dump(
        ast.parse('env[key] = env["ENV"].get(key, default)').body[0],
        include_attributes=False,
    )
    flows = [
        node
        for node in ast.walk(functions[0])
        if isinstance(node, ast.Assign)
        and ast.dump(node, include_attributes=False) == expected_flow
    ]
    if len(flows) != 1:
        raise RuntimeError("defaults.py lacks exact ENV.get assignment flow")
    use_vars, overrides = None, None
    for node in ast.walk(functions[0]):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "use_vars":
            use_vars = node.value
        elif isinstance(target, ast.Name) and target.id == "var_overrides":
            overrides = node.value
    if not isinstance(use_vars, ast.Set) or not isinstance(
        overrides, ast.Dict
    ):
        raise RuntimeError(
            "defaults.py CCFLAGS_EXTRA declarations are malformed"
        )
    declared = [
        item.value
        for item in use_vars.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]
    values = {
        key.value: value.value
        for key, value in zip(overrides.keys, overrides.values)
        if isinstance(key, ast.Constant)
        and isinstance(key.value, str)
        and isinstance(value, ast.Constant)
    }
    if (
        declared.count("CCFLAGS_EXTRA") != 1
        or values.get("CCFLAGS_EXTRA") != ""
    ):
        raise RuntimeError("defaults.py lacks declared/default CCFLAGS_EXTRA")
    return hashes


def artifact(value, label, required_path=None):
    exact_keys(value, ("path", "sha256"), label)
    path = verify_hash(value["path"], value["sha256"], label)
    if (
        required_path is not None
        and path != pathlib.Path(required_path).resolve()
    ):
        raise RuntimeError(f"{label} path is not canonical")
    return path


def evidence_artifact(value, evidence_dir, name):
    return artifact(value, f"wrapper evidence {name}", evidence_dir / name)


def parse_systemd_show(path, label):
    """Parse a raw, property-limited `systemctl show` snapshot exactly."""
    raw = pathlib.Path(path).read_text(encoding="utf-8", errors="strict")
    fields = {}
    for line in raw.splitlines():
        if not line or "=" not in line:
            raise RuntimeError(f"{label} has a malformed systemd-show line")
        key, value = line.split("=", 1)
        if key not in SYSTEMD_SHOW_PROPERTIES or key in fields:
            raise RuntimeError(f"{label} has an unexpected/duplicate property")
        fields[key] = value
    if set(fields) != set(SYSTEMD_SHOW_PROPERTIES):
        raise RuntimeError(f"{label} has incomplete systemd-show properties")
    return fields


def parse_proc_start_receipt(path, pid, invocation, show_start_usec):
    value = read_json(path)
    exact_keys(
        value,
        (
            "schema",
            "pid",
            "proc_start_ticks",
            "invocation_id",
            "exec_main_start_timestamp_monotonic",
        ),
        "live process start receipt",
    )
    if (
        value["schema"] != "lanl-maa-proc-start-receipt-v1"
        or value["pid"] != pid
        or value["invocation_id"] != invocation
        or value["exec_main_start_timestamp_monotonic"] != show_start_usec
        or not isinstance(value["proc_start_ticks"], str)
        or not re.fullmatch(r"[1-9][0-9]*", value["proc_start_ticks"])
    ):
        raise RuntimeError("live process start receipt is invalid")
    return value


def wrapper_command(evidence_dir, wrapper=BUILD_WRAPPER):
    return (
        "/usr/bin/python3",
        str(pathlib.Path(wrapper).resolve()),
        "--unit",
        BUILD_UNIT,
        "--source",
        str(CANONICAL_SOURCE),
        "--evidence-dir",
        str(pathlib.Path(evidence_dir).resolve()),
    )


def validate_systemd_resource_mapping():
    expected_show = {
        "CPUQuotaPerSecUSec": "4s",
        "CPUWeight": "1000",
        "MemoryHigh": str(14 * 1024**3),
        "MemoryMax": str(16 * 1024**3),
        "MemorySwapMax": "0",
        "RuntimeMaxUSec": "4h",
    }
    expected_launch = (
        ("CPUQuota", "400%"),
        ("CPUWeight", "1000"),
        ("MemoryHigh", str(14 * 1024**3)),
        ("MemoryMax", str(16 * 1024**3)),
        ("MemorySwapMax", "0"),
        ("RuntimeMaxSec", "4h"),
    )
    if (
        RESOURCE_POLICY != expected_show
        or DISPATCH_PROPERTIES != expected_launch
        or BUILD_DISPATCH_PROPERTIES != expected_launch
    ):
        raise RuntimeError("frozen systemd resource mapping is altered")


def build_systemd_run_command(evidence_dir, wrapper=BUILD_WRAPPER):
    """Return the exact retained v19 build-unit launch; never execute it."""
    validate_systemd_resource_mapping()
    evidence = pathlib.Path(evidence_dir).resolve()
    if evidence.name != BUILD_EVIDENCE_NAME:
        raise RuntimeError("v19 build evidence identity is not canonical")
    command = [
        "systemd-run",
        "--user",
        "--remain-after-exit",
        f"--unit={BUILD_UNIT}",
        *[
            f"--property={key}={value}"
            for key, value in BUILD_DISPATCH_PROPERTIES
        ],
        f"--working-directory={CANONICAL_SOURCE}",
        *wrapper_command(evidence, wrapper),
    ]
    if "--collect" in command:
        raise RuntimeError("v19 build unit must remain for terminal capture")
    return command


def dry_build_plan(campaign_root, output):
    """Freeze one no-clobber build command without invoking systemd/SCons."""
    validate_build_argv(BUILD_ARGV)
    validate_fixed_build_environment(BUILD_ENVIRONMENT["fixed_values"])
    verify_canonical_source()
    validate_build_system_contract(CANONICAL_SOURCE)
    conformance_provenance = validate_conformance_provenance(
        {
            "host_report": {
                "path": str(CONFORMANCE_REPORT),
                "sha256": CONFORMANCE_REPORT_SHA256,
            },
            "temporal_plan": {
                "path": str(TEMPORAL_PLAN),
                "sha256": TEMPORAL_PLAN_SHA256,
            },
            "independent_review": {
                "path": str(PROMOTION_REVIEW),
                "sha256": PROMOTION_REVIEW_SHA256,
            },
        }
    )
    campaign = pathlib.Path(campaign_root).resolve()
    output = pathlib.Path(output).resolve()
    if (
        campaign != BUILD_CAMPAIGN_ROOT
        or campaign.exists()
        or output != campaign / "build-plan-v19.json"
        or BUILD_ROOT.exists()
        or BUILD_ROOT.is_symlink()
    ):
        raise RuntimeError(
            "v19 build plan requires its one fresh campaign and build root"
        )
    evidence = campaign / "identity" / BUILD_EVIDENCE_NAME
    value = {
        "schema": BUILD_PLAN_SCHEMA,
        "status": "dry_only_not_dispatched",
        "campaign_root": str(campaign),
        "source_worktree": str(CANONICAL_SOURCE),
        "source_commit": CANONICAL_SOURCE_COMMIT,
        "source_tree": CANONICAL_SOURCE_TREE,
        "instrumentation_source_sha256": INSTRUMENTATION_SOURCES,
        "legacy_observer_source_sha256": LEGACY_INSTRUMENTATION_SOURCES,
        "conformance_source_sha256": CONFORMANCE_INSTRUMENTATION_SOURCES,
        "build_system_source_sha256": BUILD_SYSTEM_SOURCES,
        "object_prebuild_argv": list(OBJECT_PREBUILD_ARGV),
        "fixed_child_environment": {
            "CCFLAGS_EXTRA": TRACE_DEFINE_VALUE,
        },
        "required_defines": list(TRACE_DEFINE_FLAGS),
        "compiled_object_and_binary_markers": [
            marker.decode("ascii") for marker in COMPILED_MARKERS
        ],
        "conformance_provenance": conformance_provenance,
        "sanitized_child_environment_names": SANITIZED_CHILD_ENV_NAMES,
        "unit": BUILD_UNIT,
        "evidence_dir": str(evidence),
        "clean_method": BUILD_CLEAN_METHOD,
        "required_initial_absent_paths": [
            str(BUILD_ROOT),
            str(CANONICAL_GEM5),
            str(BUILD_OBJECT),
        ],
        "fresh_full_build_expected_cost": {
            "estimate_only": True,
            "cpu_cores": 4,
            "memory_max_bytes": 16 * 1024**3,
            "disk_output_bytes_range": [7 * 1024**3, 12 * 1024**3],
            "wall_time_seconds_range": [3600, 10800],
            "hard_runtime_cap_seconds": 4 * 3600,
        },
        "generated_root_ownership_policy": {
            "schema": GENERATED_ROOT_OWNERSHIP_SCHEMA,
            "sentinel": str(GENERATED_ROOT_SENTINEL),
            "success_state": "retained_in_generated_root",
            "failure_action": "remove_only_exact_sentinel_owned_root",
        },
        "build_argv": list(BUILD_ARGV),
        "resource_policy": RESOURCE_POLICY,
        "launch_command": build_systemd_run_command(evidence),
        "show_command": list(BUILD_SHOW_COMMAND),
        "journal_command": list(BUILD_JOURNAL_COMMAND),
        "cleanup_commands_after_terminal_capture": [
            list(command) for command in BUILD_CLEANUP_COMMANDS
        ],
        "cleanup_receipt": {
            "schema": BUILD_CLEANUP_RECEIPT_SCHEMA,
            "path": str(campaign / "identity/build-cleanup-v19.json"),
            "show_command": list(BUILD_CLEANUP_SHOW_COMMAND),
            "required_state": {
                "LoadState": "not-found",
                "ActiveState": "inactive",
                "SubState": "dead",
            },
            "must_follow_terminal_proof": True,
            "failure_anchor": str(evidence / "failure-restore.json"),
            "accepted_anchor_kinds": ["success_proof", "failure_restore"],
        },
        "authorization": {
            "build_count": 1,
            "unit": BUILD_UNIT,
            "campaign_root": str(BUILD_CAMPAIGN_ROOT),
            "launch_requires_independent_review": True,
            "launch_status": "not_launched",
        },
        "claim_boundary": (
            "Dry plan only. Capture the terminal 17-property snapshot and "
            "journal before the explicit cleanup commands."
        ),
    }
    atomic_no_clobber(output, value)
    return value


def record_build_cleanup_receipt(
    output,
    terminal_proof,
    terminal_proof_sha256,
    cleanup_show,
    cleanup_show_sha256,
    anchor_kind="success_proof",
):
    """Record hash-backed cleanup after either success proof or failure restore."""
    if anchor_kind not in ("success_proof", "failure_restore"):
        raise RuntimeError("cleanup anchor kind is invalid")
    terminal_proof = verify_hash(
        terminal_proof, terminal_proof_sha256, "terminal build proof"
    )
    cleanup_show = verify_hash(
        cleanup_show, cleanup_show_sha256, "post-cleanup systemd show"
    )
    fields = {}
    for line in cleanup_show.read_text(
        encoding="utf-8", errors="strict"
    ).splitlines():
        if not line or "=" not in line:
            raise RuntimeError("post-cleanup systemd show is malformed")
        key, value = line.split("=", 1)
        if (
            key not in ("LoadState", "ActiveState", "SubState")
            or key in fields
        ):
            raise RuntimeError(
                "post-cleanup systemd show has unexpected fields"
            )
        fields[key] = value
    required = {
        "LoadState": "not-found",
        "ActiveState": "inactive",
        "SubState": "dead",
    }
    if fields != required:
        raise RuntimeError(
            "post-cleanup unit state is not not-found/inactive/dead"
        )
    value = {
        "schema": BUILD_CLEANUP_RECEIPT_SCHEMA,
        "status": "cleanup_observed_after_" + anchor_kind,
        "unit": BUILD_UNIT,
        "terminal_anchor": {
            "kind": anchor_kind,
            "path": str(terminal_proof),
            "sha256": terminal_proof_sha256,
        },
        "cleanup_commands": [
            list(command) for command in BUILD_CLEANUP_COMMANDS
        ],
        "show_command": list(BUILD_CLEANUP_SHOW_COMMAND),
        "cleanup_show": {
            "path": str(cleanup_show),
            "sha256": cleanup_show_sha256,
        },
        "observed_state": fields,
        "claim_boundary": "Post-terminal lifecycle evidence only; never a proof input.",
    }
    atomic_no_clobber(output, value)
    return value


def show_argv(value, label, expected):
    # No shell is permitted.  The stable `systemctl show` representation is
    # parsed as a whole field; the nested SCons argv is attested by the
    # wrapper's own JSON rather than mistaken for the service ExecStart.
    match = re.fullmatch(
        r"\{ path=/usr/bin/python3 ; argv\[\]=(.*?) ; ignore_errors=(?:yes|no)"
        r"(?: ; .*)? \}",
        value,
    )
    if not match or tuple(match.group(1).split()) != tuple(expected):
        raise RuntimeError(f"{label} ExecStart is not the canonical wrapper")


def validate_show_snapshot(
    fields, *, phase, pid, proc_start_ticks, invocation, command
):
    if (
        fields["Id"] != BUILD_UNIT
        or not re.fullmatch(r"[0-9a-f]{32}", fields["InvocationID"])
        or fields["InvocationID"] != invocation
        or fields["WorkingDirectory"] != str(CANONICAL_SOURCE)
        or {key: fields[key] for key in RESOURCE_POLICY} != RESOURCE_POLICY
    ):
        raise RuntimeError(
            f"{phase} systemd-show identity/resource binding mismatch"
        )
    show_argv(fields["ExecStart"], phase, command)
    if fields["Environment"] != "":
        raise RuntimeError(
            f"{phase} systemd-show environment binding mismatch"
        )
    if not re.fullmatch(
        r"[1-9][0-9]*", fields["ExecMainStartTimestampMonotonic"]
    ):
        raise RuntimeError(f"{phase} systemd-show start timestamp is invalid")
    if not re.fullmatch(r"[1-9][0-9]*", proc_start_ticks):
        raise RuntimeError(f"{phase} process start receipt is invalid")
    if phase == "live":
        if (
            fields["MainPID"] != str(pid)
            or fields["ExecMainPID"] != str(pid)
            or fields["ExecMainCode"] not in ("", "0", "(null)")
            or fields["ExecMainStatus"] not in ("", "0")
            or fields["Result"] not in ("", "success")
        ):
            raise RuntimeError("live systemd-show PID/state binding mismatch")
    elif (
        fields["MainPID"] not in ("0", str(pid))
        or fields["ExecMainPID"] != str(pid)
        # systemd's service-result enum is CLD_EXITED (numeric 1) in
        # `systemctl show`; do not infer success from prose in a journal.
        or fields["ExecMainCode"] != "1"
        or fields["ExecMainStatus"] != "0"
        or fields["Result"] != "success"
    ):
        raise RuntimeError("terminal systemd-show PID/status binding mismatch")


def parse_export_journal_bytes(raw):
    """Parse journal's binary-safe export wire format without text decoding."""
    records, position, length = [], 0, len(raw)
    while position < length:
        fields = {}
        while True:
            end = raw.find(b"\n", position)
            if end < 0:
                raise RuntimeError("journal export has an unterminated field")
            line, position = raw[position:end], end + 1
            if not line:
                break
            if b"=" in line:
                key, value = line.split(b"=", 1)
            else:
                key = line
                if position + 8 > length:
                    raise RuntimeError(
                        "journal export binary field lacks length"
                    )
                size = int.from_bytes(raw[position : position + 8], "little")
                position += 8
                if (
                    position + size >= length
                    or raw[position + size : position + size + 1] != b"\n"
                ):
                    raise RuntimeError(
                        "journal export binary field has invalid length"
                    )
                value, position = (
                    raw[position : position + size],
                    position + size + 1,
                )
            try:
                key = key.decode("ascii")
            except UnicodeDecodeError as error:
                raise RuntimeError(
                    "journal export has a non-ASCII field name"
                ) from error
            if not key or key in fields:
                raise RuntimeError(
                    "journal export has a duplicate/empty field"
                )
            fields[key] = value
        if fields:
            records.append(fields)
    if not records:
        raise RuntimeError("journal export is empty")
    return records


def journal_marker(
    kind, *, invocation, pid, proc_start_ticks, target_sha256=None
):
    value = {
        "schema": BUILD_ATTESTATION_SCHEMA,
        "unit": BUILD_UNIT,
        "invocation_id": invocation,
        "wrapper_pid": pid,
        "wrapper_proc_start_ticks": proc_start_ticks,
    }
    if target_sha256 is not None:
        value["target_sha256"] = target_sha256
    return (
        JOURNAL_TERMINAL_PROTOCOL
        + " "
        + kind
        + " "
        + json.dumps(value, sort_keys=True, separators=(",", ":"))
    ).encode()


def parse_export_journal(
    path, invocation, pid, proc_start_ticks, target_sha256
):
    start = journal_marker(
        "START",
        invocation=invocation,
        pid=pid,
        proc_start_ticks=proc_start_ticks,
    )
    success = journal_marker(
        "SUCCESS",
        invocation=invocation,
        pid=pid,
        proc_start_ticks=proc_start_ticks,
        target_sha256=target_sha256,
    )
    events = []
    for ordinal, record in enumerate(
        parse_export_journal_bytes(pathlib.Path(path).read_bytes())
    ):
        service_pair = (
            record.get("_SYSTEMD_USER_UNIT"),
            record.get("_SYSTEMD_INVOCATION_ID"),
        )
        expected = (BUILD_UNIT.encode(), invocation.encode())
        message = record.get("MESSAGE", b"")
        if JOURNAL_TERMINAL_PROTOCOL.encode() in message:
            if (
                service_pair != expected
                or record.get("_PID") != str(pid).encode()
            ):
                raise RuntimeError(
                    "journal terminal marker is not emitted by the service wrapper"
                )
            if message == start:
                events.append((ordinal, "start"))
            elif message == success:
                events.append((ordinal, "success"))
            else:
                raise RuntimeError(
                    "journal terminal protocol marker is forged, failed, or noncanonical"
                )
            continue
        present = any(value is not None for value in service_pair) or any(
            record.get(key) is not None
            for key in ("USER_UNIT", "USER_INVOCATION_ID")
        )
        if not present:
            continue
        manager_pair = (
            record.get("USER_UNIT"),
            record.get("USER_INVOCATION_ID"),
        )
        exact_service = service_pair == expected and manager_pair in (
            (None, None),
            expected,
        )
        exact_manager_exception = (
            manager_pair == expected
            and service_pair == (b"init.scope", None)
            and record.get("_SYSTEMD_CGROUP", b"").endswith(b"/init.scope")
            and record.get("_COMM") == b"systemd"
        )
        if not exact_service and not exact_manager_exception:
            raise RuntimeError(
                "journal export has wrong/incomplete ordinary provenance"
            )
    if (
        sum(kind == "start" for _, kind in events) != 1
        or sum(kind == "success" for _, kind in events) != 1
    ):
        raise RuntimeError(
            "journal export requires one exact wrapper start and SUCCESS marker"
        )
    if events[0][1] != "start" or events[-1][1] != "success":
        raise RuntimeError(
            "journal wrapper start/terminal ordering is invalid"
        )


def verify_canonical_source():
    if (
        CANONICAL_SOURCE.resolve()
        != pathlib.Path(CANONICAL_SOURCE_ROOT).resolve()
    ):
        raise RuntimeError("canonical source root is altered")
    if (
        not CANONICAL_SOURCE.is_dir()
        or git_output(CANONICAL_SOURCE, "rev-parse", "HEAD")
        != CANONICAL_SOURCE_COMMIT
        or git_output(CANONICAL_SOURCE, "rev-parse", "HEAD^{tree}")
        != CANONICAL_SOURCE_TREE
    ):
        raise RuntimeError(
            "canonical instrumented source commit/tree mismatch"
        )
    if git_output(CANONICAL_SOURCE, "status", "--porcelain"):
        raise RuntimeError("canonical instrumented source worktree is dirty")
    for relative, digest in INSTRUMENTATION_SOURCES.items():
        verify_hash(
            CANONICAL_SOURCE / relative,
            digest,
            f"instrumentation source {relative}",
        )
    validate_build_argv(BUILD_ARGV)
    validate_fixed_build_environment(BUILD_ENVIRONMENT["fixed_values"])
    validate_build_system_contract(CANONICAL_SOURCE)


def verify_native_identity():
    if not ADAPTIVE_NATIVE_CWD.is_dir() or not ADAPTIVE_NATIVE.is_file():
        raise RuntimeError("pinned adaptive native inputs are absent")
    if sha256(ADAPTIVE_NATIVE) != ADAPTIVE_NATIVE_SHA256:
        raise RuntimeError("pinned adaptive native binary SHA-256 mismatch")
    if (
        git_output(ADAPTIVE_NATIVE_CWD, "rev-parse", "HEAD")
        != ADAPTIVE_NATIVE_COMMIT
        or git_output(ADAPTIVE_NATIVE_CWD, "rev-parse", "HEAD^{tree}")
        != ADAPTIVE_NATIVE_TREE
        or git_output(ADAPTIVE_NATIVE_CWD, "status", "--porcelain")
    ):
        raise RuntimeError("pinned adaptive native source identity is invalid")
    for relative, digest in NATIVE_ABI_SOURCES.items():
        verify_hash(
            ADAPTIVE_NATIVE_CWD / relative,
            digest,
            f"native ABI source {relative}",
        )
    return {
        "binary": str(ADAPTIVE_NATIVE),
        "binary_sha256": ADAPTIVE_NATIVE_SHA256,
        "worktree": str(ADAPTIVE_NATIVE_CWD),
        "source_commit": ADAPTIVE_NATIVE_COMMIT,
        "source_tree": ADAPTIVE_NATIVE_TREE,
        "abi_source_sha256": NATIVE_ABI_SOURCES,
        "abi_contracts": ABI_CONTRACTS,
        "mapping_cookie": ADAPTIVE_COOKIE,
    }


def validate_link_transcript(path):
    lines = pathlib.Path(path).read_bytes().splitlines()
    target_name = b"X86_UMT_T32_W2/gem5.opt"
    if not any(
        target_name in line
        and re.search(rb"(?:\bLINK\b|\bLinking\b|\bg\+\+\b|\bc\+\+\b)", line)
        for line in lines
    ):
        raise RuntimeError("build transcript lacks the exact gem5 link")


def file_contains(path, needle):
    overlap = len(needle) - 1
    previous = b""
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value = previous + block
            if needle in value:
                return True
            previous = value[-overlap:]
    return False


def validate_conformance_provenance(value):
    exact_keys(
        value,
        ("host_report", "temporal_plan", "independent_review"),
        "conformance provenance",
    )
    bindings = (
        (
            "host_report",
            CONFORMANCE_REPORT,
            CONFORMANCE_REPORT_SHA256,
        ),
        ("temporal_plan", TEMPORAL_PLAN, TEMPORAL_PLAN_SHA256),
        (
            "independent_review",
            PROMOTION_REVIEW,
            PROMOTION_REVIEW_SHA256,
        ),
    )
    for key, expected_path, expected_sha256 in bindings:
        artifact(value[key], f"conformance {key}", expected_path)
        if value[key]["sha256"] != expected_sha256:
            raise RuntimeError(f"conformance {key} SHA-256 mismatch")
    report = read_json(CONFORMANCE_REPORT)
    review = read_json(PROMOTION_REVIEW)
    identity = review.get("evidence_identity", {})
    scope = review.get("review_scope", {})
    if (
        report.get("schema") != "lanl-maa-umt-pki4-conformance-host-gate-v3"
        or report.get("status") != "passed_host_only"
        or report.get("required_define") != CONFORMANCE_BUILD_DEFINE
        or report.get("input_source_sha256")
        != CONFORMANCE_INSTRUMENTATION_SOURCES
        or report.get("persisted_temporal_plan") != value["temporal_plan"]
        or review.get("schema")
        != "lanl-maa-umt-pki4-conformance-independent-promotion-review-v3"
        or review.get("decision") != "PASS"
        or review.get("status")
        != "passed_independent_host_v3_review_not_promoted"
        or scope.get("commit") != CANONICAL_SOURCE_COMMIT
        or scope.get("tree") != CANONICAL_SOURCE_TREE
        or identity.get("umt-pki4-conformance-host-gate-v3.json")
        != CONFORMANCE_REPORT_SHA256
        or identity.get("pki4-temporal-equivalence-plan-v2.json")
        != TEMPORAL_PLAN_SHA256
    ):
        raise RuntimeError("conformance report/plan/review semantics mismatch")
    return value


def validate_generated_root_ownership(value, invocation, pid, start_ticks):
    exact_keys(
        value,
        (
            "schema",
            "unit",
            "invocation_id",
            "wrapper_pid",
            "wrapper_proc_start_ticks",
            "nonce",
            "source_root",
            "generated_root",
            "root_device",
            "root_inode",
            "sentinel",
            "sentinel_sha256",
            "sentinel_device",
            "sentinel_inode",
            "success_state",
        ),
        "generated-root ownership",
    )
    root_stat = BUILD_ROOT.stat()
    sentinel = verify_hash(
        value["sentinel"], value["sentinel_sha256"], "ownership sentinel"
    )
    sentinel_stat = sentinel.stat()
    if (
        value["schema"] != GENERATED_ROOT_OWNERSHIP_SCHEMA
        or value["unit"] != BUILD_UNIT
        or value["invocation_id"] != invocation
        or value["wrapper_pid"] != pid
        or value["wrapper_proc_start_ticks"] != start_ticks
        or not re.fullmatch(r"[0-9a-f]{64}", value["nonce"])
        or pathlib.Path(value["source_root"]).resolve()
        != CANONICAL_SOURCE.resolve()
        or pathlib.Path(value["generated_root"]).resolve()
        != BUILD_ROOT.resolve()
        or pathlib.Path(value["sentinel"]).resolve()
        != GENERATED_ROOT_SENTINEL.resolve()
        or (value["root_device"], value["root_inode"])
        != (root_stat.st_dev, root_stat.st_ino)
        or (value["sentinel_device"], value["sentinel_inode"])
        != (sentinel_stat.st_dev, sentinel_stat.st_ino)
        or value["success_state"] != "retained_in_generated_root"
    ):
        raise RuntimeError("generated-root ownership binding mismatch")
    record = {
        key: value[key]
        for key in (
            "schema",
            "unit",
            "invocation_id",
            "wrapper_pid",
            "wrapper_proc_start_ticks",
            "nonce",
            "source_root",
            "generated_root",
            "root_device",
            "root_inode",
        )
    }
    if (
        sentinel.read_bytes()
        != (json.dumps(record, sort_keys=True, indent=2) + "\n").encode()
    ):
        raise RuntimeError("generated-root sentinel content mismatch")
    return value


def read_build_proof(path, digest, gem5, gem5_digest):
    path = verify_hash(path, digest, "instrumented-build proof")
    proof = read_json(path)
    exact_keys(
        proof,
        (
            "schema",
            "status",
            "producer",
            "source_worktree",
            "source_commit",
            "source_tree",
            "source_clean_before",
            "source_clean_after",
            "source_identity_unchanged",
            "gem5",
            "gem5_sha256",
            "build_cwd",
            "build_argv",
            "build_environment",
            "trace_defines",
            "instrumentation_source_sha256",
            "build_system_source_sha256",
            "clean_method",
            "initial_absent_paths",
            "invalidated_artifacts",
            "target_paths_absent_after_clean",
            "generated_root_ownership",
            "clean_stdout",
            "clean_stderr",
            "object_prebuild_argv",
            "object_prebuild_returncode",
            "object_prebuild_define_verified",
            "object_prebuild_artifact",
            "object_identity_unchanged_after_link",
            "object_prebuild_stdout",
            "object_prebuild_stderr",
            "build_returncode",
            "required_link_observed",
            "build_stdout",
            "build_stderr",
            "build_artifacts",
            "build_invocation",
            "observer_gate",
            "conformance_gate",
        ),
        "instrumented-build proof",
    )
    if (
        proof["schema"] != SCHEMA_BUILD_PROOF
        or proof["status"] != "passed"
        or proof["producer"]
        != "systemd-pki4-dual-build-proof-v19-service-wrapper"
    ):
        raise RuntimeError("instrumented-build proof schema/status mismatch")
    if (
        pathlib.Path(proof["source_worktree"]).resolve()
        != CANONICAL_SOURCE.resolve()
        or proof["source_commit"] != CANONICAL_SOURCE_COMMIT
        or proof["source_tree"] != CANONICAL_SOURCE_TREE
        or proof["source_clean_before"] is not True
        or proof["source_clean_after"] is not True
        or proof["source_identity_unchanged"] is not True
    ):
        raise RuntimeError("build proof does not bind canonical source")
    verify_canonical_source()
    if (
        pathlib.Path(proof["gem5"]).resolve() != CANONICAL_GEM5.resolve()
        or pathlib.Path(proof["gem5"]).resolve() != gem5
        or proof["gem5_sha256"] != gem5_digest
        or pathlib.Path(proof["build_cwd"]).resolve()
        != CANONICAL_SOURCE.resolve()
        or tuple(proof["build_argv"]) != BUILD_ARGV
        or proof["trace_defines"]
        != [TRACE_BUILD_DEFINE, CONFORMANCE_BUILD_DEFINE]
        or proof["instrumentation_source_sha256"] != INSTRUMENTATION_SOURCES
        or proof["build_system_source_sha256"] != BUILD_SYSTEM_SOURCES
        or proof["clean_method"] != BUILD_CLEAN_METHOD
        or proof["target_paths_absent_after_clean"] is not True
        or proof["initial_absent_paths"]
        != [str(BUILD_ROOT), str(CANONICAL_GEM5), str(BUILD_OBJECT)]
        or proof["invalidated_artifacts"] != {}
        or tuple(proof["object_prebuild_argv"]) != OBJECT_PREBUILD_ARGV
        or proof["object_prebuild_returncode"] != 0
        or proof["object_prebuild_define_verified"] is not True
        or proof["object_identity_unchanged_after_link"] is not True
        or proof["build_returncode"] != 0
        or proof["required_link_observed"] is not True
    ):
        raise RuntimeError("build proof command/clean/source binding mismatch")
    validate_build_argv(proof["build_argv"])
    validate_fixed_build_environment(
        proof["build_environment"]["fixed_values"]
    )
    validate_build_system_contract(
        CANONICAL_SOURCE, proof["build_system_source_sha256"]
    )
    validate_build_environment(proof["build_environment"], "build environment")

    artifacts = proof["build_artifacts"]
    exact_keys(
        artifacts,
        (
            "gem5",
            "lanl_maa_o",
            "config_compute_tokens",
            "config_fp_issue_width",
        ),
        "build artifacts",
    )
    artifact(artifacts["gem5"], "built gem5", CANONICAL_GEM5)
    artifact(artifacts["lanl_maa_o"], "built MAA object", BUILD_OBJECT)
    object_prebuild = proof["object_prebuild_artifact"]
    exact_keys(
        object_prebuild,
        ("path", "sha256", "device", "inode"),
        "object prebuild artifact",
    )
    object_stat = BUILD_OBJECT.stat()
    if (
        pathlib.Path(object_prebuild["path"]).resolve()
        != BUILD_OBJECT.resolve()
        or object_prebuild["sha256"] != artifacts["lanl_maa_o"]["sha256"]
        or (object_prebuild["device"], object_prebuild["inode"])
        != (object_stat.st_dev, object_stat.st_ino)
    ):
        raise RuntimeError("object prebuild/final identity mismatch")
    for key, (expected_path, expected_text) in CONFIG_ARTIFACTS.items():
        config_path = artifact(artifacts[key], f"build {key}", expected_path)
        if config_path.read_bytes() != expected_text:
            raise RuntimeError("generated UMT variant header is not exact")
    if artifacts["gem5"]["sha256"] != gem5_digest or any(
        not file_contains(path, marker)
        for path in (CANONICAL_GEM5, BUILD_OBJECT)
        for marker in COMPILED_MARKERS
    ):
        raise RuntimeError(
            "canonical gem5/object lacks exact dual instrumented identity"
        )

    inv = proof["build_invocation"]
    exact_keys(
        inv,
        (
            "unit",
            "launch_command",
            "show_command",
            "live_systemd_show",
            "terminal_systemd_show",
            "live_process_start_receipt",
            "journal_command",
            "journal",
            "journal_terminal_protocol",
            "wrapper",
            "wrapper_command",
            "wrapper_attestation",
            "cleanup_commands_after_terminal_capture",
        ),
        "build systemd invocation",
    )
    wrapper = artifact(
        inv["wrapper"], "service-owned build wrapper", PROOF_BUILD_WRAPPER
    )
    if sha256(wrapper) != PROOF_BUILD_WRAPPER_SHA256:
        raise RuntimeError(
            "service-owned proof build wrapper SHA-256 mismatch"
        )
    attestation = artifact(inv["wrapper_attestation"], "wrapper attestation")
    evidence_dir = attestation.parent
    if evidence_dir.name != BUILD_EVIDENCE_NAME:
        raise RuntimeError("wrapper attestation evidence identity mismatch")
    command = wrapper_command(evidence_dir, PROOF_BUILD_WRAPPER)
    if (
        wrapper != PROOF_BUILD_WRAPPER.resolve()
        or tuple(inv["wrapper_command"]) != command
        or inv["launch_command"]
        != build_systemd_run_command(evidence_dir, PROOF_BUILD_WRAPPER)
        or "--collect" in inv["launch_command"]
        or "--remain-after-exit" not in inv["launch_command"]
        or inv["cleanup_commands_after_terminal_capture"]
        != [list(item) for item in BUILD_CLEANUP_COMMANDS]
    ):
        raise RuntimeError("retained build launch/cleanup binding mismatch")

    live = parse_systemd_show(
        artifact(inv["live_systemd_show"], "live systemd-show"),
        "live systemd-show",
    )
    terminal = parse_systemd_show(
        artifact(inv["terminal_systemd_show"], "terminal systemd-show"),
        "terminal systemd-show",
    )
    receipt = artifact(
        inv["live_process_start_receipt"], "live process start receipt"
    )
    if (
        inv["unit"] != BUILD_UNIT
        or tuple(inv["show_command"]) != BUILD_SHOW_COMMAND
        or tuple(inv["journal_command"]) != BUILD_JOURNAL_COMMAND
        or inv["journal_terminal_protocol"] != JOURNAL_TERMINAL_PROTOCOL
        or not re.fullmatch(r"[1-9][0-9]*", live["MainPID"])
    ):
        raise RuntimeError("build systemd invocation binding mismatch")
    pid, invocation = int(live["MainPID"]), live["InvocationID"]
    process = parse_proc_start_receipt(
        receipt, pid, invocation, live["ExecMainStartTimestampMonotonic"]
    )
    for phase, value in (("live", live), ("terminal", terminal)):
        validate_show_snapshot(
            value,
            phase=phase,
            pid=pid,
            proc_start_ticks=process["proc_start_ticks"],
            invocation=invocation,
            command=command,
        )

    attest = read_json(attestation)
    exact_keys(
        attest,
        (
            "schema",
            "unit",
            "invocation_id",
            "wrapper_pid",
            "wrapper_proc_start_ticks",
            "status",
            "source_commit",
            "source_tree",
            "source_clean_before",
            "source_clean_after",
            "source_identity_unchanged",
            "clean_method",
            "initial_absent_paths",
            "invalidated_artifacts",
            "target_paths_absent_after_clean",
            "generated_root_ownership",
            "object_prebuild_argv",
            "object_prebuild_returncode",
            "object_prebuild_define_verified",
            "object_prebuild_artifact",
            "object_identity_unchanged_after_link",
            "build_argv",
            "build_environment",
            "build_returncode",
            "required_link_observed",
            "instrumentation_source_sha256",
            "build_system_source_sha256",
            "build_artifacts",
            "compiled_binary_markers",
            "observer_gate",
            "conformance_gate",
            "evidence",
        ),
        "wrapper attestation",
    )
    if (
        attest["schema"] != BUILD_ATTESTATION_SCHEMA
        or attest["unit"] != BUILD_UNIT
        or attest["invocation_id"] != invocation
        or attest["wrapper_pid"] != pid
        or attest["wrapper_proc_start_ticks"] != process["proc_start_ticks"]
        or attest["status"] != "passed"
        or attest["source_commit"] != CANONICAL_SOURCE_COMMIT
        or attest["source_tree"] != CANONICAL_SOURCE_TREE
        or attest["source_clean_before"] is not True
        or attest["source_clean_after"] is not True
        or attest["source_identity_unchanged"] is not True
        or attest["clean_method"] != BUILD_CLEAN_METHOD
        or attest["target_paths_absent_after_clean"] is not True
        or attest["initial_absent_paths"]
        != [str(BUILD_ROOT), str(CANONICAL_GEM5), str(BUILD_OBJECT)]
        or attest["invalidated_artifacts"] != {}
        or tuple(attest["object_prebuild_argv"]) != OBJECT_PREBUILD_ARGV
        or attest["object_prebuild_returncode"] != 0
        or attest["object_prebuild_define_verified"] is not True
        or attest["object_identity_unchanged_after_link"] is not True
        or tuple(attest["build_argv"]) != BUILD_ARGV
        or attest["build_returncode"] != 0
        or attest["required_link_observed"] is not True
        or attest["instrumentation_source_sha256"] != INSTRUMENTATION_SOURCES
        or attest["build_system_source_sha256"] != BUILD_SYSTEM_SOURCES
        or attest["compiled_binary_markers"]
        != [marker.decode("ascii") for marker in COMPILED_MARKERS]
    ):
        raise RuntimeError(
            "wrapper attestation identity/build binding mismatch"
        )
    validate_build_environment(
        attest["build_environment"], "wrapper environment"
    )
    validate_fixed_build_environment(
        attest["build_environment"]["fixed_values"]
    )
    validate_build_argv(attest["build_argv"])
    ownership = validate_generated_root_ownership(
        attest["generated_root_ownership"],
        invocation,
        pid,
        process["proc_start_ticks"],
    )
    if (
        attest["build_environment"] != proof["build_environment"]
        or attest["object_prebuild_artifact"] != object_prebuild
        or attest["invalidated_artifacts"] != proof["invalidated_artifacts"]
        or ownership != proof["generated_root_ownership"]
        or attest["build_artifacts"]
        != {key: value["sha256"] for key, value in artifacts.items()}
    ):
        raise RuntimeError(
            "proof/wrapper clean or artifact cross-binding mismatch"
        )

    evidence_names = {
        "clean_stdout": "clean.stdout",
        "clean_stderr": "clean.stderr",
        "object_prebuild_stdout": "object-prebuild.stdout",
        "object_prebuild_stderr": "object-prebuild.stderr",
        "build_stdout": "build.stdout",
        "build_stderr": "build.stderr",
        "observer_stdout": "observer.stdout",
        "observer_stderr": "observer.stderr",
        "observer_report": "observer-report.json",
        "observer_transcript": "observer-transcript.txt",
        "source_manifest": "observer-input-source-sha256.json",
        "conformance_source_manifest": (
            "conformance-input-source-sha256.json"
        ),
        "conformance_stdout": "conformance.stdout",
        "conformance_stderr": "conformance.stderr",
        "conformance_report": "conformance-report.json",
        "conformance_transcript": "conformance-transcript.txt",
        "build_system_manifest": "build-system-source-sha256.json",
        "target_config_literal_scan": "target-config-literal-scan.json",
    }
    exact_keys(attest["evidence"], evidence_names, "wrapper evidence")
    for key, name in evidence_names.items():
        evidence_artifact(attest["evidence"][key], evidence_dir, name)
    build_system_manifest = read_json(
        evidence_artifact(
            attest["evidence"]["build_system_manifest"],
            evidence_dir,
            "build-system-source-sha256.json",
        )
    )
    if build_system_manifest != BUILD_SYSTEM_SOURCES:
        raise RuntimeError("wrapper build-system manifest mismatch")
    if (
        proof["clean_stdout"] != attest["evidence"]["clean_stdout"]
        or proof["clean_stderr"] != attest["evidence"]["clean_stderr"]
        or proof["object_prebuild_stdout"]
        != attest["evidence"]["object_prebuild_stdout"]
        or proof["object_prebuild_stderr"]
        != attest["evidence"]["object_prebuild_stderr"]
        or proof["build_stdout"] != attest["evidence"]["build_stdout"]
        or proof["build_stderr"] != attest["evidence"]["build_stderr"]
        or pathlib.Path(proof["clean_stderr"]["path"]).read_bytes() != b""
    ):
        raise RuntimeError("clean/build transcript cross-binding mismatch")
    validate_clean_stdout(proof["clean_stdout"]["path"])
    validate_object_compile_transcript(
        artifact(
            proof["object_prebuild_stdout"], "object prebuild stdout"
        ).read_bytes()
    )
    artifact(proof["object_prebuild_stderr"], "object prebuild stderr")
    validate_link_transcript(artifact(proof["build_stdout"], "build stdout"))
    artifact(proof["build_stderr"], "build stderr")

    source_manifest = read_json(
        evidence_artifact(
            attest["evidence"]["source_manifest"],
            evidence_dir,
            "observer-input-source-sha256.json",
        )
    )
    if source_manifest != LEGACY_INSTRUMENTATION_SOURCES:
        raise RuntimeError("wrapper source manifest mismatch")
    conformance_source_manifest = read_json(
        evidence_artifact(
            attest["evidence"]["conformance_source_manifest"],
            evidence_dir,
            "conformance-input-source-sha256.json",
        )
    )
    if conformance_source_manifest != CONFORMANCE_INSTRUMENTATION_SOURCES:
        raise RuntimeError("wrapper conformance source manifest mismatch")
    scan = read_json(
        evidence_artifact(
            attest["evidence"]["target_config_literal_scan"],
            evidence_dir,
            "target-config-literal-scan.json",
        )
    )
    exact_keys(
        scan,
        (
            "target",
            "target_sha256",
            "object",
            "object_sha256",
            "config_compute_tokens",
            "config_compute_tokens_sha256",
            "config_fp_issue_width",
            "config_fp_issue_width_sha256",
            "compiled_binary_markers",
            "markers_verified_in",
        ),
        "target/object/config scan",
    )
    if (
        pathlib.Path(scan["target"]).resolve() != CANONICAL_GEM5.resolve()
        or scan["target_sha256"] != artifacts["gem5"]["sha256"]
        or pathlib.Path(scan["object"]).resolve() != BUILD_OBJECT.resolve()
        or scan["object_sha256"] != artifacts["lanl_maa_o"]["sha256"]
        or pathlib.Path(scan["config_compute_tokens"]).resolve()
        != CONFIG_ARTIFACTS["config_compute_tokens"][0].resolve()
        or scan["config_compute_tokens_sha256"]
        != artifacts["config_compute_tokens"]["sha256"]
        or pathlib.Path(scan["config_fp_issue_width"]).resolve()
        != CONFIG_ARTIFACTS["config_fp_issue_width"][0].resolve()
        or scan["config_fp_issue_width_sha256"]
        != artifacts["config_fp_issue_width"]["sha256"]
        or scan["compiled_binary_markers"]
        != [marker.decode("ascii") for marker in COMPILED_MARKERS]
        or scan["markers_verified_in"]
        != [str(CANONICAL_GEM5), str(BUILD_OBJECT)]
    ):
        raise RuntimeError("target/object/config scan mismatch")

    expected_gate = (
        "/usr/bin/python3",
        str(
            CANONICAL_SOURCE
            / "tests/lanl_maa/run_umt_production_ingress_trace_gate.py"
        ),
        "--cxx",
        "g++",
        "--binary",
        str(CANONICAL_GEM5),
        "--binary-sha256",
        gem5_digest,
        "--input-source-sha256",
        str(evidence_dir / "observer-input-source-sha256.json"),
    )
    exact_keys(
        attest["observer_gate"],
        ("command", "returncode", "report", "transcript"),
        "wrapper observer gate",
    )
    if (
        tuple(attest["observer_gate"]["command"]) != expected_gate
        or attest["observer_gate"]["returncode"] != 0
        or attest["observer_gate"]["report"]
        != attest["evidence"]["observer_report"]
        or attest["observer_gate"]["transcript"]
        != attest["evidence"]["observer_transcript"]
    ):
        raise RuntimeError("wrapper observer gate binding mismatch")

    expected_conformance_gate = (
        "/usr/bin/python3",
        str(
            CANONICAL_SOURCE
            / "tests/lanl_maa/run_umt_pki4_conformance_gate.py"
        ),
        "--cxx",
        "g++",
    )
    exact_keys(
        attest["conformance_gate"],
        ("command", "returncode", "report", "transcript", "provenance"),
        "wrapper conformance gate",
    )
    if (
        tuple(attest["conformance_gate"]["command"])
        != expected_conformance_gate
        or attest["conformance_gate"]["returncode"] != 0
        or attest["conformance_gate"]["report"]
        != attest["evidence"]["conformance_report"]
        or attest["conformance_gate"]["transcript"]
        != attest["evidence"]["conformance_transcript"]
    ):
        raise RuntimeError("wrapper conformance gate binding mismatch")
    conformance_provenance = validate_conformance_provenance(
        attest["conformance_gate"]["provenance"]
    )

    journal = artifact(inv["journal"], "build journal")
    parse_export_journal(
        journal, invocation, pid, process["proc_start_ticks"], gem5_digest
    )
    gate = proof["observer_gate"]
    exact_keys(
        gate,
        (
            "command",
            "input_source_sha256",
            "binary",
            "binary_sha256",
            "stdout",
            "stderr",
            "report",
            "transcript",
            "status",
        ),
        "observer gate",
    )
    if (
        gate["status"] != "passed"
        or tuple(gate["command"]) != expected_gate
        or gate["input_source_sha256"] != LEGACY_INSTRUMENTATION_SOURCES
        or pathlib.Path(gate["binary"]).resolve() != CANONICAL_GEM5.resolve()
        or gate["binary_sha256"] != gem5_digest
        or gate["stdout"] != attest["evidence"]["observer_stdout"]
        or gate["stderr"] != attest["evidence"]["observer_stderr"]
        or gate["report"] != attest["evidence"]["observer_report"]
        or gate["transcript"] != attest["evidence"]["observer_transcript"]
    ):
        raise RuntimeError("proof observer gate binding mismatch")
    artifact(gate["stdout"], "observer stdout")
    artifact(gate["stderr"], "observer stderr")
    transcript = artifact(gate["transcript"], "observer transcript")
    report = read_json(artifact(gate["report"], "observer report"))
    expected_cells = [
        {
            "tokens": t,
            "issue_width": w,
            "waiter_counts": [1, 7, 8],
            "abi_boundaries": ["D32", "D64"],
            "two_lane_serialization": "rejected_by_trace_difference",
            "selected_token_text": (
                "numeric_for_denominator_and_source_sentinel"
            ),
            "default_off": "compiled_without_observer_macro",
        }
        for t, w in ((24, 1), (24, 2), (32, 1), (32, 2))
    ]
    if (
        not isinstance(report, dict)
        or set(report)
        != {
            "schema",
            "status",
            "source_root",
            "input_source_sha256",
            "binary",
            "binary_sha256",
            "required_define",
            "compiled_binary_markers",
            "cells",
        }
        or report["schema"] != "lanl-maa-umt-production-ingress-trace-v3"
        or report["status"] != "passed"
        or pathlib.Path(report["source_root"]).resolve()
        != CANONICAL_SOURCE.resolve()
        or report["input_source_sha256"] != LEGACY_INSTRUMENTATION_SOURCES
        or pathlib.Path(report["binary"]).resolve() != CANONICAL_GEM5.resolve()
        or report["binary_sha256"] != gem5_digest
        or report["required_define"] != TRACE_BUILD_DEFINE
        or report["compiled_binary_markers"]
        != [
            "UMT_INGRESS kind=",
            "d64_hold cycle=",
            "waiters=%u token=%llu pre=",
        ]
        or report["cells"] != expected_cells
        or "status=0/SUCCESS"
        not in pathlib.Path(transcript)
        .read_text(encoding="utf-8", errors="strict")
        .splitlines()
    ):
        raise RuntimeError("observer report/transcript semantics mismatch")

    conformance = proof["conformance_gate"]
    exact_keys(
        conformance,
        (
            "command",
            "input_source_sha256",
            "stdout",
            "stderr",
            "report",
            "transcript",
            "provenance",
            "status",
        ),
        "conformance gate",
    )
    conformance_report = artifact(conformance["report"], "conformance report")
    conformance_transcript = artifact(
        conformance["transcript"], "conformance transcript"
    )
    if (
        conformance["status"] != "passed_host_only"
        or tuple(conformance["command"]) != expected_conformance_gate
        or conformance["input_source_sha256"]
        != CONFORMANCE_INSTRUMENTATION_SOURCES
        or conformance["stdout"] != attest["evidence"]["conformance_stdout"]
        or conformance["stderr"] != attest["evidence"]["conformance_stderr"]
        or conformance["report"] != attest["evidence"]["conformance_report"]
        or conformance["transcript"]
        != attest["evidence"]["conformance_transcript"]
        or conformance["provenance"] != conformance_provenance
        or conformance_report.read_bytes() != CONFORMANCE_REPORT.read_bytes()
        or sha256(conformance_report) != CONFORMANCE_REPORT_SHA256
        or "status=0/SUCCESS"
        not in conformance_transcript.read_text(
            encoding="utf-8", errors="strict"
        ).splitlines()
    ):
        raise RuntimeError("conformance gate proof semantics mismatch")
    artifact(conformance["stdout"], "conformance stdout")
    artifact(conformance["stderr"], "conformance stderr")
    return path


def case_command(gem5, root, case):
    value = CASES[case]
    runner = ROOT / "tests/lanl_maa/umt_ingress_micro_process_cpu.py"
    return [
        str(gem5),
        "--listener-mode=off",
        "--dot-config=",
        f"--outdir={root / 'm5out'}",
        "--debug-flags=LANLMAA",
        f"--debug-file={root / 'debug.log'}",
        str(runner),
        f"--binary={ADAPTIVE_NATIVE}",
        f"--cwd={ADAPTIVE_NATIVE_CWD}",
        f"--output-dir={root}",
        f"--app-stdout={root / 'app.stdout'}",
        f"--app-stderr={root / 'app.stderr'}",
        f"--submission-report={root / 'submission.json'}",
        f"--groups={value['groups']}",
        f"--umt-mode={value['mode']}",
        f"--label={LABEL_PREFIX}_{case}",
    ]


def arm_wrapper_argv(root, gem5_argv):
    command_digest = json_sha256(gem5_argv)
    return [
        "/usr/bin/python3",
        str(ARM_WRAPPER),
        "--arm-root",
        str(pathlib.Path(root).resolve()),
        "--gem5-argv-sha256",
        command_digest,
        "--",
        *gem5_argv,
    ]


def expected_contract(campaign, proof, proof_digest, gem5_digest):
    harness = verify_harness_identity()
    guest_compatibility = verify_guest_compatibility_source()
    wrapper = verify_hash(
        ARM_WRAPPER, ARM_WRAPPER_SHA256, "service-owned arm wrapper"
    )
    arms = {}
    for name in CASES:
        root = campaign / "arms" / name
        command = case_command(CANONICAL_GEM5, root, name)
        wrapper_command = arm_wrapper_argv(root, command)
        arms[name] = {
            "root": str(root),
            "unit": f"umt-ingress-micro-v16-{name}-20260830.service",
            "gem5_argv": command,
            "gem5_argv_sha256": json_sha256(command),
            "wrapper": {
                "path": str(wrapper),
                "sha256": ARM_WRAPPER_SHA256,
            },
            "wrapper_argv": wrapper_command,
            "wrapper_argv_sha256": json_sha256(wrapper_command),
            "binary_sha256": ADAPTIVE_NATIVE_SHA256,
        }
    return {
        "schema": SCHEMA_CONTRACT,
        "status": "frozen_before_dispatch",
        "campaign_root": str(campaign),
        "harness_source_root": harness["source_root"],
        "harness_source_commit": harness["source_commit"],
        "harness_source_tree": harness["source_tree"],
        "harness_reviewed_file_sha256": harness["reviewed_file_sha256"],
        "gem5": str(CANONICAL_GEM5),
        "gem5_sha256": gem5_digest,
        "instrumented_build_proof": str(proof),
        "instrumented_build_proof_schema": SCHEMA_BUILD_PROOF,
        "instrumented_build_proof_sha256": proof_digest,
        "required_define": TRACE_BUILD_DEFINE,
        "guest_compatibility_environment": guest_compatibility,
        "native_identity": verify_native_identity(),
        "cases": CASES,
        "arms": arms,
        "resource_policy": RESOURCE_POLICY,
        "predecessors": {
            "v1": {"review_status": "rejected", "reuse": "forbidden"},
            "v2": {"review_status": "rejected", "reuse": "forbidden"},
            "v6": {
                "review_status": (
                    "rejected_direct_gem5_launch_without_stream_capture"
                ),
                "reuse": "forbidden",
            },
            "v7": {
                "review_status": (
                    "split_predecessors_only; build-v7 and arm-v7 are "
                    "accepted solely through a combined contract"
                ),
                "reuse": "forbidden_as_combined_contract",
            },
            "v8": {
                "review_status": (
                    "rejected_invalid_systemd_runtime_launch_property"
                ),
                "reuse": "forbidden",
            },
            "v9": {
                "review_status": "rejected_inert_CPPDEFINES_build_argument",
                "reuse": "forbidden",
            },
            "v10": {
                "review_status": "rejected_command_line_CCFLAGS_overwritten",
                "reuse": "forbidden",
            },
            "v11": {
                "review_status": "rejected_no_exec_preflight_config_failure",
                "reuse": "forbidden",
            },
            "v12": {
                "review_status": "rejected_incomplete_failure_restoration",
                "reuse": "forbidden",
            },
            "v13": {
                "review_status": "rejected_obsolete_config_artifact_paths",
                "reuse": "forbidden",
            },
            "v14": {
                "review_status": "rejected_overstrict_manager_journal_metadata",
                "reuse": "forbidden_as_combined_contract",
            },
            "v15": {
                "review_status": "rejected_guest_unsupported_PCMPSTRI",
                "reuse": "forbidden_raw_runs",
            },
        },
        "claim_boundary": (
            "Correctness and ingress mechanism only; simTicks are not "
            "compared or promoted."
        ),
    }


def systemd_run_command(unit, command):
    """Construct an arm launch from the one frozen policy mapping.

    CPUQuotaPerSecUSec is a systemd *show* property (microseconds per
    second); `systemd-run` instead consumes CPUQuota. RuntimeMaxUSec is also
    a *show* property, while the accepted unit-file launch directive is
    RuntimeMaxSec. Keep both conversions explicit rather than deriving option
    names by rewriting arbitrary property strings.
    """
    validate_systemd_resource_mapping()
    return [
        "systemd-run",
        "--user",
        "--collect",
        f"--unit={unit}",
        *[f"--property={key}={value}" for key, value in DISPATCH_PROPERTIES],
        *command,
    ]


def systemd_arm_plan(arm):
    command = systemd_run_command(arm["unit"], arm["wrapper_argv"])
    return {
        "unit": arm["unit"],
        "wrapper": arm["wrapper"],
        "wrapper_argv": arm["wrapper_argv"],
        "wrapper_argv_sha256": arm["wrapper_argv_sha256"],
        "gem5_argv_sha256": arm["gem5_argv_sha256"],
        "systemd_run_argv": command,
        "systemd_run_argv_sha256": json_sha256(command),
    }


def freeze_contract(args):
    verify_harness_identity()
    if (
        pathlib.Path(args.instrumented_build_proof).resolve()
        != BUILD_PROOF_PATH.resolve()
    ):
        raise RuntimeError(
            "v16 freeze requires the exact future v19 proof publication path"
        )
    gem5 = verify_hash(args.gem5, args.gem5_sha256, "gem5")
    if gem5 != CANONICAL_GEM5.resolve():
        raise RuntimeError("gem5 is not the canonical T32/W2 target")
    proof = read_build_proof(
        args.instrumented_build_proof,
        args.instrumented_build_proof_sha256,
        gem5,
        args.gem5_sha256,
    )
    campaign, output = (
        pathlib.Path(args.campaign_root).resolve(),
        pathlib.Path(args.output).resolve(),
    )
    if campaign.exists() or output != campaign / CONTRACT_FILENAME:
        raise RuntimeError(
            "v16 contract must be a fresh campaign/ingress-contract-v16.json"
        )
    contract = expected_contract(
        campaign, proof, args.instrumented_build_proof_sha256, args.gem5_sha256
    )
    campaign.mkdir(parents=True, exist_ok=False)
    atomic_no_clobber(output, contract)
    return contract


def dispatch_plan(contract_path, digest, campaign_root, output):
    campaign, contract_path = pathlib.Path(
        campaign_root
    ).resolve(), verify_hash(contract_path, digest, "frozen contract")
    if contract_path != campaign / CONTRACT_FILENAME:
        raise RuntimeError(
            "externally fixed campaign/contract identity mismatch"
        )
    contract = read_json(contract_path)
    if (
        not isinstance(contract, dict)
        or set(contract) != CONTRACT_FIELDS
        or contract.get("schema") != SCHEMA_CONTRACT
    ):
        raise RuntimeError(
            "v16 contract semantics, resources, units, roots, or self-hash "
            "binding altered"
        )
    contract_harness_identity(contract)
    if (
        pathlib.Path(contract["instrumented_build_proof"]).resolve()
        != BUILD_PROOF_PATH.resolve()
        or contract["instrumented_build_proof_schema"] != SCHEMA_BUILD_PROOF
    ):
        raise RuntimeError(
            "v16 contract does not bind the exact v19 proof path/schema"
        )
    gem5 = verify_hash(
        CANONICAL_GEM5, contract["gem5_sha256"], "canonical gem5"
    )
    proof = read_build_proof(
        contract["instrumented_build_proof"],
        contract["instrumented_build_proof_sha256"],
        gem5,
        contract["gem5_sha256"],
    )
    expected = expected_contract(
        campaign,
        pathlib.Path(contract.get("instrumented_build_proof", ".")),
        contract.get("instrumented_build_proof_sha256", ""),
        contract.get("gem5_sha256", ""),
    )
    if contract != expected:
        raise RuntimeError(
            "v16 contract semantics, resources, units, roots, or self-hash "
            "binding altered"
        )
    output = pathlib.Path(output).resolve()
    if output != campaign / "identity" / DISPATCH_FILENAME:
        raise RuntimeError("v16 dry dispatch output identity mismatch")
    commands = {
        name: systemd_arm_plan(arm) for name, arm in contract["arms"].items()
    }
    plan = {
        "schema": SCHEMA_DISPATCH_PLAN,
        "status": "dry_only_not_dispatched",
        "campaign_root": str(campaign),
        "contract": str(contract_path),
        "contract_sha256": digest,
        "concurrency": {
            "launch_mode": "concurrent_all_arms",
            "max_parallel": len(CASES),
            "unique_units_and_roots_required": True,
        },
        "arms": commands,
        "forbidden_in_dry_mode": [
            "systemd-run execution",
            "gem5 execution",
            "build",
            "remote operations",
        ],
    }
    atomic_no_clobber(output, plan)
    return plan


def parse_debug_file_text(text):
    events = []
    for raw in text.splitlines():
        if "UMT_INGRESS" not in raw:
            continue
        line = raw[raw.index("UMT_INGRESS") :]
        match = SOURCE_RE.fullmatch(line)
        if match:
            keys = (
                "kind",
                "cycle",
                "callback",
                "lane",
                "packet",
                "line",
                "abi",
                "stage",
                "group",
                "corner",
                "order",
                "waiters",
                "token",
                "pre",
                "post",
                "next_tick",
            )
            event = dict(zip(keys, match.groups()))
            for key in (
                "cycle",
                "callback",
                "lane",
                "abi",
                "stage",
                "group",
                "corner",
                "order",
                "waiters",
                "token",
                "next_tick",
            ):
                event[key] = int(event[key])
            event["class"] = "callback"
        else:
            match = LINE_RE.fullmatch(line)
            if not match:
                raise RuntimeError(f"unparseable UMT_INGRESS witness: {line}")
            keys = (
                "abi_label",
                "kind",
                "cycle",
                "line",
                "abi",
                "stage",
                "group",
                "corner",
                "waiters",
                "pre",
                "post",
            )
            event = dict(zip(keys, match.groups()))
            for key in ("cycle", "abi", "stage", "group", "corner", "waiters"):
                event[key] = int(event[key])
            event["class"] = "line"
        event["ordinal"] = len(events)
        events.append(event)
    if not events:
        raise RuntimeError("missing UMT_INGRESS witness")
    return events


def parse_debug(path):
    return parse_debug_file_text(
        pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    )


def validate_trace(events, case, descriptor_callback_restart_epochs=None):
    spec, abi = CASES[case], 4 if CASES[case]["abi"] == "D32" else 5
    if any(x["abi"] != abi for x in events) or any(
        events[i]["cycle"] > events[i + 1]["cycle"]
        for i in range(len(events) - 1)
    ):
        raise RuntimeError("trace ABI/chronology mismatch")
    callbacks, lines = [x for x in events if x["class"] == "callback"], [
        x for x in events if x["class"] == "line"
    ]
    if not callbacks or {x["kind"] for x in callbacks} != {
        "source",
        "denominator",
    }:
        raise RuntimeError(
            "trace lacks exact source and denominator callbacks"
        )
    groups, active, active_cycle, closed = {}, None, None, set()
    callback_epoch = 1
    restart_epochs = descriptor_callback_restart_epochs or {}
    used_restart_cycles = set()
    for item in callbacks:
        callback = item["callback"]
        if (
            callback <= 0
            or item["waiters"] <= 0
            or item["next_tick"] <= item["cycle"]
            or item["pre"] == item["post"]
        ):
            raise RuntimeError(
                "callback ordering/waiter/digest witness is invalid"
            )
        new_group = active is not None and (
            callback != active or item["cycle"] != active_cycle
        )
        if item["cycle"] in restart_epochs and new_group and callback == 1:
            # The legacy UMT_INGRESS observer is cleared at descriptor rearm,
            # so its callback counter restarts at one.  That debug format has
            # no descriptor-epoch field.  Only the dual-observer live
            # normalizer opts in.  Each inferred restart cycle must already
            # be cross-bound to the next explicit descriptor_epoch in the
            # canonical-v3 stream, which is then fully validated by the
            # committed canonical normalizer.
            callback_epoch += 1
            if restart_epochs[item["cycle"]] != callback_epoch:
                raise RuntimeError(
                    "legacy callback restart differs from canonical epoch"
                )
            used_restart_cycles.add(item["cycle"])
            active, active_cycle, closed = None, None, set()
        if active is not None and callback != active:
            closed.add(active)
        if (callback in closed and callback != active) or (
            callback == active and item["cycle"] != active_cycle
        ):
            raise RuntimeError("callback sequence reappears after closure")
        active, active_cycle = callback, item["cycle"]
        groups.setdefault((callback_epoch, callback), []).append(item)
    for epoch in range(1, callback_epoch + 1):
        sequences = sorted(
            callback
            for group_epoch, callback in groups
            if group_epoch == epoch
        )
        if sequences != list(range(1, len(sequences) + 1)):
            raise RuntimeError("callback sequence is not contiguous")
    if used_restart_cycles != set(restart_epochs):
        raise RuntimeError(
            "canonical callback epoch start is absent from legacy trace"
        )
    for items in groups.values():
        if (
            len({x["kind"] for x in items}) != 1
            or [x["lane"] for x in items] != list(range(len(items)))
            or [x["order"] for x in items] != list(range(len(items)))
            or len({x["cycle"] for x in items}) != 1
            or len({x["waiters"] for x in items}) != 1
            or items[0]["waiters"] != len(items)
            or any(
                items[i]["post"] != items[i + 1]["pre"]
                for i in range(len(items) - 1)
            )
        ):
            raise RuntimeError(
                "callback lane/order/waiter/digest chain is invalid"
            )
    source_groups = [
        items for items in groups.values() if items[0]["kind"] == "source"
    ]
    if not source_groups:
        raise RuntimeError("trace lacks source callbacks for bank pressure")
    source_banks = [
        [item["group"] % 4 for item in items] for items in source_groups
    ]
    source_bank_pressure = {
        "bank_count": 4,
        "bank_mapping": "bank=group%4",
        "source_callbacks": len(source_groups),
        "max_source_writes_per_callback": max(map(len, source_groups)),
        "max_same_bank_multiplicity": max(
            max(banks.count(bank) for bank in set(banks))
            for banks in source_banks
        ),
        "callbacks_with_duplicate_banks": sum(
            len(set(banks)) != len(banks) for banks in source_banks
        ),
        "four_distinct_bank_accepted_callbacks": sum(
            len(banks) <= 4 and len(set(banks)) == len(banks)
            for banks in source_banks
        ),
        "claim_boundary": (
            "Trace-derived pressure for the current four-bank stream state; "
            "not RTL timing or physical equivalence."
        ),
    }
    waits = [x["waiters"] for x in callbacks]
    if case == "d32-g31" and not any(
        waits[i : i + 2] == [7, 1] for i in range(len(waits) - 1)
    ):
        raise RuntimeError("G31 lacks chronological 7+1 boundary")
    if case in ("d32-g32", "d64-g32") and max(waits) != 8:
        raise RuntimeError("G32 lacks exact eight-waiter response")
    if case == "d64-g31" and not {7, 8}.issubset(set(waits)):
        raise RuntimeError("D64/G31 lacks full-line and short-tail callbacks")
    if spec["abi"] == "D32":
        if not lines or any(
            x["abi_label"] != "d32" or x["kind"] != "release" for x in lines
        ):
            raise RuntimeError("D32 line witness invalid")
    else:
        active, release_waiters = {}, []
        for item in lines:
            if item["abi_label"] != "d64":
                raise RuntimeError("D64 line witness carries another ABI")
            key = tuple(
                item[name] for name in ("line", "stage", "group", "corner")
            )
            if item["kind"] == "hold":
                prior = active.setdefault(key, [])
                if prior and (
                    item["cycle"] < prior[-1]["cycle"]
                    or item["waiters"] < prior[-1]["waiters"]
                ):
                    raise RuntimeError("D64 hold chronology/count regressed")
                prior.append(item)
                continue
            if item["kind"] != "release":
                raise RuntimeError("D64 line witness kind is invalid")
            holds = active.pop(key, [])
            if (
                not 1 <= item["waiters"] <= 8
                or any(hold["waiters"] >= item["waiters"] for hold in holds)
                or (holds and item["cycle"] <= holds[-1]["cycle"])
            ):
                raise RuntimeError("D64 hold/release lifecycle is malformed")
            release_waiters.append(item["waiters"])
        if active or not release_waiters:
            raise RuntimeError("D64 trace ends with an unreleased line")
        if case == "d64-g32" and set(release_waiters) != {8}:
            raise RuntimeError("D64/G32 release is not a full line")
        if case == "d64-g31" and not {7, 8}.issubset(set(release_waiters)):
            raise RuntimeError("D64/G31 lacks 7-word tail and 8-word line")
    return {
        "callbacks": len(groups),
        "callback_epochs": callback_epoch,
        "records": len(events),
        "max_lanes": max(map(len, groups.values())),
        "max_waiters": max(waits),
        "source_callbacks": sum(x["kind"] == "source" for x in callbacks),
        "denominator_callbacks": sum(
            x["kind"] == "denominator" for x in callbacks
        ),
        "d64_holds": sum(x["kind"] == "hold" for x in lines),
        "releases": sum(x["kind"] == "release" for x in lines),
        "source_bank_pressure": source_bank_pressure,
    }


def parse_stats(path):
    values = {}
    for line in (
        pathlib.Path(path)
        .read_text(encoding="utf-8", errors="replace")
        .splitlines()
    ):
        fields = line.split()
        if len(fields) >= 2 and fields[0].startswith("system.lanl_maa."):
            try:
                values[fields[0].split(".")[-1]] = int(float(fields[1]))
            except ValueError:
                pass
    return values


def canonical_descriptor_callback_restart_epochs(path):
    prefix = "UMT_PKI4_CONFORMANCE "
    restarts = {}
    with pathlib.Path(path).open(
        "r", encoding="utf-8", errors="strict"
    ) as stream:
        for line_number, line in enumerate(stream, 1):
            if (
                not line.startswith(prefix)
                or '"phase":"callback_begin"' not in line
                or '"callback_sequence":1' not in line
            ):
                continue
            try:
                record = json.loads(line[len(prefix) :])
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    "malformed canonical callback epoch boundary at "
                    f"line {line_number}"
                ) from error
            if (
                record.get("phase") != "callback_begin"
                or record.get("callback_sequence") != 1
            ):
                continue
            epoch, cycle = record.get("descriptor_epoch"), record.get("cycle")
            if (
                record.get("schema") != "lanl-maa-umt-pki4-conformance-v3"
                or not isinstance(epoch, int)
                or epoch <= 0
                or not isinstance(cycle, int)
                or cycle <= 0
            ):
                raise RuntimeError("invalid canonical callback epoch boundary")
            if epoch == 1:
                continue
            if cycle in restarts or epoch != len(restarts) + 2:
                raise RuntimeError(
                    "canonical callback epoch boundaries are not unique/contiguous"
                )
            restarts[cycle] = epoch
    if not restarts:
        raise RuntimeError(
            "canonical trace has no descriptor callback restart"
        )
    return restarts


def validate_submission(submission, case):
    exact_keys(submission, SUBMISSION_FIELDS, "submission report")
    spec, groups = CASES[case], CASES[case]["groups"]
    if (
        submission["schema"] != SCHEMA_SUBMISSION
        or submission["opcode"] != 11
        or submission["mode"] != "ordered_wave"
    ):
        raise RuntimeError(
            "submission does not attest exact opcode-11 ordered-wave schema"
        )
    if any(
        not isinstance(submission[x], int) or submission[x] <= 0
        for x in (
            "wave_calls",
            "wave_corners",
            "direct_arena_submissions",
            "wave_soa_arena_descriptors",
            "descriptor_submissions",
            "submitted_groups",
            "capability_probes",
        )
    ):
        raise RuntimeError(
            "submission lacks positive descriptor/wave evidence"
        )
    if (
        submission["wave_corners"] != submission["wave_calls"] * 8
        or submission["descriptor_submissions"] != submission["wave_calls"]
        or submission["direct_arena_submissions"] != submission["wave_calls"]
        or submission["wave_soa_arena_descriptors"] != submission["wave_calls"]
        or submission["wave_calls"] * groups != submission["submitted_groups"]
    ):
        raise RuntimeError(
            "submission per-call group accounting is inconsistent"
        )
    if (
        submission["last_error"] != 0
        or submission["all_completions_valid"] is not True
        or submission["ordered_corner_scalar_solves_replaced"] is not True
        or submission["adaptive_wave_selector_threshold_groups"] != 32
    ):
        raise RuntimeError("submission completion/error gate failed")
    if any(
        submission[x] != 0
        for x in (
            "corner_calls",
            "scalar_direct_corners",
            "scalar_direct_groups",
            "scalar_ordered_fallback_corners",
            "scalar_ordered_fallback_groups",
            "wave_record_copy_bytes",
            "wave_result_clear_bytes",
            "wave_result_copy_bytes",
            "post_completion_result_read_words",
        )
    ):
        raise RuntimeError(
            "submission reports scalar fallback or forbidden copy/readback"
        )
    selected, other = (
        ("d32", "d64") if spec["abi"] == "D32" else ("d64", "d32")
    )
    if (
        submission[f"wave_{selected}_descriptors"] != submission["wave_calls"]
        or submission[f"wave_{selected}_groups"]
        != submission["submitted_groups"]
        or submission[f"wave_{selected}_decisions"] != submission["wave_calls"]
        or any(
            submission[f"wave_{other}_{suffix}"] != 0
            for suffix in ("descriptors", "groups", "decisions")
        )
    ):
        raise RuntimeError(
            f"submission does not prove exact selected {spec['abi']} counters"
        )
    return {
        "abi": f"{spec['abi']}-v{ABI_CONTRACTS[spec['abi']]['version']}",
        "wave_calls": submission["wave_calls"],
        "submitted_groups": submission["submitted_groups"],
    }


def regular_identity(path):
    path = pathlib.Path(path)
    value = os.lstat(path)
    if not stat.S_ISREG(value.st_mode):
        raise RuntimeError(f"arm evidence is not a regular file: {path}")
    return {
        "path": str(path.resolve()),
        "device": value.st_dev,
        "inode": value.st_ino,
    }


def reserved_output_initial_sha256(root, case):
    csv = f"{LABEL_PREFIX}_{case}.csv"
    return {
        "gem5.stdout": EMPTY_SHA256,
        "gem5.stderr": EMPTY_SHA256,
        "app.stdout": EMPTY_SHA256,
        "app.stderr": EMPTY_SHA256,
        "debug.log": EMPTY_SHA256,
        "submission.json": EMPTY_SHA256,
        csv: ARM_CSV_HEADER_SHA256,
        "m5out/stats.txt": EMPTY_SHA256,
        "m5out/config.ini": EMPTY_SHA256,
        "m5out/config.json": EMPTY_SHA256,
    }


def validate_reserved_identity(value, expected_path, label):
    exact_keys(value, ("path", "device", "inode"), label)
    if (
        pathlib.Path(value["path"]).resolve()
        != pathlib.Path(expected_path).resolve()
        or not isinstance(value["device"], int)
        or value["device"] < 0
        or not isinstance(value["inode"], int)
        or value["inode"] <= 0
        or regular_identity(expected_path) != value
    ):
        raise RuntimeError(f"{label} reserved identity mismatch")


def validate_arm_execution_evidence(root, case, arm):
    """Bind reserved outputs/receipts to the frozen wrapper and gem5 argv."""
    root = pathlib.Path(root).resolve()
    wrapper = artifact(
        arm["wrapper"], "service-owned arm wrapper", ARM_WRAPPER
    )
    if arm["wrapper"]["sha256"] != ARM_WRAPPER_SHA256:
        raise RuntimeError("arm wrapper digest is not the reviewed digest")
    expected_gem5 = case_command(CANONICAL_GEM5, root, case)
    expected_wrapper = arm_wrapper_argv(root, expected_gem5)
    if (
        arm["gem5_argv"] != expected_gem5
        or arm["gem5_argv_sha256"] != json_sha256(expected_gem5)
        or arm["wrapper_argv"] != expected_wrapper
        or arm["wrapper_argv_sha256"] != json_sha256(expected_wrapper)
    ):
        raise RuntimeError("arm wrapper/gem5 argv contract mismatch")

    evidence_root = root / ARM_EVIDENCE_DIRECTORY
    launch_path = evidence_root / "arm-launch.json"
    ownership_path = evidence_root / "arm-output-ownership.json"
    terminal_path = evidence_root / "arm-terminal.json"
    launch = read_json(launch_path)
    ownership = read_json(ownership_path)
    terminal = read_json(terminal_path)
    exact_keys(
        ownership,
        (
            "schema",
            "status",
            "arm_root",
            "evidence_root",
            "wrapper",
            "wrapper_sha256",
            "wrapper_pid",
            "wrapper_proc_start_ticks",
            "wrapper_argv_sha256",
            "gem5_argv_sha256",
            "outputs",
            "receipts",
        ),
        "arm output ownership",
    )
    exact_keys(
        launch,
        (
            "schema",
            "status",
            "arm_root",
            "evidence_root",
            "wrapper",
            "wrapper_sha256",
            "wrapper_pid",
            "wrapper_proc_start_ticks",
            "wrapper_argv",
            "wrapper_argv_sha256",
            "gem5_argv",
            "gem5_argv_sha256",
            "output_ownership",
        ),
        "arm launch evidence",
    )
    if (
        ownership["schema"] != ARM_OWNERSHIP_SCHEMA
        or ownership["status"] != "reserved_before_child"
        or launch["schema"] != ARM_LAUNCH_SCHEMA
        or launch["status"] != "child_launch_authorized"
        or ownership["arm_root"] != str(root)
        or launch["arm_root"] != str(root)
        or ownership["evidence_root"] != str(evidence_root)
        or launch["evidence_root"] != str(evidence_root)
        or pathlib.Path(ownership["wrapper"]).resolve() != wrapper
        or pathlib.Path(launch["wrapper"]).resolve() != wrapper
        or ownership["wrapper_sha256"] != ARM_WRAPPER_SHA256
        or launch["wrapper_sha256"] != ARM_WRAPPER_SHA256
        or not isinstance(launch["wrapper_pid"], int)
        or launch["wrapper_pid"] <= 0
        or launch["wrapper_pid"] != ownership["wrapper_pid"]
        or not isinstance(launch["wrapper_proc_start_ticks"], str)
        or not re.fullmatch(r"[1-9][0-9]*", launch["wrapper_proc_start_ticks"])
        or launch["wrapper_proc_start_ticks"]
        != ownership["wrapper_proc_start_ticks"]
        or launch["wrapper_argv"] != arm["wrapper_argv"]
        or launch["wrapper_argv_sha256"] != arm["wrapper_argv_sha256"]
        or ownership["wrapper_argv_sha256"] != arm["wrapper_argv_sha256"]
        or launch["gem5_argv"] != arm["gem5_argv"]
        or launch["gem5_argv_sha256"] != arm["gem5_argv_sha256"]
        or ownership["gem5_argv_sha256"] != arm["gem5_argv_sha256"]
    ):
        raise RuntimeError("arm launch/ownership identity mismatch")
    artifact(
        launch["output_ownership"],
        "arm output ownership receipt",
        ownership_path,
    )
    expected_outputs = reserved_output_initial_sha256(root, case)
    if set(ownership["outputs"]) != set(expected_outputs):
        raise RuntimeError("arm reserved output set mismatch")
    for relative, initial_sha256 in expected_outputs.items():
        value = ownership["outputs"][relative]
        exact_keys(
            value,
            ("path", "device", "inode", "initial_sha256"),
            f"reserved output {relative}",
        )
        identity = {key: value[key] for key in ("path", "device", "inode")}
        validate_reserved_identity(
            identity, root / relative, f"reserved output {relative}"
        )
        if value["initial_sha256"] != initial_sha256:
            raise RuntimeError(
                f"reserved output {relative} initial hash mismatch"
            )
    expected_receipts = {
        "arm-launch.json": launch_path,
        "arm-output-ownership.json": ownership_path,
        "arm-terminal.json": terminal_path,
    }
    if set(ownership["receipts"]) != set(expected_receipts):
        raise RuntimeError("arm reserved receipt set mismatch")
    for name, path in expected_receipts.items():
        validate_reserved_identity(
            ownership["receipts"][name], path, f"reserved receipt {name}"
        )

    exact_keys(
        terminal,
        (
            "schema",
            "status",
            "arm_root",
            "evidence_root",
            "wrapper",
            "wrapper_sha256",
            "wrapper_pid",
            "wrapper_proc_start_ticks",
            "wrapper_argv_sha256",
            "gem5_argv_sha256",
            "launch_evidence",
            "output_ownership",
            "gem5_returncode",
            "wrapper_returncode",
            "outputs",
        ),
        "arm terminal evidence",
    )
    if (
        terminal["schema"] != ARM_TERMINAL_SCHEMA
        or terminal["status"] != "exited"
        or terminal["gem5_returncode"] != 0
        or terminal["wrapper_returncode"] != 0
        or terminal["arm_root"] != str(root)
        or terminal["evidence_root"] != str(evidence_root)
        or pathlib.Path(terminal["wrapper"]).resolve() != wrapper
        or terminal["wrapper_sha256"] != launch["wrapper_sha256"]
        or terminal["wrapper_pid"] != launch["wrapper_pid"]
        or terminal["wrapper_proc_start_ticks"]
        != launch["wrapper_proc_start_ticks"]
        or terminal["wrapper_argv_sha256"] != launch["wrapper_argv_sha256"]
        or terminal["gem5_argv_sha256"] != launch["gem5_argv_sha256"]
        or terminal["output_ownership"] != launch["output_ownership"]
        or set(terminal["outputs"]) != set(expected_outputs)
    ):
        raise RuntimeError("arm terminal wrapper/return evidence mismatch")
    artifact(terminal["launch_evidence"], "arm launch receipt", launch_path)
    artifact(
        terminal["output_ownership"],
        "arm output ownership receipt",
        ownership_path,
    )
    for relative, reserved in ownership["outputs"].items():
        observed = terminal["outputs"][relative]
        exact_keys(
            observed,
            (
                "path",
                "device",
                "inode",
                "sha256",
                "reservation_identity_match",
            ),
            f"terminal output {relative}",
        )
        identity = {key: observed[key] for key in ("path", "device", "inode")}
        if observed["reservation_identity_match"] is not True or identity != {
            key: reserved[key] for key in ("path", "device", "inode")
        }:
            raise RuntimeError(f"terminal output {relative} identity mismatch")
        verify_hash(
            root / relative,
            observed["sha256"],
            f"terminal output {relative}",
        )
    csv_path = root / f"{LABEL_PREFIX}_{case}.csv"
    with csv_path.open("rb") as stream:
        if stream.read(len(ARM_CSV_HEADER)) != ARM_CSV_HEADER:
            raise RuntimeError("reserved CSV header binding mismatch")
    return {
        "wrapper_sha256": ARM_WRAPPER_SHA256,
        "wrapper_argv_sha256": arm["wrapper_argv_sha256"],
        "gem5_argv_sha256": arm["gem5_argv_sha256"],
        "wrapper_pid": launch["wrapper_pid"],
        "wrapper_proc_start_ticks": launch["wrapper_proc_start_ticks"],
        "launch_sha256": sha256(launch_path),
        "ownership_sha256": sha256(ownership_path),
        "terminal_sha256": sha256(terminal_path),
        "reserved_output_count": len(expected_outputs),
        "reserved_receipt_count": len(expected_receipts),
    }


def analyze_arm(
    root,
    case,
    contract_path,
    contract_digest,
    allow_descriptor_callback_restart=False,
):
    contract_path = verify_hash(
        contract_path, contract_digest, "frozen contract"
    )
    contract = read_json(contract_path)
    if (
        not isinstance(contract, dict)
        or set(contract) != CONTRACT_FIELDS
        or contract.get("schema") != SCHEMA_CONTRACT
    ):
        raise RuntimeError("arm is not bound to an unaltered v16 contract")
    harness_identity = contract_harness_identity(
        contract,
        allow_external_producer=allow_descriptor_callback_restart,
    )
    campaign = pathlib.Path(contract.get("campaign_root", ".")).resolve()
    if (
        pathlib.Path(contract["instrumented_build_proof"]).resolve()
        != BUILD_PROOF_PATH.resolve()
        or contract["instrumented_build_proof_schema"] != SCHEMA_BUILD_PROOF
    ):
        raise RuntimeError(
            "arm contract does not bind the exact v19 proof path/schema"
        )
    gem5 = verify_hash(
        CANONICAL_GEM5, contract["gem5_sha256"], "canonical gem5"
    )
    read_build_proof(
        contract["instrumented_build_proof"],
        contract["instrumented_build_proof_sha256"],
        gem5,
        contract["gem5_sha256"],
    )
    if contract_path != campaign / CONTRACT_FILENAME or contract != (
        frozen_producer_expected_contract(contract, campaign)
        if allow_descriptor_callback_restart
        else expected_contract(
            campaign,
            pathlib.Path(contract.get("instrumented_build_proof", ".")),
            contract.get("instrumented_build_proof_sha256", ""),
            contract.get("gem5_sha256", ""),
        )
    ):
        raise RuntimeError("arm is not bound to an unaltered v16 contract")
    root, arm = pathlib.Path(root).resolve(), contract["arms"].get(case, {})
    if str(root) != arm.get("root") or arm.get("gem5_argv") != case_command(
        CANONICAL_GEM5, root, case
    ):
        raise RuntimeError("arm command/root binding mismatch")
    missing = [
        str(root / name)
        for name in RAW_FILES + ARM_EVIDENCE_FILES
        if not (root / name).is_file()
    ]
    csv = root / f"{LABEL_PREFIX}_{case}.csv"
    if not csv.is_file():
        missing.append(str(csv))
    if missing:
        raise RuntimeError("missing raw evidence: " + ", ".join(missing))
    execution = validate_arm_execution_evidence(root, case, arm)
    gem5_text = (root / "gem5.stdout").read_text(
        encoding="utf-8", errors="replace"
    )
    app_text = (root / "app.stdout").read_text(
        encoding="utf-8", errors="replace"
    )
    combined = (
        gem5_text
        + "\n"
        + (root / "gem5.stderr").read_text(encoding="utf-8", errors="replace")
    )
    if (
        gem5_text.count("LANLMAA_UMT_INGRESS_TERMINAL code=0") != 1
        or app_text.count("RESULT CHECK PASSED:") != 1
        or re.search(r"(?im)^(?:fatal|panic):", combined)
    ):
        raise RuntimeError("terminal/correctness/fatal gate failed")
    submission = validate_submission(read_json(root / "submission.json"), case)
    mechanism, stats = validate_trace(
        parse_debug(root / "debug.log"),
        case,
        descriptor_callback_restart_epochs=(
            canonical_descriptor_callback_restart_epochs(root / "gem5.stderr")
            if allow_descriptor_callback_restart
            else None
        ),
    ), parse_stats(root / "m5out/stats.txt")
    if any(name not in stats for name in WORK_COUNTERS):
        raise RuntimeError("missing required MAA counters")
    selected, other = (
        ("D32", "D64") if CASES[case]["abi"] == "D32" else ("D64", "D32")
    )
    if (
        stats[f"descriptorUmt{selected}Descriptors"]
        != submission["wave_calls"]
        or stats[f"descriptorUmt{other}Descriptors"] != 0
        or stats["descriptorUmtGroupsLoaded"] != submission["submitted_groups"]
        or stats["descriptorUmtInputReads"]
        != submission["submitted_groups"] * 16
    ):
        raise RuntimeError("exact D32/D64/group/input counter gate failed")
    return {
        "schema": SCHEMA_ARM_REPORT,
        "status": "passed",
        "case": case,
        "contract": str(contract_path),
        "contract_sha256": contract_digest,
        "gem5_argv_sha256": arm["gem5_argv_sha256"],
        "wrapper_argv_sha256": arm["wrapper_argv_sha256"],
        "native_binary_sha256": ADAPTIVE_NATIVE_SHA256,
        "harness_identity": harness_identity,
        "execution": execution,
        "mechanism": mechanism,
        "source_bank_pressure": mechanism["source_bank_pressure"],
        "submission": submission,
        "observed_work": {name: stats[name] for name in WORK_COUNTERS},
        "raw_sha256": {
            **{
                name.replace("/", "_"): sha256(root / name)
                for name in RAW_FILES + ARM_EVIDENCE_FILES
            },
            "csv_sha256": sha256(csv),
        },
        "claim_boundary": "No simTicks comparison, speedup, or promotion.",
    }


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    build_plan = sub.add_parser("dry-build-plan")
    for name in ("campaign-root", "output"):
        build_plan.add_argument("--" + name, required=True)
    freeze = sub.add_parser("freeze-contract")
    for name in (
        "campaign-root",
        "output",
        "gem5",
        "gem5-sha256",
        "instrumented-build-proof",
        "instrumented-build-proof-sha256",
    ):
        freeze.add_argument("--" + name, required=True)
    dry = sub.add_parser("dry-dispatch")
    for name in ("contract", "contract-sha256", "campaign-root", "output"):
        dry.add_argument("--" + name, required=True)
    arm = sub.add_parser("analyze-arm")
    for name in ("root", "case", "contract", "contract-sha256", "output"):
        arm.add_argument("--" + name, required=True)
    arm._option_string_actions["--case"].choices = CASES
    args = parser.parse_args()
    result = (
        dry_build_plan(args.campaign_root, args.output)
        if args.action == "dry-build-plan"
        else freeze_contract(args)
        if args.action == "freeze-contract"
        else dispatch_plan(
            args.contract,
            args.contract_sha256,
            args.campaign_root,
            args.output,
        )
        if args.action == "dry-dispatch"
        else analyze_arm(
            args.root, args.case, args.contract, args.contract_sha256
        )
    )
    if args.action == "analyze-arm":
        atomic_no_clobber(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
