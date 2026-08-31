#!/usr/bin/env python3
"""Post-terminal canonical-v3 normalization for one validated ingress arm."""

import argparse
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import tempfile

import umt_ingress_micro_harness as ingress

SCHEMA = "lanl-maa-umt-pki4-live-normalization-v2"
TRACE_PREFIX = "UMT_PKI4_CONFORMANCE "
TRACE_SCHEMA = "lanl-maa-umt-pki4-conformance-v3"
TOKEN_SENTINEL = (1 << 64) - 1
SOURCE = pathlib.Path(
    "/data1/nier/worktrees/DX100-umt-pki4-conformance-source-v3-20260831"
)
SOURCE_COMMIT = "45e8e848ff6e1cd2be7901a32d58a93d7109b668"
SOURCE_TREE = "0d937910257d088b87303a3ade6642442f9faf22"
NORMALIZER = SOURCE / "tests/lanl_maa/umt_pki4_conformance_normalizer.py"
NORMALIZER_SHA256 = (
    "de2c140c638884aa876756c81be3de832ac14ccb938ee863a69f84a006146fb7"
)
SOURCE_HASHES = dict(ingress.CONFORMANCE_INSTRUMENTATION_SOURCES)
SOURCE_MANIFEST_BYTES = (
    json.dumps(SOURCE_HASHES, indent=2, sort_keys=True) + "\n"
).encode()
SOURCE_MANIFEST_SHA256 = hashlib.sha256(SOURCE_MANIFEST_BYTES).hexdigest()
TEMPORAL_PLAN = pathlib.Path(
    "/data1/nier/dx100-runs/2026-08-31-umt-pki4-conformance-gate-v3/"
    "pki4-temporal-equivalence-plan-v2.json"
)
TEMPORAL_PLAN_SHA256 = (
    "7ff5188835462202586fa44a3b0272e9c298aca745293abfae8354cc0988a15d"
)
NORMALIZER_REVIEW = pathlib.Path(
    "/data1/nier/dx100-runs/2026-08-31-umt-pki4-conformance-gate-v2/"
    "pki4-conformance-independent-promotion-review-v2.json"
)
NORMALIZER_REVIEW_SHA256 = (
    "64b0f37290092bfcaa7e5ed77b03acfdc9c70dc5543f1ca8a725eaf43bde9057"
)
REPLAY_SOURCE = pathlib.Path(
    "/data1/nier/worktrees/DX100-umt-pki4-gate-a-replay-20260831"
)
REPLAY_COMMIT = "c08b63a4731023cef1ade71a2eebb8663cdf1130"
REPLAY_TREE = "4ec18de22b7cf841000a3b85bf09f547ade8cdd0"
REPLAY_GENERATOR = REPLAY_SOURCE / (
    "experiments/lanl_maa_fp64_physical/scripts/"
    "generate_umt_pki4_gate_a_replay.py"
)
REPLAY_GENERATOR_SHA256 = (
    "e8d60c252e22f706459607f38aaa57f2ac23d0da929cb7a2d03b126add1268e0"
)
REPLAY_REVIEW = pathlib.Path(
    "/data1/nier/build/lanl-maa-umt-pki4-gate-a-replay-20260831/"
    "pki4-gate-a-canonical-v3-c08b63a4-independent-rereview-v1.json"
)
REPLAY_REVIEW_SHA256 = (
    "8c97c755669db95feb4e6bb79e47d3bc7928699b9505ba688ab6e8800c2dc1a3"
)
CLAIM_BOUNDARY = (
    "Full raw canonical-v3 normalization validates the C++ Gate-A trace. "
    "Canonical epoch shards are deterministic sampled inputs for later RTL "
    "replay; they are not evidence that RTL replay ran. No full-RTL claim is "
    "allowed until every complete epoch is streamed through and checked by "
    "the approved replay flow."
)
SNAPSHOT_NAME = "terminal-validated-gem5.stderr.snapshot"
SNAPSHOT_CHUNK_BYTES = 1024 * 1024


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_fd(descriptor):
    """Hash a regular file without changing its shared file offset."""
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, SNAPSHOT_CHUNK_BYTES, offset)
        if not block:
            break
        digest.update(block)
        offset += len(block)
    return digest.hexdigest()


def stable_identity(status):
    return {
        "device": status.st_dev,
        "inode": status.st_ino,
        "bytes": status.st_size,
        "mtime_ns": status.st_mtime_ns,
    }


def open_regular_nofollow(path):
    try:
        descriptor = os.open(
            pathlib.Path(path),
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
    except OSError as error:
        raise RuntimeError(
            f"cannot open evidence input without following links: {path}"
        ) from error
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RuntimeError(f"evidence input is not a regular file: {path}")
    return descriptor


def terminal_trace_binding(root, arm_report):
    """Read the analyzer-bound terminal receipt without following a link."""
    terminal_path = (
        pathlib.Path(root)
        / ingress.ARM_EVIDENCE_DIRECTORY
        / "arm-terminal.json"
    )
    expected_receipt_digest = arm_report["execution"]["terminal_sha256"]
    descriptor = open_regular_nofollow(terminal_path)
    try:
        before = stable_identity(os.fstat(descriptor))
        chunks = []
        offset = 0
        while True:
            block = os.pread(descriptor, SNAPSHOT_CHUNK_BYTES, offset)
            if not block:
                break
            chunks.append(block)
            offset += len(block)
        after = stable_identity(os.fstat(descriptor))
    finally:
        os.close(descriptor)
    if before != after:
        raise RuntimeError("arm terminal receipt changed while being read")
    receipt_bytes = b"".join(chunks)
    if hashlib.sha256(receipt_bytes).hexdigest() != expected_receipt_digest:
        raise RuntimeError("arm terminal receipt no longer matches analyzer")
    try:
        terminal = json.loads(receipt_bytes)
        output = terminal["outputs"]["gem5.stderr"]
        report_digest = arm_report["raw_sha256"]["gem5.stderr"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "arm terminal trace binding is malformed"
        ) from error
    source = pathlib.Path(root) / "gem5.stderr"
    if (
        output.get("path") != str(source)
        or output.get("sha256") != report_digest
        or output.get("reservation_identity_match") is not True
        or not isinstance(output.get("device"), int)
        or not isinstance(output.get("inode"), int)
    ):
        raise RuntimeError("terminal and analyzer trace bindings disagree")
    return {
        "terminal_receipt": {
            "path": str(terminal_path),
            "sha256": expected_receipt_digest,
        },
        "source_path": str(source),
        "device": output["device"],
        "inode": output["inode"],
        "sha256": report_digest,
    }


def _write_all(descriptor, data):
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise RuntimeError("snapshot write made no progress")
        view = view[written:]


def capture_terminal_validated_snapshot(root, arm_report, snapshot):
    """Publish one immutable-by-contract copy of validated gem5.stderr."""
    root = pathlib.Path(root).resolve()
    source = root / "gem5.stderr"
    snapshot = pathlib.Path(os.path.abspath(snapshot))
    expected_snapshot = root / "analysis/pki4-canonical-v3" / SNAPSHOT_NAME
    if snapshot != expected_snapshot:
        raise RuntimeError("terminal trace snapshot path is not canonical")
    binding = terminal_trace_binding(root, arm_report)
    if str(source) != binding["source_path"]:
        raise RuntimeError("snapshot source path is not analyzer-bound")
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if snapshot.exists() or snapshot.is_symlink():
        raise RuntimeError(
            f"terminal trace snapshot already exists: {snapshot}"
        )

    source_descriptor = open_regular_nofollow(source)
    temporary_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{snapshot.name}.", suffix=".tmp", dir=snapshot.parent
    )
    temporary = pathlib.Path(temporary_name)
    digest = hashlib.sha256()
    published = False
    try:
        before_status = os.fstat(source_descriptor)
        before = stable_identity(before_status)
        if (
            before["device"] != binding["device"]
            or before["inode"] != binding["inode"]
        ):
            raise RuntimeError("snapshot source identity mismatches terminal")
        while True:
            block = os.read(source_descriptor, SNAPSHOT_CHUNK_BYTES)
            if not block:
                break
            digest.update(block)
            _write_all(temporary_descriptor, block)
        os.fsync(temporary_descriptor)
        after = stable_identity(os.fstat(source_descriptor))
        try:
            path_status = os.stat(source, follow_symlinks=False)
        except FileNotFoundError as error:
            raise RuntimeError("snapshot source path disappeared") from error
        if (
            before != after
            or not stat.S_ISREG(path_status.st_mode)
            or stable_identity(path_status) != after
        ):
            raise RuntimeError("snapshot source changed during capture")
        observed_digest = digest.hexdigest()
        if observed_digest != binding["sha256"]:
            raise RuntimeError(
                "snapshot source hash mismatches terminal/report"
            )
        temporary_status = os.fstat(temporary_descriptor)
        if temporary_status.st_size != before["bytes"]:
            raise RuntimeError("snapshot byte count mismatches source")
        os.link(temporary, snapshot, follow_symlinks=False)
        published = True
        directory = os.open(snapshot.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError as error:
        raise RuntimeError(
            f"terminal trace snapshot already exists: {snapshot}"
        ) from error
    finally:
        os.close(source_descriptor)
        os.close(temporary_descriptor)
        temporary.unlink(missing_ok=True)

    if not published:
        raise RuntimeError("terminal trace snapshot was not published")
    snapshot_descriptor = open_regular_nofollow(snapshot)
    try:
        snapshot_status = os.fstat(snapshot_descriptor)
        snapshot_digest = sha256_fd(snapshot_descriptor)
    finally:
        os.close(snapshot_descriptor)
    if (
        snapshot_status.st_dev != temporary_status.st_dev
        or snapshot_status.st_ino != temporary_status.st_ino
        or snapshot_status.st_size != before["bytes"]
        or snapshot_digest != binding["sha256"]
    ):
        raise RuntimeError("published terminal trace snapshot is invalid")
    return {
        "path": str(snapshot),
        "sha256": snapshot_digest,
        "device": snapshot_status.st_dev,
        "inode": snapshot_status.st_ino,
        "bytes": snapshot_status.st_size,
        "mtime_ns": snapshot_status.st_mtime_ns,
        "source": {
            "path": str(source),
            **before,
            "terminal_sha256": binding["sha256"],
            "analyzer_sha256": arm_report["raw_sha256"]["gem5.stderr"],
            "terminal_receipt": binding["terminal_receipt"],
        },
    }


def open_verified_snapshot(snapshot_evidence):
    path = pathlib.Path(snapshot_evidence["path"])
    descriptor = open_regular_nofollow(path)
    observed = stable_identity(os.fstat(descriptor))
    expected = {
        key: snapshot_evidence[key]
        for key in ("device", "inode", "bytes", "mtime_ns")
    }
    if (
        observed != expected
        or sha256_fd(descriptor) != snapshot_evidence["sha256"]
    ):
        os.close(descriptor)
        raise RuntimeError("terminal trace snapshot changed after publication")
    return descriptor


def verify_snapshot_unchanged(snapshot_evidence, descriptor):
    path = pathlib.Path(snapshot_evidence["path"])
    expected = {
        key: snapshot_evidence[key]
        for key in ("device", "inode", "bytes", "mtime_ns")
    }
    observed = stable_identity(os.fstat(descriptor))
    try:
        path_status = os.stat(path, follow_symlinks=False)
    except FileNotFoundError as error:
        raise RuntimeError(
            "terminal trace snapshot path disappeared"
        ) from error
    digest = sha256_fd(descriptor)
    if (
        observed != expected
        or not stat.S_ISREG(path_status.st_mode)
        or stable_identity(path_status) != expected
        or digest != snapshot_evidence["sha256"]
    ):
        raise RuntimeError(
            "terminal trace snapshot changed during normalization"
        )
    return digest


def git_output(root, *argv):
    return subprocess.check_output(["git", *argv], cwd=root, text=True).strip()


def atomic_bytes(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_bytes(
        path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    )


def verify_provenance():
    if (
        git_output(SOURCE, "rev-parse", "HEAD") != SOURCE_COMMIT
        or git_output(SOURCE, "rev-parse", "HEAD^{tree}") != SOURCE_TREE
        or sha256(NORMALIZER) != NORMALIZER_SHA256
    ):
        raise RuntimeError("canonical-v3 normalizer source identity mismatch")
    for relative, digest in SOURCE_HASHES.items():
        if sha256(SOURCE / relative) != digest:
            raise RuntimeError(
                f"canonical-v3 source hash mismatch: {relative}"
            )
    if (
        sha256(TEMPORAL_PLAN) != TEMPORAL_PLAN_SHA256
        or sha256(NORMALIZER_REVIEW) != NORMALIZER_REVIEW_SHA256
        or git_output(REPLAY_SOURCE, "rev-parse", "HEAD") != REPLAY_COMMIT
        or git_output(REPLAY_SOURCE, "rev-parse", "HEAD^{tree}") != REPLAY_TREE
        or sha256(REPLAY_GENERATOR) != REPLAY_GENERATOR_SHA256
        or sha256(REPLAY_REVIEW) != REPLAY_REVIEW_SHA256
    ):
        raise RuntimeError(
            "normalizer plan/review or replay approval mismatch"
        )
    return {
        "post_terminal_harness": ingress.verify_harness_identity(),
        "normalizer": {
            "path": str(NORMALIZER),
            "sha256": NORMALIZER_SHA256,
            "source_commit": SOURCE_COMMIT,
            "source_tree": SOURCE_TREE,
        },
        "temporal_plan": {
            "path": str(TEMPORAL_PLAN),
            "sha256": TEMPORAL_PLAN_SHA256,
        },
        "normalizer_review": {
            "path": str(NORMALIZER_REVIEW),
            "sha256": NORMALIZER_REVIEW_SHA256,
        },
        "approved_replay": {
            "source_root": str(REPLAY_SOURCE),
            "source_commit": REPLAY_COMMIT,
            "source_tree": REPLAY_TREE,
            "generator": {
                "path": str(REPLAY_GENERATOR),
                "sha256": REPLAY_GENERATOR_SHA256,
            },
            "independent_rereview": {
                "path": str(REPLAY_REVIEW),
                "sha256": REPLAY_REVIEW_SHA256,
            },
            "executed_by_this_action": False,
        },
    }


def run_normalizer(trace, manifest, output, trace_descriptor=None):
    output = pathlib.Path(output)
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"normalizer output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    temporary = pathlib.Path(temporary_name)
    try:
        trace_argument = (
            f"/proc/self/fd/{trace_descriptor}"
            if trace_descriptor is not None
            else str(pathlib.Path(trace).resolve())
        )
        if trace_descriptor is not None:
            os.lseek(trace_descriptor, 0, os.SEEK_SET)
        completed = subprocess.run(
            [
                "/usr/bin/python3",
                str(NORMALIZER),
                "--trace",
                trace_argument,
                "--source-hashes",
                str(pathlib.Path(manifest).resolve()),
                "--output",
                str(temporary),
            ],
            cwd=SOURCE,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            pass_fds=(trace_descriptor,)
            if trace_descriptor is not None
            else (),
        )
        if completed.returncode:
            raise RuntimeError(
                "canonical-v3 normalization failed: "
                + completed.stderr.strip()[:1000]
            )
        if temporary.stat().st_size == 0:
            raise RuntimeError("canonical-v3 normalizer produced no output")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(temporary, output, follow_symlinks=False)
        directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": str(output.resolve()),
        "sha256": sha256(output),
        "bytes": output.stat().st_size,
    }


def expected_waiters(record):
    plane = record["record_base"] + record["stage"] * record["record_stride"]
    line, end = record["full_line_address"], (
        record["full_line_address"] + record["line_bytes"]
    )
    first = max(0, (line - plane + 7) // 8)
    last = min(record["group_count"] - 1, (end - 1 - plane) // 8)
    if first > last:
        return 0, 0
    return last - first + 1, (plane + first * 8 - line) // 8


def increment(mapping, value):
    key = str(value)
    mapping[key] = mapping.get(key, 0) + 1


def discover_epochs(trace):
    epochs, phases = {}, {}
    counts = {"records": 0, "issues": 0, "callbacks": 0, "events": 0}
    token_range = {
        "selected_count": 0,
        "sentinel_count": 0,
        "minimum": None,
        "maximum": None,
        "pre_mask_minimum": None,
        "pre_mask_maximum": None,
        "post_mask_minimum": None,
        "post_mask_maximum": None,
    }
    d64 = {
        "release_expected_waiter_distribution": {},
        "misaligned_release_expected_waiter_distribution": {},
        "short_tail_release_expected_waiter_distribution": {},
    }
    with pathlib.Path(trace).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.startswith(TRACE_PREFIX):
                continue
            record = json.loads(line[len(TRACE_PREFIX) :])
            if record.get("schema") != TRACE_SCHEMA:
                raise RuntimeError(
                    f"trace schema mismatch at line {line_number}"
                )
            phase, epoch = record["phase"], record["descriptor_epoch"]
            meta = epochs.setdefault(
                epoch,
                {
                    "records": 0,
                    "descriptor_bind": 0,
                    "reset_end": 0,
                    "first_request_id": None,
                },
            )
            meta["records"] += 1
            meta["descriptor_bind"] += phase == "descriptor_bind"
            meta["reset_end"] += phase == "reset_end"
            request_id = record["request_id"]
            if request_id and meta["first_request_id"] is None:
                meta["first_request_id"] = request_id
            counts["records"] += 1
            counts["issues"] += phase in {
                "d32_release",
                "d64_hold",
                "d64_release",
            }
            counts["callbacks"] += phase == "callback_end"
            counts["events"] += phase == "callback_lane"
            phases[phase] = phases.get(phase, 0) + 1
            selected = record["selected_token"]
            if selected == TOKEN_SENTINEL:
                token_range["sentinel_count"] += 1
            else:
                token_range["selected_count"] += 1
                token_range["minimum"] = (
                    selected
                    if token_range["minimum"] is None
                    else min(token_range["minimum"], selected)
                )
                token_range["maximum"] = (
                    selected
                    if token_range["maximum"] is None
                    else max(token_range["maximum"], selected)
                )
            for name, prefix in (
                ("token_free_pre_mask", "pre_mask"),
                ("token_free_post_mask", "post_mask"),
            ):
                value = record[name]
                low, high = prefix + "_minimum", prefix + "_maximum"
                token_range[low] = (
                    value
                    if token_range[low] is None
                    else min(token_range[low], value)
                )
                token_range[high] = (
                    value
                    if token_range[high] is None
                    else max(token_range[high], value)
                )
            if phase == "d64_release":
                expected, offset = expected_waiters(record)
                increment(
                    d64["release_expected_waiter_distribution"], expected
                )
                if offset:
                    increment(
                        d64["misaligned_release_expected_waiter_distribution"],
                        expected,
                    )
                if expected < record["line_bytes"] // 8:
                    increment(
                        d64["short_tail_release_expected_waiter_distribution"],
                        expected,
                    )
    complete = sorted(
        epoch
        for epoch, meta in epochs.items()
        if meta["descriptor_bind"] == 1
        and meta["reset_end"] == 1
        and meta["first_request_id"] is not None
    )
    if not counts["records"] or not complete:
        raise RuntimeError("trace has no complete canonical-v3 epoch")
    return {
        "epochs": epochs,
        "complete_epochs": complete,
        "counts": {**counts, "epochs": len(epochs)},
        "phase_counts": phases,
        "token_masks_and_range": token_range,
        "d64_expected_count_geometry": d64,
    }


def select_epochs(complete, raw_digest, hash_count):
    if not complete or hash_count < 0:
        raise RuntimeError("complete-epoch selection inputs are invalid")
    anchors = {complete[0], complete[-1]}
    candidates = [epoch for epoch in complete if epoch not in anchors]
    ranked = sorted(
        candidates,
        key=lambda epoch: (
            hashlib.sha256(f"{raw_digest}:{epoch}".encode()).hexdigest(),
            epoch,
        ),
    )
    return sorted(anchors | set(ranked[:hash_count]))


def extract_epoch_traces(trace, discovery, selected, shard_root):
    shard_root = pathlib.Path(shard_root)
    if shard_root.exists() or shard_root.is_symlink():
        raise RuntimeError("canonical shard root already exists")
    shard_root.mkdir(parents=True, exist_ok=False)
    streams, paths = {}, {}
    try:
        for epoch in selected:
            path = shard_root / f"epoch-{epoch:06d}.normalized-trace.jsonl"
            streams[epoch] = path.open("x", encoding="utf-8")
            paths[epoch] = path
        with pathlib.Path(trace).open("r", encoding="utf-8") as source:
            for line in source:
                if not line.startswith(TRACE_PREFIX):
                    continue
                record = json.loads(line[len(TRACE_PREFIX) :])
                epoch = record["descriptor_epoch"]
                if epoch not in streams:
                    continue
                first_request = discovery["epochs"][epoch]["first_request_id"]
                record["descriptor_epoch"] = 1
                record["reset_sequence"] = (
                    1 if record["phase"] in {"reset_begin", "reset_end"} else 0
                )
                if record["request_id"]:
                    record["request_id"] -= first_request - 1
                streams[epoch].write(
                    TRACE_PREFIX
                    + json.dumps(record, sort_keys=True, separators=(",", ":"))
                    + "\n"
                )
    finally:
        for stream in streams.values():
            stream.close()
    return paths


def normalize_arm(args):
    root = pathlib.Path(args.root).resolve()
    output = pathlib.Path(args.output).resolve()
    full = pathlib.Path(args.full_canonical_output).resolve()
    shards = pathlib.Path(args.shard_root).resolve()
    analysis_root = root / "analysis/pki4-canonical-v3"
    snapshot = analysis_root / SNAPSHOT_NAME
    if (
        output != analysis_root / "normalization-summary-v1.json"
        or full != analysis_root / "full-canonical-v3.json"
        or shards != analysis_root / "sampled-complete-epochs"
    ):
        raise RuntimeError(
            "post-terminal normalization output identity mismatch"
        )

    # This is deliberately first: it verifies terminal receipts and every raw
    # hash, then correctness, fatal markers, submission, and final counters.
    arm_report = ingress.analyze_arm(
        root,
        args.case,
        args.contract,
        args.contract_sha256,
        allow_descriptor_callback_restart=True,
    )
    provenance = verify_provenance()
    snapshot_evidence = capture_terminal_validated_snapshot(
        root, arm_report, snapshot
    )
    snapshot_descriptor = open_verified_snapshot(snapshot_evidence)
    manifest = analysis_root / "canonical-v3-source-hashes.json"
    try:
        atomic_bytes(manifest, SOURCE_MANIFEST_BYTES)
        stable_trace_path = pathlib.Path(
            f"/proc/self/fd/{snapshot_descriptor}"
        )
        full_result = run_normalizer(
            snapshot, manifest, full, snapshot_descriptor
        )
        full_result["input_terminal_snapshot"] = snapshot_evidence
        raw_digest = snapshot_evidence["sha256"]
        os.lseek(snapshot_descriptor, 0, os.SEEK_SET)
        discovery = discover_epochs(stable_trace_path)
        selected = select_epochs(
            discovery["complete_epochs"], raw_digest, args.hash_epoch_count
        )
        os.lseek(snapshot_descriptor, 0, os.SEEK_SET)
        traces = extract_epoch_traces(
            stable_trace_path, discovery, selected, shards
        )
        shard_reports = []
        for epoch in selected:
            trace = traces[epoch]
            canonical = trace.with_name("epoch-%06d.canonical-v3.json" % epoch)
            normalized = run_normalizer(trace, manifest, canonical)
            normalized["source_terminal_snapshot"] = snapshot_evidence
            shard_reports.append(
                {
                    "original_epoch": epoch,
                    "selection": (
                        "first"
                        if epoch == discovery["complete_epochs"][0]
                        else "last"
                        if epoch == discovery["complete_epochs"][-1]
                        else "sha256_ranked"
                    ),
                    "identity_transform": {
                        "descriptor_epoch": 1,
                        "reset_sequence": "0_then_1_for_reset_pair",
                        "request_id": (
                            "subtract_epoch_first_request_id_minus_one"
                        ),
                        "cycles_payloads_addresses_and_order": "unchanged",
                    },
                    "source_terminal_snapshot": snapshot_evidence,
                    "trace": {
                        "path": str(trace),
                        "sha256": sha256(trace),
                        "bytes": trace.stat().st_size,
                        "derived_from_terminal_snapshot_sha256": raw_digest,
                    },
                    "canonical": normalized,
                }
            )
        post_normalization_digest = verify_snapshot_unchanged(
            snapshot_evidence, snapshot_descriptor
        )
    finally:
        os.close(snapshot_descriptor)
    snapshot_evidence = {
        **snapshot_evidence,
        "post_normalization_sha256": post_normalization_digest,
        "post_normalization_identity_match": True,
    }
    full_result["input_terminal_snapshot"] = snapshot_evidence
    for shard in shard_reports:
        shard["source_terminal_snapshot"] = snapshot_evidence
        shard["canonical"]["source_terminal_snapshot"] = snapshot_evidence
    value = {
        "schema": SCHEMA,
        "status": "passed_full_raw_and_sampled_complete_epochs",
        "case": args.case,
        "contract": str(pathlib.Path(args.contract).resolve()),
        "contract_sha256": args.contract_sha256,
        "pre_normalization_arm_validation": {
            "schema": arm_report["schema"],
            "status": arm_report["status"],
            "execution": arm_report["execution"],
            "raw_sha256": arm_report["raw_sha256"],
            "submission": arm_report["submission"],
        },
        "terminal_validated_snapshot": snapshot_evidence,
        "raw_conformance_trace": {
            "path": str(root / "gem5.stderr"),
            "sha256": raw_digest,
            "bytes": snapshot_evidence["source"]["bytes"],
            "normalization_input": str(snapshot),
            "normalization_input_sha256": raw_digest,
        },
        "source_manifest": {
            "path": str(manifest),
            "sha256": SOURCE_MANIFEST_SHA256,
        },
        "provenance": provenance,
        "full_canonical": full_result,
        "streamed_summary": {
            key: value
            for key, value in discovery.items()
            if key not in {"epochs", "complete_epochs"}
        },
        "epoch_coverage": {
            "complete_epoch_count": len(discovery["complete_epochs"]),
            "sampled_epoch_count": len(selected),
            "selected_original_epochs": selected,
            "selection_algorithm": (
                "first complete + last complete + lowest "
                "sha256(raw_trace_sha256:epoch) ranks"
            ),
            "hash_rank_count": args.hash_epoch_count,
            "full_rtl_epoch_coverage": False,
        },
        "canonical_shards": shard_reports,
        "bounded_memory_policy": (
            "Terminal-bound source capture, snapshot hashing, epoch "
            "discovery, extraction, and summaries are streaming. Only "
            "per-epoch metadata is retained. The pinned committed "
            "canonical-v3 normalizer necessarily materializes its validated "
            "model for the full Gate-A claim."
        ),
        "claim_boundary": CLAIM_BOUNDARY,
    }
    atomic_json(output, value)
    return value


def main():
    parser = argparse.ArgumentParser()
    for name in (
        "root",
        "case",
        "contract",
        "contract-sha256",
        "output",
        "full-canonical-output",
        "shard-root",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--hash-epoch-count", type=int, default=4)
    parser._option_string_actions["--case"].choices = tuple(ingress.CASES)
    args = parser.parse_args()
    print(json.dumps(normalize_arm(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
