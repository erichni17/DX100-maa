#!/usr/bin/env python3
"""Postprocess one terminal Gate-B arm from one immutable trace snapshot."""

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import umt_ingress_micro_harness as ingress
import umt_pki4_gate_b_live_harness as live

SOURCE = live.SOURCE
LIFECYCLE_NORMALIZER = (
    SOURCE / "tests/lanl_maa/umt_pki4_lifecycle_normalizer.py"
)
LIFECYCLE_NORMALIZER_SHA256 = (
    "6adc78f3a41a6a0f088622aeb3a7abbcd7456c531f329dce1086405fcbefebdc"
)
CONFORMANCE_NORMALIZER = (
    SOURCE / "tests/lanl_maa/umt_pki4_conformance_normalizer.py"
)
CONFORMANCE_NORMALIZER_SHA256 = (
    "de2c140c638884aa876756c81be3de832ac14ccb938ee863a69f84a006146fb7"
)
ROUTER = SOURCE / (
    "experiments/lanl_maa_fp64_physical/scripts/umt_pki4_gate_b_schema.py"
)
ROUTER_SHA256 = (
    "a3912db4dfeab162dcc44533d6c84e8abc0489d6212762a1fa5dd059a473cdb2"
)
HOST_REVIEW = pathlib.Path(
    "/data1/nier/dx100-runs/2026-08-31-umt-pki4-gate-b-lifecycle-host-v4/"
    "gate-b-lifecycle-host-independent-review-v4.json"
)
HOST_REVIEW_SHA256 = (
    "4a20183db04f520246785ca1e776e5c802fb117fcd9e37932a136cac87fee2c4"
)
NORMALIZER_262_REVIEW = pathlib.Path(
    "/data1/nier/dx100-runs/2026-08-31-umt-pki4-normalizer-v4-d32-repair/"
    "pki4-normalizer-v4-live-semantics-262ab23e-independent-review-v3.json"
)
NORMALIZER_262_REVIEW_SHA256 = (
    "a03f7ce82864e1ac81f695b9a478c1408e8a7fc5e4ee221c76adb87209fb4360"
)

V3_PREFIX = b"UMT_PKI4_CONFORMANCE "
V4_PREFIX = b"UMT_PKI4_LIFECYCLE "
V3_RAW_SCHEMA = "lanl-maa-umt-pki4-conformance-v3"
V4_RAW_SCHEMA = "lanl-maa-umt-pki4-lifecycle-v1"
V3_CANONICAL_SCHEMA = "lanl-maa-umt-pki4-canonical-stimulus-v3"
V4_CANONICAL_SCHEMA = "lanl-maa-umt-pki4-canonical-stimulus-v4"
SNAPSHOT_NAME = "terminal-validated-gem5.stderr.snapshot"
CHUNK = 1024 * 1024
SCHEMA_REPORT = "lanl-maa-umt-pki4-gate-b-live-arm-report-v25"
SCHEMA_ROUTER_RECEIPT = "lanl-maa-umt-pki4-gate-b-router-receipt-v25"
REQUIRED_PHASES = {
    "token_admission",
    "token_issue",
    "token_completion",
    "token_release",
    "token_reuse",
}
WORK_COUNTERS = live.read_json_nofollow(live.DRY_PLAN)["analysis"][
    "correctness"
]["required_work_counters"]


def fd_sha256(descriptor):
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, CHUNK, offset)
        if not block:
            break
        digest.update(block)
        offset += len(block)
    return digest.hexdigest()


def identity(status):
    return {
        "device": status.st_dev,
        "inode": status.st_ino,
        "bytes": status.st_size,
        "mtime_ns": status.st_mtime_ns,
    }


def open_regular(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        raise RuntimeError(
            f"cannot O_NOFOLLOW-open evidence: {path}"
        ) from error
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RuntimeError(f"evidence is not regular: {path}")
    return descriptor


def verify_open_path(descriptor, path, expected):
    observed = identity(os.fstat(descriptor))
    try:
        path_status = os.stat(path, follow_symlinks=False)
    except FileNotFoundError as error:
        raise RuntimeError(f"evidence path disappeared: {path}") from error
    if (
        observed != {key: expected[key] for key in observed}
        or not stat.S_ISREG(path_status.st_mode)
        or identity(path_status) != observed
        or fd_sha256(descriptor) != expected["sha256"]
    ):
        raise RuntimeError(f"evidence changed after binding: {path}")
    return expected["sha256"]


def verify_provenance():
    live.git_identity(SOURCE, live.SOURCE_COMMIT, live.SOURCE_TREE)
    pins = {
        LIFECYCLE_NORMALIZER: LIFECYCLE_NORMALIZER_SHA256,
        CONFORMANCE_NORMALIZER: CONFORMANCE_NORMALIZER_SHA256,
        ROUTER: ROUTER_SHA256,
        HOST_REVIEW: HOST_REVIEW_SHA256,
        NORMALIZER_262_REVIEW: NORMALIZER_262_REVIEW_SHA256,
    }
    for path, digest in pins.items():
        live.regular_nofollow(path)
        if live.sha256(path) != digest:
            raise RuntimeError(
                f"Gate-B postprocessor provenance changed: {path}"
            )
    return {
        "source_commit": live.SOURCE_COMMIT,
        "source_tree": live.SOURCE_TREE,
        "conformance_normalizer": {
            "path": str(CONFORMANCE_NORMALIZER),
            "sha256": CONFORMANCE_NORMALIZER_SHA256,
        },
        "lifecycle_normalizer_262_bound": {
            "path": str(LIFECYCLE_NORMALIZER),
            "sha256": LIFECYCLE_NORMALIZER_SHA256,
            "independent_review": {
                "path": str(NORMALIZER_262_REVIEW),
                "sha256": NORMALIZER_262_REVIEW_SHA256,
            },
        },
        "gate_b_router_40c": {
            "path": str(ROUTER),
            "sha256": ROUTER_SHA256,
            "independent_host_review": {
                "path": str(HOST_REVIEW),
                "sha256": HOST_REVIEW_SHA256,
            },
        },
    }


def terminal_binding(arm, contract_digest):
    manager_validation = live.validate_manager_terminal(arm, contract_digest)
    service = manager_validation["service"]
    root = pathlib.Path(arm["root"])
    manager_path = root / ".manager-owned/manager-terminal.json"
    terminal_path = root / ".service-owned/arm-terminal.json"
    terminal = service["terminal_value"]
    output = terminal["outputs"].get("gem5.stderr", {})
    source = root / "gem5.stderr"
    if (
        output.get("path") != str(source)
        or output.get("reservation_identity_match") is not True
        or not isinstance(output.get("device"), int)
        or not isinstance(output.get("inode"), int)
        or not re.fullmatch(r"[0-9a-f]{64}", output.get("sha256", ""))
    ):
        raise RuntimeError("terminal gem5.stderr binding is invalid")
    return {
        "source": str(source),
        "device": output["device"],
        "inode": output["inode"],
        "sha256": output["sha256"],
        "service_terminal": {
            "path": str(terminal_path),
            "sha256": live.sha256(terminal_path),
        },
        "manager_terminal": {
            "path": str(manager_path),
            "sha256": live.sha256(manager_path),
        },
        "terminal_value": terminal,
    }


def _write_all(descriptor, raw):
    view = memoryview(raw)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise RuntimeError("snapshot publication made no progress")
        view = view[written:]


def capture_snapshot(arm, contract_digest):
    root = pathlib.Path(arm["root"])
    analysis = root / "analysis/gate-b"
    snapshot = analysis / SNAPSHOT_NAME
    if str(snapshot) != arm["terminal_snapshot"]:
        raise RuntimeError("snapshot path differs from frozen arm contract")
    if snapshot.exists() or snapshot.is_symlink():
        raise RuntimeError("terminal snapshot already exists")
    binding = terminal_binding(arm, contract_digest)
    source = pathlib.Path(binding["source"])
    analysis.mkdir(parents=True, exist_ok=False)
    source_fd = open_regular(source)
    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{snapshot.name}.", suffix=".tmp", dir=analysis
    )
    temporary = pathlib.Path(temporary_name)
    digest = hashlib.sha256()
    try:
        before = identity(os.fstat(source_fd))
        if (before["device"], before["inode"]) != (
            binding["device"],
            binding["inode"],
        ):
            raise RuntimeError("snapshot source identity mismatches terminal")
        while True:
            block = os.read(source_fd, CHUNK)
            if not block:
                break
            digest.update(block)
            _write_all(temporary_fd, block)
        os.fsync(temporary_fd)
        after = identity(os.fstat(source_fd))
        path_status = os.stat(source, follow_symlinks=False)
        if (
            before != after
            or not stat.S_ISREG(path_status.st_mode)
            or identity(path_status) != after
            or digest.hexdigest() != binding["sha256"]
            or os.fstat(temporary_fd).st_size != before["bytes"]
        ):
            raise RuntimeError(
                "snapshot source changed or mismatched terminal"
            )
        temporary_status = os.fstat(temporary_fd)
        os.link(temporary, snapshot, follow_symlinks=False)
        directory = os.open(analysis, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(source_fd)
        os.close(temporary_fd)
        temporary.unlink(missing_ok=True)
    descriptor = open_regular(snapshot)
    try:
        observed = identity(os.fstat(descriptor))
        observed_digest = fd_sha256(descriptor)
    finally:
        os.close(descriptor)
    if (observed["device"], observed["inode"]) != (
        temporary_status.st_dev,
        temporary_status.st_ino,
    ) or observed_digest != binding["sha256"]:
        raise RuntimeError("published terminal snapshot identity is invalid")
    return {
        "path": str(snapshot),
        "sha256": observed_digest,
        **observed,
        "source": {
            "path": str(source),
            **before,
            "terminal_sha256": binding["sha256"],
            "service_terminal": binding["service_terminal"],
            "manager_terminal": binding["manager_terminal"],
        },
    }


def split_prefix_streams(snapshot_fd, snapshot_evidence, v3_path, v4_path):
    for path in (v3_path, v4_path):
        if pathlib.Path(path).exists() or pathlib.Path(path).is_symlink():
            raise RuntimeError(f"split trace output already exists: {path}")
    parent = pathlib.Path(v3_path).parent
    if parent != pathlib.Path(v4_path).parent:
        raise RuntimeError("split trace outputs do not share analysis root")
    temporary_root = pathlib.Path(
        tempfile.mkdtemp(prefix=".gate-b-split-", dir=parent)
    )
    temp_v3, temp_v4 = temporary_root / "v3.raw", temporary_root / "v4.raw"
    counts = {"canonical_v3": 0, "canonical_v4": 0}
    digests = {
        "canonical_v3": hashlib.sha256(),
        "canonical_v4": hashlib.sha256(),
    }
    fatal_count = 0
    try:
        os.lseek(snapshot_fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(snapshot_fd), "rb") as source, temp_v3.open(
            "xb"
        ) as v3, temp_v4.open("xb") as v4:
            for line_number, line in enumerate(source, 1):
                lower = line.lower()
                if re.match(rb"^(?:fatal|panic):", lower):
                    fatal_count += 1
                selected = None
                if line.startswith(V3_PREFIX):
                    selected = ("canonical_v3", V3_PREFIX, V3_RAW_SCHEMA, v3)
                elif line.startswith(V4_PREFIX):
                    selected = ("canonical_v4", V4_PREFIX, V4_RAW_SCHEMA, v4)
                elif b"UMT_PKI4_" in line:
                    raise RuntimeError(
                        f"unknown, embedded, or malformed PKI4 prefix at line {line_number}"
                    )
                if selected is None:
                    continue
                label, prefix, schema, output = selected
                if not line.endswith(b"\n"):
                    raise RuntimeError(
                        f"truncated {label} record at terminal EOF"
                    )
                try:
                    value = json.loads(line[len(prefix) :])
                except json.JSONDecodeError as error:
                    raise RuntimeError(
                        f"invalid {label} JSON at snapshot line {line_number}"
                    ) from error
                if (
                    not isinstance(value, dict)
                    or value.get("schema") != schema
                ):
                    raise RuntimeError(f"{label} raw schema mismatch")
                output.write(line)
                counts[label] += 1
                digests[label].update(line)
            for output in (v3, v4):
                output.flush()
                os.fsync(output.fileno())
        if fatal_count or not all(counts.values()):
            raise RuntimeError(
                "fatal/panic marker or missing dual-prefix stream"
            )
        for temporary, final in (
            (temp_v3, pathlib.Path(v3_path)),
            (temp_v4, pathlib.Path(v4_path)),
        ):
            os.link(temporary, final, follow_symlinks=False)
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
    v3_status, v4_status = os.lstat(v3_path), os.lstat(v4_path)
    return {
        "input_snapshot_sha256": snapshot_evidence["sha256"],
        "canonical_v3": {
            "path": str(v3_path),
            "sha256": live.sha256(v3_path),
            **identity(v3_status),
            "records": counts["canonical_v3"],
            "stream_sha256": digests["canonical_v3"].hexdigest(),
        },
        "canonical_v4": {
            "path": str(v4_path),
            "sha256": live.sha256(v4_path),
            **identity(v4_status),
            "records": counts["canonical_v4"],
            "stream_sha256": digests["canonical_v4"].hexdigest(),
        },
        "fatal_or_panic_count": fatal_count,
    }


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_manifest():
    normalizer = load_module(
        "gate_b_pinned_v3_normalizer", CONFORMANCE_NORMALIZER
    )
    return normalizer.current_source_hashes()


def run_normalizer(program, trace_fd, output, extra=()):
    output = pathlib.Path(output)
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"normalizer output already exists: {output}")
    temporary_root = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent)
    )
    temporary = temporary_root / output.name
    try:
        os.lseek(trace_fd, 0, os.SEEK_SET)
        command = [
            "/usr/bin/python3",
            str(program),
            *extra,
            "--output",
            str(temporary),
        ]
        completed = subprocess.run(
            command,
            cwd=SOURCE,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            pass_fds=(trace_fd,),
            check=False,
        )
        if completed.returncode != 0 or completed.stdout:
            raise RuntimeError(
                "normalizer failed: "
                + completed.stderr.decode(errors="replace")[:1000]
            )
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("normalizer did not create a nonempty output")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(temporary, output, follow_symlinks=False)
        directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
    return {
        "path": str(output),
        "sha256": live.sha256(output),
        "bytes": output.stat().st_size,
        "argv": command,
    }


def validate_queue_reference(document, case, require_c2=True):
    if document.get("schema") != V3_CANONICAL_SCHEMA:
        raise RuntimeError("queue reference input is not canonical-v3")
    expected_events = []
    c1, c2, maximum_depth = 0, 0, 0
    for callback in document.get("callbacks", []):
        if callback.get("aborted") is not False:
            raise RuntimeError("queue reference includes an aborted callback")
        depths = [0, 0, 0, 0]
        for lane in callback.get("lanes", []):
            if lane["bank"] != lane["group"] % 4:
                raise RuntimeError("queue bank is not group modulo four")
            if lane["accepted"] and lane["kind"] == "source":
                bank = lane["bank"]
                depths[bank] += 1
                maximum_depth = max(maximum_depth, depths[bank])
                if depths[bank] > 2:
                    raise RuntimeError("queue reference exceeds depth two")
                cycle = callback["cycle"] + depths[bank]
                c1 += depths[bank] == 1
                c2 += depths[bank] == 2
                expected_events.append(
                    {
                        "phase": "source_commit",
                        "cycle": cycle,
                        "callback_sequence": callback["callback_sequence"],
                        "request_id": callback["request_id"],
                        "order": lane["order"],
                        "bank": bank,
                        "row": lane["row"],
                        "corner": lane["corner"],
                        "payload_word": lane["payload_word"],
                    }
                )
            elif lane["kind"] == "denominator":
                if (lane["accepted"] and lane["error"] != 0) or (
                    not lane["accepted"] and lane["error"] == 0
                ):
                    raise RuntimeError(
                        "denominator backpressure acceptance/error mismatch"
                    )
                expected_events.append(
                    {
                        "phase": "denominator_admit",
                        "cycle": callback["cycle"],
                        "callback_sequence": callback["callback_sequence"],
                        "request_id": callback["request_id"],
                        "order": lane["order"],
                        "group": lane["group"],
                        "corner": lane["corner"],
                        "token_free_pre_mask": lane["token_free_pre_mask"],
                        "token_free_post_mask": lane["token_free_post_mask"],
                        "selected_token": lane["selected_token"],
                        "accepted": lane["accepted"],
                        "error": lane["error"],
                    }
                )
    expected_events.sort(
        key=lambda event: (
            event["cycle"],
            0 if event["phase"] == "source_commit" else 1,
            event.get("bank", 4),
            event["order"],
            event["request_id"],
        )
    )
    if expected_events != document.get("expected_events"):
        raise RuntimeError("canonical-v3 C++ queue reference event mismatch")
    issue_decisions = document.get("issue_decisions", [])
    if not issue_decisions or any(
        item["next_engine_tick"] != item["cycle"] + 1
        for item in issue_decisions
    ):
        raise RuntimeError("canonical-v3 issue decision is not C+1")
    if require_c2 and c2 == 0:
        raise RuntimeError("canonical-v3 trace lacks same-bank C+2 evidence")
    abi, groups = (4, 32) if case == "d32-g32" else (5, 31)
    if any(
        descriptor["abi_version"] != abi
        or descriptor["group_count"] != groups
        or descriptor["compute_tokens"] != 32
        or descriptor["fp_issue_width"] != 2
        for descriptor in document.get("descriptors", [])
    ):
        raise RuntimeError("canonical-v3 arm variant/geometry mismatch")
    return {
        "bank_count": 4,
        "bank_mapping": "bank=group%4",
        "maximum_depth": maximum_depth,
        "c_plus_1_source_commits": c1,
        "c_plus_2_same_bank_source_commits": c2,
        "c_plus_1_issue_decisions": len(issue_decisions),
        "reference": "C++ canonical-v3 queue-timed projection",
        "observed_rtl": False,
    }


def denominator_admissions(document):
    result = []
    for callback in document["callbacks"]:
        for lane in callback["lanes"]:
            if lane["kind"] != "denominator" or not lane["accepted"]:
                continue
            result.append(
                {
                    "descriptor_epoch": callback["descriptor_epoch"],
                    "reset_sequence": callback["reset_sequence"],
                    "cycle": callback["cycle"],
                    "callback_sequence": callback["callback_sequence"],
                    "request_id": callback["request_id"],
                    "compute_tokens": callback["compute_tokens"],
                    "fp_issue_width": callback["fp_issue_width"],
                    "token": lane["selected_token"],
                    "operation_index": lane["operation"],
                    "group": lane["group"],
                    "corner": lane["corner"],
                    "pre_state_digest": lane["cpp_pre_digest"],
                    "post_state_digest": lane["cpp_post_digest"],
                    "token_free_pre_mask": lane["token_free_pre_mask"],
                    "token_free_post_mask": lane["token_free_post_mask"],
                }
            )
    return result


def lifecycle_admissions(document):
    fields = (
        "descriptor_epoch",
        "reset_sequence",
        "cycle",
        "callback_sequence",
        "request_id",
        "compute_tokens",
        "fp_issue_width",
        "token",
        "operation_index",
        "group",
        "corner",
        "pre_state_digest",
        "post_state_digest",
        "token_free_pre_mask",
        "token_free_post_mask",
    )
    return [
        {field: event[field] for field in fields}
        for event in document["events"]
        if event["phase"] == "token_admission"
    ]


def validate_cross_stream(v3, v4):
    conformance = denominator_admissions(v3)
    lifecycle = lifecycle_admissions(v4)
    if not conformance or conformance != lifecycle:
        raise RuntimeError(
            "canonical-v3 denominator admissions and canonical-v4 lifecycle diverge"
        )
    live_keys = set()
    for event in v4["events"]:
        key = (
            event["descriptor_epoch"],
            event["reset_sequence"],
            event["callback_sequence"],
            event["request_id"],
            event["token"],
            event["token_generation"],
        )
        if event["phase"] == "token_admission":
            live_keys.add(key)
        elif key not in live_keys:
            raise RuntimeError(
                "lifecycle event lacks its cross-stream admission"
            )
        if event["phase"] == "token_release":
            live_keys.remove(key)
    if live_keys:
        raise RuntimeError("cross-stream lifecycle did not drain")
    return {
        "matched_admissions": len(conformance),
        "identity_fields": list(conformance[0]),
        "all_successor_events_bound_to_admission": True,
        "terminal_live_identity_count": 0,
    }


def validate_lifecycle(document):
    if (
        document.get("schema") != V4_CANONICAL_SCHEMA
        or document.get("status") != "passed_host_lifecycle_gate_b"
        or document.get("replay_authorized") is not True
        or document.get("compute_tokens") != 32
        or document.get("fp_issue_width") != 2
        or set(document.get("phase_counts", {})) != REQUIRED_PHASES
        or any(
            document["phase_counts"][phase] <= 0 for phase in REQUIRED_PHASES
        )
    ):
        raise RuntimeError(
            "canonical-v4 full-successor status/phase gate failed"
        )
    events = document.get("events", [])
    if not any(event["token_generation"] > 1 for event in events) or not any(
        event["phase"] == "token_reuse" for event in events
    ):
        raise RuntimeError("canonical-v4 lacks generation/reuse evidence")
    all_free = (1 << 32) - 1
    if not events or events[-1]["token_free_post_mask"] != all_free:
        raise RuntimeError("canonical-v4 terminal token mask is not all-free")
    return {
        "phase_counts": document["phase_counts"],
        "generation_greater_than_one": True,
        "reuse_marker_present": True,
        "terminal_live_token_count": 0,
        "terminal_token_free_mask": all_free,
        "full_drain": True,
    }


def route_v4(document, canonical_path, output):
    router = load_module("gate_b_pinned_router", ROUTER)
    selected = router.select_profile(document, require_full_successor=True)
    if selected != {"name": "canonical-v4-gate-b", "full_successor": True}:
        raise RuntimeError("Gate-B router did not select full successor")
    receipt = {
        "schema": SCHEMA_ROUTER_RECEIPT,
        "status": "passed_full_successor_router",
        "input": {
            "path": str(canonical_path),
            "sha256": live.sha256(canonical_path),
        },
        "router": {"path": str(ROUTER), "sha256": ROUTER_SHA256},
        "source_commit": live.SOURCE_COMMIT,
        "source_tree": live.SOURCE_TREE,
        "host_review": {
            "path": str(HOST_REVIEW),
            "sha256": HOST_REVIEW_SHA256,
        },
        "selection": selected,
        "canonical_v4_rtl_transactor_present": False,
        "rtl_claim_authorized": False,
    }
    live.atomic_json(output, receipt)
    return {"path": str(output), "sha256": live.sha256(output), **receipt}


def validate_correctness(root, case):
    root = pathlib.Path(root)
    gem5_stdout = live.read_regular_bytes(root / "gem5.stdout").decode(
        "utf-8", errors="replace"
    )
    app_stdout = live.read_regular_bytes(root / "app.stdout").decode(
        "utf-8", errors="replace"
    )
    app_stderr = live.read_regular_bytes(root / "app.stderr").decode(
        "utf-8", errors="replace"
    )
    if gem5_stdout.count("LANLMAA_UMT_INGRESS_TERMINAL code=0") != 1:
        raise RuntimeError("gem5 terminal marker count is not exactly one")
    if app_stdout.count("RESULT CHECK PASSED:") != 1:
        raise RuntimeError(
            "application result-check marker count is not exactly one"
        )
    if re.search(
        r"(?im)^(?:fatal|panic):",
        gem5_stdout + "\n" + app_stdout + "\n" + app_stderr,
    ):
        raise RuntimeError(
            "fatal/panic marker precedes mechanism interpretation"
        )
    submission_raw = live.read_json_nofollow(root / "submission.json")
    submission = ingress.validate_submission(submission_raw, case)
    stats = parse_stats_bytes(
        live.read_regular_bytes(root / "m5out/stats.txt")
    )
    if any(name not in stats for name in WORK_COUNTERS):
        raise RuntimeError("final stats omit a required work counter")
    calls, groups = submission["wave_calls"], submission["submitted_groups"]
    selected = "D32" if case == "d32-g32" else "D64"
    other = "D64" if selected == "D32" else "D32"
    expected = {
        "descriptorDoorbells": calls,
        "descriptorFetches": calls * 4,
        "descriptorCompletionWrites": calls,
        f"descriptorUmt{selected}Descriptors": calls,
        f"descriptorUmt{other}Descriptors": 0,
        "descriptorUmtGroupsLoaded": groups,
        "descriptorUmtInputReads": groups * 16,
        "descriptorUmtStateInputWrites": groups * 8,
        "descriptorUmtStateDenominatorsConsumed": groups * 8,
        "descriptorUmtStateResultWrites": groups * 8,
        "descriptorUmtResultsComputed": groups * 8,
    }
    if any(stats.get(name) != value for name, value in expected.items()):
        raise RuntimeError(
            "exact descriptor/group/input/state work equation failed"
        )
    return {
        "terminal_marker_count": 1,
        "result_check_passed_count": 1,
        "submission": submission,
        "work_equations": expected,
        "observed_work": {name: stats[name] for name in WORK_COUNTERS},
        "scalar_fallback_copy_readback": 0,
        "all_completions_valid": True,
    }


def parse_stats_bytes(raw):
    values = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0].startswith("system.lanl_maa."):
            try:
                values[fields[0].split(".")[-1]] = int(float(fields[1]))
            except ValueError:
                pass
    return values


def postprocess(contract_path, contract_digest, case):
    contract = live.validate_contract(contract_path, contract_digest)
    if case not in live.CASES:
        raise RuntimeError(
            "postprocess case is outside exact two-arm contract"
        )
    arm = contract["arms"][case]
    root = pathlib.Path(arm["root"])
    report_path = pathlib.Path(arm["arm_report"])
    router_path = pathlib.Path(arm["router_receipt"])
    canonical_v3 = pathlib.Path(arm["conformance_v3"])
    canonical_v4 = pathlib.Path(arm["canonical_v4"])
    analysis = report_path.parent
    if any(
        path.exists() or path.is_symlink()
        for path in (
            report_path,
            router_path,
            canonical_v3,
            canonical_v4,
        )
    ):
        raise RuntimeError("Gate-B derived evidence already exists")

    provenance = verify_provenance()
    correctness = validate_correctness(root, case)
    snapshot = capture_snapshot(arm, contract_digest)
    snapshot_fd = open_regular(snapshot["path"])
    v3_raw = analysis / "full-conformance-v3.raw.jsonl"
    v4_raw = analysis / "full-lifecycle-v4.raw.jsonl"
    manifest = analysis / "canonical-v3-source-hashes-40c8861b.json"
    try:
        verify_open_path(snapshot_fd, snapshot["path"], snapshot)
        split = split_prefix_streams(snapshot_fd, snapshot, v3_raw, v4_raw)
        verify_open_path(snapshot_fd, snapshot["path"], snapshot)
        live.atomic_json(manifest, source_manifest())
        v3_fd, v4_fd = open_regular(v3_raw), open_regular(v4_raw)
        try:
            verify_open_path(v3_fd, v3_raw, split["canonical_v3"])
            verify_open_path(v4_fd, v4_raw, split["canonical_v4"])
            v3_result = run_normalizer(
                CONFORMANCE_NORMALIZER,
                v3_fd,
                canonical_v3,
                (
                    "--trace",
                    f"/proc/self/fd/{v3_fd}",
                    "--source-hashes",
                    str(manifest),
                ),
            )
            verify_open_path(snapshot_fd, snapshot["path"], snapshot)
            v4_result = run_normalizer(
                LIFECYCLE_NORMALIZER,
                v4_fd,
                canonical_v4,
                ("--raw", f"/proc/self/fd/{v4_fd}"),
            )
            verify_open_path(snapshot_fd, snapshot["path"], snapshot)
            verify_open_path(v3_fd, v3_raw, split["canonical_v3"])
            verify_open_path(v4_fd, v4_raw, split["canonical_v4"])
        finally:
            os.close(v3_fd)
            os.close(v4_fd)
        v3 = live.read_json_nofollow(canonical_v3)
        v4 = live.read_json_nofollow(canonical_v4)
        queue = validate_queue_reference(v3, case)
        lifecycle = validate_lifecycle(v4)
        cross = validate_cross_stream(v3, v4)
        router = route_v4(v4, canonical_v4, router_path)
        final_snapshot_sha256 = verify_open_path(
            snapshot_fd, snapshot["path"], snapshot
        )
    finally:
        os.close(snapshot_fd)
    report = {
        "schema": SCHEMA_REPORT,
        "status": "passed_live_cpp_gate_b_dual_stream",
        "case": case,
        "contract": {"path": str(live.CONTRACT), "sha256": contract_digest},
        "correctness_before_mechanism": correctness,
        "terminal_snapshot": {
            **snapshot,
            "postprocessing_sha256": final_snapshot_sha256,
            "unchanged_through_both_normalizers": True,
        },
        "split_streams": split,
        "canonical_v3": {
            **v3_result,
            "input_snapshot_sha256": snapshot["sha256"],
        },
        "canonical_v4": {
            **v4_result,
            "input_snapshot_sha256": snapshot["sha256"],
        },
        "queue_timing": queue,
        "lifecycle": lifecycle,
        "cross_stream": cross,
        "router": {"path": router["path"], "sha256": router["sha256"]},
        "provenance": provenance,
        "rtl": {
            "canonical_v4_transactor_present": False,
            "observed_ready_accept_queue_commit": False,
            "rtl_launch_or_replay_performed": False,
            "cpp_rtl_equivalence_claim": False,
        },
        "claim_boundary": (
            "Live C++ correctness, callback-ingress queue reference, and full "
            "token lifecycle from one terminal snapshot. C+1/C+2 are C++ "
            "projection evidence, not observed RTL timing. No performance, "
            "mapped cost, generality, universal default, RTL, equivalence, or "
            "Gate-B promotion claim."
        ),
    }
    live.atomic_json(report_path, report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--contract-sha256", required=True)
    parser.add_argument("--case", choices=live.CASES, required=True)
    args = parser.parse_args()
    result = postprocess(args.contract, args.contract_sha256, args.case)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
