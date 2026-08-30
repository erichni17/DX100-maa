#!/usr/bin/env python3
"""Fail-closed v5 opcode-11 UMT ingress evidence harness.

This program only freezes, validates, and records launch commands.  It never
builds, invokes systemd, or executes gem5.  A future launcher must first
produce the separately validated, canonical-source build proof.
"""

import argparse
import hashlib
import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
TRACE_BUILD_DEFINE = "LANL_MAA_UMT_INGRESS_TRACE_TEST"
LABEL_PREFIX = "lanl_maa_umt_ingress_micro"
SCHEMA_BUILD_PROOF = "lanl-maa-umt-ingress-instrumented-gem5-build-proof-v5"
SCHEMA_SUBMISSION = "umt-lanl-maa-submission-v1"
CANONICAL_SOURCE_ROOT = "/data1/nier/worktrees/DX100-umt-trace-replay-20260830"
CANONICAL_SOURCE = pathlib.Path(CANONICAL_SOURCE_ROOT)
CANONICAL_SOURCE_COMMIT = "6d36a1a4f0d5bdbfb3b80a28c6964fc72539d69a"
CANONICAL_SOURCE_TREE = "3359187ec074e45aef495aea9675cfedf757eb24"
CANONICAL_GEM5 = CANONICAL_SOURCE / "build/X86_UMT_T32_W2/gem5.opt"
BUILD_UNIT = "umt-ingress-trace-build-v5-20260830.service"
BUILD_ARGV = (
    "/usr/bin/scons",
    "--ignore-style",
    "build/X86_UMT_T32_W2/gem5.opt",
    "-j4",
    "CCFLAGS_EXTRA=-DLANL_MAA_UMT_INGRESS_TRACE_TEST=1",
)
BUILD_ENVIRONMENT = {}
BUILD_WRAPPER = ROOT / "tests/lanl_maa/run_umt_ingress_build_attestation.py"
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
JOURNAL_TERMINAL_PROTOCOL = "LANL_MAA_UMT_INGRESS_BUILD_ATTESTATION_V5"
DISPATCH_PROPERTIES = (
    ("CPUQuota", "400%"),
    ("CPUWeight", "1000"),
    ("MemoryHigh", str(14 * 1024**3)),
    ("MemoryMax", str(16 * 1024**3)),
    ("MemorySwapMax", "0"),
    ("RuntimeMaxUSec", "4h"),
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
INSTRUMENTATION_SOURCES = {
    "src/mem/LANLMAA/UmtOrderedWaveIngressTrace.hh": "31b46207da10d149c59fa5841085458810f037b6a59105ff5fee41b376c48189",
    "src/mem/LANLMAA/UmtOrderedWaveStreamState.hh": "d783907dd26ec671d6ba4a779719e19eadc75098ab25ba0fd3457cf68438b5c8",
    "src/mem/LANLMAA/lanl_maa.hh": "0867579688c902f04b86d0fdce0b896f60b61031d61410fbd4789385b4cd5b9a",
    "src/mem/LANLMAA/lanl_maa.cc": "dfeb641477a9bf8820b32e8b2231efdc7a968504f34da9b6146e7ed5834e714b",
    "tests/lanl_maa/umt_production_ingress_trace_test.cc": "1364d75af5b1305775b5d0dceeda5a1e1d4dd188d241b1b3db9667684cf3b436",
    "tests/lanl_maa/run_umt_production_ingress_trace_gate.py": "5460448694b20d83e3965405545803ec7b5313440ff5a5d0cd5381229c170cc6",
}
ABI_CONTRACTS = {
    "D32": {"version": 4, "max_groups": 32},
    "D64": {"version": 5, "max_groups": 64},
}
CASES = {
    "d32-g16": {"abi": "D32", "groups": 16, "mode": "wave_d32"},
    "d32-g31": {"abi": "D32", "groups": 31, "mode": "wave_d32"},
    "d32-g32": {"abi": "D32", "groups": 32, "mode": "wave_d32"},
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
        "harness_source_commit",
        "gem5",
        "gem5_sha256",
        "instrumented_build_proof",
        "instrumented_build_proof_sha256",
        "required_define",
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


def atomic_no_clobber(path, value):
    path = pathlib.Path(path)
    if path.exists():
        raise RuntimeError(f"refusing to overwrite evidence: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


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


def git_output(cwd, *argv):
    return subprocess.check_output(["git", *argv], cwd=cwd, text=True).strip()


def artifact(value, label, required_path=None):
    exact_keys(value, ("path", "sha256"), label)
    path = verify_hash(value["path"], value["sha256"], label)
    if (
        required_path is not None
        and path != pathlib.Path(required_path).resolve()
    ):
        raise RuntimeError(f"{label} path is not canonical")
    return path


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


def wrapper_command(evidence_dir):
    return (
        "/usr/bin/python3",
        str(BUILD_WRAPPER),
        "--unit",
        BUILD_UNIT,
        "--source",
        str(CANONICAL_SOURCE),
        "--evidence-dir",
        str(pathlib.Path(evidence_dir).resolve()),
    )


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
        "schema": "lanl-maa-umt-ingress-build-attestation-v5",
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
        manager_pair = (
            record.get("USER_UNIT"),
            record.get("USER_INVOCATION_ID"),
        )
        expected = (BUILD_UNIT.encode(), invocation.encode())
        for pair in (service_pair, manager_pair):
            if any(value is not None for value in pair) and pair != expected:
                raise RuntimeError(
                    "journal export has wrong/incomplete unit or invocation IDs"
                )
        bound = service_pair == expected or manager_pair == expected
        message = record.get("MESSAGE", b"")
        if JOURNAL_TERMINAL_PROTOCOL.encode() in message:
            if (
                not bound
                or service_pair != expected
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
            "trace_define",
            "instrumentation_source_sha256",
            "build_returncode",
            "required_relink_observed",
            "build_stdout",
            "build_stderr",
            "build_artifacts",
            "build_invocation",
            "observer_gate",
        ),
        "instrumented-build proof",
    )
    if proof["schema"] != SCHEMA_BUILD_PROOF or proof["status"] != "passed":
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
        raise RuntimeError(
            "build proof does not bind the canonical instrumented source"
        )
    verify_canonical_source()
    if (
        pathlib.Path(proof["gem5"]).resolve() != CANONICAL_GEM5.resolve()
        or pathlib.Path(proof["gem5"]).resolve() != gem5
        or proof["gem5_sha256"] != gem5_digest
    ):
        raise RuntimeError("build proof does not bind canonical T32/W2 gem5")
    if (
        pathlib.Path(proof["build_cwd"]).resolve()
        != CANONICAL_SOURCE.resolve()
        or tuple(proof["build_argv"]) != BUILD_ARGV
        or proof["build_environment"] != BUILD_ENVIRONMENT
        or proof["trace_define"] != TRACE_BUILD_DEFINE
        or proof["instrumentation_source_sha256"] != INSTRUMENTATION_SOURCES
        or proof["build_returncode"] != 0
        or proof["required_relink_observed"] is not True
    ):
        raise RuntimeError(
            "build proof command/environment/source binding mismatch"
        )
    if proof["producer"] != "systemd-build-proof-v5-service-wrapper":
        raise RuntimeError("build proof producer is not accepted")
    artifact(proof["build_stdout"], "build stdout")
    artifact(proof["build_stderr"], "build stderr")
    exact_keys(
        proof["build_artifacts"],
        ("gem5", "config_hh", "config_cc"),
        "build artifacts",
    )
    artifact(proof["build_artifacts"]["gem5"], "built gem5", CANONICAL_GEM5)
    artifact(
        proof["build_artifacts"]["config_hh"],
        "build config.hh",
        CANONICAL_SOURCE / "build/X86_UMT_T32_W2/config.hh",
    )
    artifact(
        proof["build_artifacts"]["config_cc"],
        "build config.cc",
        CANONICAL_SOURCE / "build/X86_UMT_T32_W2/config.cc",
    )
    inv = proof["build_invocation"]
    exact_keys(
        inv,
        (
            "unit",
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
        ),
        "build systemd invocation",
    )
    wrapper = artifact(
        inv["wrapper"], "service-owned build wrapper", BUILD_WRAPPER
    )
    if wrapper != BUILD_WRAPPER.resolve():
        raise RuntimeError("build wrapper path is not the reviewed wrapper")
    attestation = artifact(inv["wrapper_attestation"], "wrapper attestation")
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
            "build_argv",
            "build_environment",
            "build_returncode",
            "required_relink_observed",
            "instrumentation_source_sha256",
            "build_artifacts",
            "compiled_binary_markers",
            "observer_gate",
            "logs",
        ),
        "wrapper attestation",
    )
    evidence_dir = attestation.parent
    command = wrapper_command(evidence_dir)
    if tuple(inv["wrapper_command"]) != command:
        raise RuntimeError(
            "service wrapper command differs from the frozen invocation"
        )
    live_show = artifact(inv["live_systemd_show"], "live systemd-show")
    terminal_show = artifact(
        inv["terminal_systemd_show"], "terminal systemd-show"
    )
    receipt = artifact(
        inv["live_process_start_receipt"], "live process start receipt"
    )
    live = parse_systemd_show(live_show, "live systemd-show")
    terminal = parse_systemd_show(terminal_show, "terminal systemd-show")
    if (
        inv["unit"] != BUILD_UNIT
        or tuple(inv["show_command"]) != BUILD_SHOW_COMMAND
        or tuple(inv["journal_command"]) != BUILD_JOURNAL_COMMAND
        or inv["journal_terminal_protocol"] != JOURNAL_TERMINAL_PROTOCOL
        or not re.fullmatch(r"[1-9][0-9]*", live["MainPID"])
    ):
        raise RuntimeError("build systemd invocation binding mismatch")
    pid = int(live["MainPID"])
    invocation = live["InvocationID"]
    process = parse_proc_start_receipt(
        receipt,
        pid,
        invocation,
        live["ExecMainStartTimestampMonotonic"],
    )
    validate_show_snapshot(
        live,
        phase="live",
        pid=pid,
        proc_start_ticks=process["proc_start_ticks"],
        invocation=invocation,
        command=command,
    )
    validate_show_snapshot(
        terminal,
        phase="terminal",
        pid=pid,
        proc_start_ticks=process["proc_start_ticks"],
        invocation=invocation,
        command=command,
    )
    if (
        attest["schema"] != "lanl-maa-umt-ingress-build-attestation-v5"
        or attest["unit"] != BUILD_UNIT
        or attest["invocation_id"] != invocation
        or attest["wrapper_pid"] != pid
        or attest["wrapper_proc_start_ticks"] != process["proc_start_ticks"]
        or attest["status"] != "passed"
        or tuple(attest["build_argv"]) != BUILD_ARGV
        or attest["build_environment"] != BUILD_ENVIRONMENT
        or attest["build_returncode"] != 0
        or attest["required_relink_observed"] is not True
        or attest["instrumentation_source_sha256"] != INSTRUMENTATION_SOURCES
        or attest["compiled_binary_markers"]
        != ["UMT_INGRESS kind=", "d64_hold cycle="]
    ):
        raise RuntimeError(
            "wrapper attestation identity/build/source binding mismatch"
        )
    exact_keys(
        attest["build_artifacts"],
        ("gem5", "config_hh", "config_cc"),
        "wrapper build artifacts",
    )
    if attest["build_artifacts"] != {
        key: value["sha256"] for key, value in proof["build_artifacts"].items()
    }:
        raise RuntimeError(
            "wrapper target/config hashes differ from proof artifacts"
        )
    if attest["build_artifacts"]["gem5"] != gem5_digest:
        raise RuntimeError("wrapper target hash differs from canonical gem5")
    exact_keys(
        attest["observer_gate"],
        ("command", "returncode", "report_sha256"),
        "wrapper observer gate",
    )
    if (
            tuple(attest["observer_gate"]["command"])
            != (
                "/usr/bin/python3",
            str(
                CANONICAL_SOURCE
                / "tests/lanl_maa/run_umt_production_ingress_trace_gate.py"
            ),
            "--cxx",
            "g++",
        )
        or attest["observer_gate"]["returncode"] != 0
    ):
        raise RuntimeError("wrapper observer gate binding mismatch")
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
    gate_script = (
        CANONICAL_SOURCE
        / "tests/lanl_maa/run_umt_production_ingress_trace_gate.py"
    )
    if (
        gate["status"] != "passed"
        or tuple(gate["command"])
        != ("/usr/bin/python3", str(gate_script), "--cxx", "g++")
        or gate["input_source_sha256"] != INSTRUMENTATION_SOURCES
        or pathlib.Path(gate["binary"]).resolve() != CANONICAL_GEM5.resolve()
        or gate["binary_sha256"] != gem5_digest
    ):
        raise RuntimeError(
            "observer gate command/input/source/binary binding mismatch"
        )
    artifact(gate["stdout"], "observer gate stdout")
    artifact(gate["stderr"], "observer gate stderr")
    transcript = artifact(gate["transcript"], "observer gate transcript")
    report = read_json(artifact(gate["report"], "observer gate report"))
    if (
        report.get("schema") != "lanl-maa-umt-production-ingress-trace-v2"
        or report.get("status") != "passed"
        or report.get("cells")
        != [
            {
                "tokens": t,
                "issue_width": w,
                "waiter_counts": [1, 7, 8],
                "abi_boundaries": ["D32", "D64"],
                "two_lane_serialization": "rejected_by_trace_difference",
                "default_off": "compiled_without_observer_macro",
            }
            for t, w in ((24, 1), (24, 2), (32, 1), (32, 2))
        ]
        or "status=0/SUCCESS"
        not in transcript.read_text(
            encoding="utf-8", errors="strict"
        ).splitlines()
    ):
        raise RuntimeError(
            "observer gate report/transcript semantics mismatch"
        )
    return path


def case_command(gem5, root, case):
    value = CASES[case]
    runner = ROOT / "tests/lanl_maa/umt_ingress_micro_process_cpu.py"
    return [
        str(gem5),
        "--listener-mode=off",
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


def expected_contract(campaign, proof, proof_digest, gem5_digest):
    arms = {}
    for name in CASES:
        root = campaign / "arms" / name
        command = case_command(CANONICAL_GEM5, root, name)
        arms[name] = {
            "root": str(root),
            "unit": f"umt-ingress-micro-v5-{name}-20260830.service",
            "command": command,
            "command_sha256": json_sha256(command),
            "binary_sha256": ADAPTIVE_NATIVE_SHA256,
        }
    return {
        "schema": "lanl-maa-umt-ingress-contract-v5",
        "status": "frozen_before_dispatch",
        "campaign_root": str(campaign),
        "harness_source_commit": git_output(ROOT, "rev-parse", "HEAD"),
        "gem5": str(CANONICAL_GEM5),
        "gem5_sha256": gem5_digest,
        "instrumented_build_proof": str(proof),
        "instrumented_build_proof_sha256": proof_digest,
        "required_define": TRACE_BUILD_DEFINE,
        "native_identity": verify_native_identity(),
        "cases": CASES,
        "arms": arms,
        "resource_policy": RESOURCE_POLICY,
        "predecessors": {
            "v1": {"review_status": "rejected", "reuse": "forbidden"},
            "v2": {"review_status": "rejected", "reuse": "forbidden"},
        },
        "claim_boundary": "Correctness and ingress mechanism only; simTicks are not compared or promoted.",
    }


def systemd_run_command(unit, command):
    """Construct an arm launch from the one frozen policy mapping.

    CPUQuotaPerSecUSec is a systemd *show* property (microseconds per
    second); `systemd-run` instead consumes CPUQuota.  Keep that conversion
    explicit and audited here rather than deriving option names by rewriting
    arbitrary property strings.
    """
    if RESOURCE_POLICY != {
        "CPUQuotaPerSecUSec": "4s",
        "CPUWeight": "1000",
        "MemoryHigh": str(14 * 1024**3),
        "MemoryMax": str(16 * 1024**3),
        "MemorySwapMax": "0",
        "RuntimeMaxUSec": "4h",
    } or DISPATCH_PROPERTIES != (
        ("CPUQuota", "400%"),
        ("CPUWeight", "1000"),
        ("MemoryHigh", str(14 * 1024**3)),
        ("MemoryMax", str(16 * 1024**3)),
        ("MemorySwapMax", "0"),
        ("RuntimeMaxUSec", "4h"),
    ):
        raise RuntimeError("frozen systemd resource mapping is altered")
    return [
        "systemd-run",
        "--user",
        "--collect",
        f"--unit={unit}",
        *[f"--property={key}={value}" for key, value in DISPATCH_PROPERTIES],
        *command,
    ]


def freeze_contract(args):
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
    if campaign.exists() or output != campaign / "ingress-contract-v5.json":
        raise RuntimeError(
            "v5 contract must be a fresh campaign/ingress-contract-v5.json"
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
    if contract_path != campaign / "ingress-contract-v5.json":
        raise RuntimeError(
            "externally fixed campaign/contract identity mismatch"
        )
    contract = read_json(contract_path)
    if (
        not isinstance(contract, dict)
        or set(contract) != CONTRACT_FIELDS
        or contract.get("schema") != "lanl-maa-umt-ingress-contract-v5"
    ):
        raise RuntimeError(
            "v5 contract semantics, resources, units, roots, or self-hash binding altered"
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
            "v5 contract semantics, resources, units, roots, or self-hash binding altered"
        )
    output = pathlib.Path(output).resolve()
    if output != campaign / "identity/ingress-dry-dispatch-v5.json":
        raise RuntimeError("v5 dry dispatch output identity mismatch")
    commands = {
        name: systemd_run_command(arm["unit"], arm["command"])
        for name, arm in contract["arms"].items()
    }
    plan = {
        "schema": "lanl-maa-umt-ingress-dispatch-plan-v5",
        "status": "dry_only_not_dispatched",
        "campaign_root": str(campaign),
        "contract": str(contract_path),
        "contract_sha256": digest,
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


def validate_trace(events, case):
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
    groups, active, closed = {}, None, set()
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
        if active is not None and callback != active:
            closed.add(active)
        if callback in closed and callback != active:
            raise RuntimeError("callback sequence reappears after closure")
        active = callback
        groups.setdefault(callback, []).append(item)
    if sorted(groups) != list(range(1, len(groups) + 1)):
        raise RuntimeError("callback sequence is not contiguous")
    for items in groups.values():
        if (
            [x["lane"] for x in items] != list(range(len(items)))
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
    waits = [x["waiters"] for x in callbacks]
    if case == "d32-g31" and not any(
        waits[i : i + 2] == [7, 1] for i in range(len(waits) - 1)
    ):
        raise RuntimeError("G31 lacks chronological 7+1 boundary")
    if case in ("d32-g32", "d64-g32") and max(waits) != 8:
        raise RuntimeError("G32 lacks exact eight-waiter response")
    if spec["abi"] == "D32":
        if not lines or any(
            x["abi_label"] != "d32" or x["kind"] != "release" for x in lines
        ):
            raise RuntimeError("D32 line witness invalid")
    else:
        if (
            len(lines) != 8
            or [x["abi_label"] for x in lines] != ["d64"] * 8
            or [x["kind"] for x in lines] != ["hold"] * 7 + ["release"]
            or [x["waiters"] for x in lines] != list(range(1, 8)) + [8]
        ):
            raise RuntimeError(
                "D64 must chronologically hold 1..7 then release 8"
            )
        first, release = lines[0], lines[-1]
        if (
            any(
                (x["line"], x["stage"], x["group"], x["corner"])
                != (
                    first["line"],
                    first["stage"],
                    first["group"],
                    first["corner"],
                )
                for x in lines
            )
            or release["cycle"] <= lines[-2]["cycle"]
        ):
            raise RuntimeError("D64 hold/release identity mismatch")
    return {
        "callbacks": len(groups),
        "records": len(events),
        "max_lanes": max(map(len, groups.values())),
        "max_waiters": max(waits),
        "source_callbacks": sum(x["kind"] == "source" for x in callbacks),
        "denominator_callbacks": sum(
            x["kind"] == "denominator" for x in callbacks
        ),
        "d64_holds": sum(x["kind"] == "hold" for x in lines),
        "releases": sum(x["kind"] == "release" for x in lines),
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


def analyze_arm(root, case, contract_path, contract_digest):
    contract_path = verify_hash(
        contract_path, contract_digest, "frozen contract"
    )
    contract = read_json(contract_path)
    if (
        not isinstance(contract, dict)
        or set(contract) != CONTRACT_FIELDS
        or contract.get("schema") != "lanl-maa-umt-ingress-contract-v5"
    ):
        raise RuntimeError("arm is not bound to an unaltered v5 contract")
    campaign = pathlib.Path(contract.get("campaign_root", ".")).resolve()
    gem5 = verify_hash(
        CANONICAL_GEM5, contract["gem5_sha256"], "canonical gem5"
    )
    read_build_proof(
        contract["instrumented_build_proof"],
        contract["instrumented_build_proof_sha256"],
        gem5,
        contract["gem5_sha256"],
    )
    if (
        contract_path != campaign / "ingress-contract-v5.json"
        or contract
        != expected_contract(
            campaign,
            pathlib.Path(contract.get("instrumented_build_proof", ".")),
            contract.get("instrumented_build_proof_sha256", ""),
            contract.get("gem5_sha256", ""),
        )
    ):
        raise RuntimeError("arm is not bound to an unaltered v5 contract")
    root, arm = pathlib.Path(root).resolve(), contract["arms"].get(case, {})
    if str(root) != arm.get("root") or arm.get("command") != case_command(
        CANONICAL_GEM5, root, case
    ):
        raise RuntimeError("arm command/root binding mismatch")
    missing = [
        str(root / name) for name in RAW_FILES if not (root / name).is_file()
    ]
    csv = root / f"{LABEL_PREFIX}_{case}.csv"
    if not csv.is_file():
        missing.append(str(csv))
    if missing:
        raise RuntimeError("missing raw evidence: " + ", ".join(missing))
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
        parse_debug(root / "debug.log"), case
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
        "schema": "lanl-maa-umt-ingress-arm-report-v5",
        "status": "passed",
        "case": case,
        "contract": str(contract_path),
        "contract_sha256": contract_digest,
        "command_sha256": arm["command_sha256"],
        "native_binary_sha256": ADAPTIVE_NATIVE_SHA256,
        "mechanism": mechanism,
        "submission": submission,
        "observed_work": {name: stats[name] for name in WORK_COUNTERS},
        "raw_sha256": {
            **{
                name.replace("/", "_"): sha256(root / name)
                for name in RAW_FILES
            },
            "csv_sha256": sha256(csv),
        },
        "claim_boundary": "No simTicks comparison, speedup, or promotion.",
    }


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
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
        freeze_contract(args)
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
