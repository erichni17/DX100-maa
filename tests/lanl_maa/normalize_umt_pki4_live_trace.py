#!/usr/bin/env python3
"""Post-terminal canonical-v3 normalization for one validated ingress arm."""

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import tempfile

import umt_ingress_micro_harness as ingress

SCHEMA = "lanl-maa-umt-pki4-live-normalization-v1"
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


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def run_normalizer(trace, manifest, output):
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
        completed = subprocess.run(
            [
                "/usr/bin/python3",
                str(NORMALIZER),
                "--trace",
                str(pathlib.Path(trace).resolve()),
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
        root, args.case, args.contract, args.contract_sha256
    )
    provenance = verify_provenance()
    manifest = analysis_root / "canonical-v3-source-hashes.json"
    atomic_bytes(manifest, SOURCE_MANIFEST_BYTES)
    full_result = run_normalizer(root / "gem5.stderr", manifest, full)
    raw_digest = sha256(root / "gem5.stderr")
    discovery = discover_epochs(root / "gem5.stderr")
    selected = select_epochs(
        discovery["complete_epochs"], raw_digest, args.hash_epoch_count
    )
    traces = extract_epoch_traces(
        root / "gem5.stderr", discovery, selected, shards
    )
    shard_reports = []
    for epoch in selected:
        trace = traces[epoch]
        canonical = trace.with_name("epoch-%06d.canonical-v3.json" % epoch)
        normalized = run_normalizer(trace, manifest, canonical)
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
                    "request_id": "subtract_epoch_first_request_id_minus_one",
                    "cycles_payloads_addresses_and_order": "unchanged",
                },
                "trace": {
                    "path": str(trace),
                    "sha256": sha256(trace),
                    "bytes": trace.stat().st_size,
                },
                "canonical": normalized,
            }
        )
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
        "raw_conformance_trace": {
            "path": str(root / "gem5.stderr"),
            "sha256": raw_digest,
            "bytes": (root / "gem5.stderr").stat().st_size,
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
            "Raw hashing, epoch discovery, extraction, and summaries are "
            "streaming. Only per-epoch metadata is retained. The pinned "
            "committed canonical-v3 normalizer necessarily materializes its "
            "validated model for the full Gate-A claim."
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
