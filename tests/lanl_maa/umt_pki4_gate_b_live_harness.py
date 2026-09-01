#!/usr/bin/env python3
"""Fail-closed freezer and manager for the reviewed Gate-B live campaign.

This successor harness consumes, but never rewrites, the independently
reviewed v22 dry plan.  Its live action is unavailable until a separate
implementation review explicitly authorizes the two exact commands.
"""

import argparse
import hashlib
import json
import os
import pathlib
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
CAMPAIGN = pathlib.Path(
    "/data1/nier/dx100-runs/2026-09-01-umt-pki4-gate-b-lifecycle-v22-live"
)
DRY_PLAN = CAMPAIGN / "gate-b-lifecycle-live-plan-v22.json"
DRY_PLAN_SHA256 = (
    "55e5f97de6d9459d3839b0fb85f5fa0b67e48618da50d741349ab37d226721aa"
)
DRY_REVIEW = CAMPAIGN / "gate-b-lifecycle-live-independent-review-v22.json"
DRY_REVIEW_SHA256 = (
    "4517b6df1d3510000a7ede34d20b48fc556f87d4c941dbda6ab34409618c043b"
)

BUILD_PROOF = pathlib.Path(
    "/data1/nier/dx100-runs/2026-08-31-umt-pki4-gate-b-lifecycle-build-v21-live/"
    "identity/pki4-gate-b-lifecycle-build-proof-v21.json"
)
BUILD_PROOF_SHA256 = (
    "51122bdcd72f609188e1116f652f60fbbad42aa7918840a6eafa9033d519dbd5"
)
BUILD_FINALIZER = pathlib.Path(
    "/data1/nier/dx100-runs/2026-08-31-umt-pki4-gate-b-lifecycle-build-v21-live/"
    "finalize_gate_b_build_v21.py"
)
BUILD_FINALIZER_SHA256 = (
    "5736178e3bb83fbfbfba610047e81e746e5dbc3b850744628612c2e5a873c6d7"
)
BUILD_PROOF_AUDIT = pathlib.Path(
    "/data1/nier/dx100-runs/2026-08-31-umt-pki4-gate-b-lifecycle-build-v21-live/"
    "gate-b-lifecycle-build-proof-independent-audit-v21.json"
)
BUILD_PROOF_AUDIT_SHA256 = (
    "efe54eb5f379dbb132e60462f31cc88850cb74e1774ba604de0642d103254fa1"
)
BUILD_FINALIZER_AUDITED_SHA256 = (
    "77d4ddeaa6fffc4af3ec7acb685006ce37592929b11e4f09c1faff8a65756eb3"
)
BUILD_VALIDATION_STDOUT_SHA256 = (
    "d355c35cbb7727467ea3374a2e70da5ff81edb53f172478e15d790fbbc8bda17"
)
SCHEMA_BUILD_PROOF_AUDIT = (
    "lanl-maa-umt-pki4-gate-b-lifecycle-build-proof-independent-audit-v21"
)
SOURCE = pathlib.Path(
    "/data1/nier/worktrees/DX100-umt-pki4-gate-b-router-repair-20260831"
)
SOURCE_COMMIT = "40c8861ba4242101c6f9235a893ccfe2f1a13ab0"
SOURCE_TREE = "07bfe75479fd2d901a5cd138a318e10701c0d5b5"
GEM5 = SOURCE / "build/X86_UMT_T32_W2/gem5.opt"
GEM5_SHA256 = (
    "4bc16e27cfa8f8d285810b41c798e5d04087c5cbc44ee3bd0ee7d2b4b8cae7bc"
)
DEFINES = [
    "-DLANL_MAA_UMT_PKI4_CONFORMANCE_TEST",
    "-DLANL_MAA_UMT_PKI4_LIFECYCLE_TEST",
]
MARKERS = [
    "UMT_PKI4_CONFORMANCE ",
    "lanl-maa-umt-pki4-conformance-v3",
    "7ff5188835462202586fa44a3b0272e9c298aca745293abfae8354cc0988a15d",
    "UMT_PKI4_LIFECYCLE ",
    "lanl-maa-umt-pki4-lifecycle-v1",
]

CONTRACT = CAMPAIGN / "gate-b-lifecycle-live-contract-v23.json"
VALIDATION_TRANSCRIPT = (
    CAMPAIGN / "identity/gate-b-build-proof-validation-v21.stdout"
)
IMPLEMENTATION_PLAN = (
    CAMPAIGN / "gate-b-lifecycle-live-implementation-plan-v24.json"
)
IMPLEMENTATION_REQUEST = (
    CAMPAIGN / "gate-b-lifecycle-live-implementation-review-request-v24.json"
)
IMPLEMENTATION_TESTS = (
    CAMPAIGN / "gate-b-lifecycle-live-implementation-tests-v24.txt"
)
IMPLEMENTATION_REVIEW = (
    CAMPAIGN
    / "gate-b-lifecycle-live-implementation-independent-review-v24.json"
)
DISPATCH_RESERVATION = CAMPAIGN / "identity/dispatch-reservation-v23.json"
DISPATCH_RECEIPT = CAMPAIGN / "identity/dispatch-live-receipt-v23.json"

CASES = ("d32-g32", "d64-g31")
SCHEMA_CONTRACT = "lanl-maa-umt-pki4-gate-b-live-contract-v23"
SCHEMA_IMPLEMENTATION_PLAN = (
    "lanl-maa-umt-pki4-gate-b-live-implementation-plan-v24"
)
SCHEMA_IMPLEMENTATION_REQUEST = (
    "lanl-maa-umt-pki4-gate-b-live-implementation-review-request-v24"
)
SCHEMA_IMPLEMENTATION_REVIEW = (
    "lanl-maa-umt-pki4-gate-b-live-implementation-independent-review-v24"
)
SCHEMA_MANAGER_LIVE = "lanl-maa-umt-pki4-gate-b-manager-live-v23"
SCHEMA_MANAGER_TERMINAL = "lanl-maa-umt-pki4-gate-b-manager-terminal-v23"
SCHEMA_PROC = "lanl-maa-proc-start-receipt-v1"
SCHEMA_DISPATCH_RESERVATION = (
    "lanl-maa-umt-pki4-gate-b-dispatch-reservation-v23"
)
SCHEMA_DISPATCH_RECEIPT = "lanl-maa-umt-pki4-gate-b-dispatch-live-receipt-v23"

SHOW_PROPERTIES = (
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
RESOURCE_SHOW = {
    "CPUQuotaPerSecUSec": "4s",
    "CPUWeight": "1000",
    "MemoryHigh": str(14 * 1024**3),
    "MemoryMax": str(16 * 1024**3),
    "MemorySwapMax": "0",
    "RuntimeMaxUSec": "4h",
}
SERVICE_SCHEMAS = {
    "arm-launch.json": "lanl-maa-umt-ingress-arm-launch-v7",
    "arm-output-ownership.json": "lanl-maa-umt-ingress-output-ownership-v7",
    "arm-terminal.json": "lanl-maa-umt-ingress-arm-terminal-v7",
}
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
CSV_HEADER = (
    b"# mpi ranks, Mem for PSI (kb), process rss mem (kb), "
    b"# solver unknowns (extents of PSI), total # flux iterations, "
    b"time steps, walltime(seconds),energy check, "
    b"energy in radiation field, maximum electron temperature, "
    b"maximum radiation temperature, incident power, escaping power, "
    b"power absorbed, power emitted\n"
)
CSV_HEADER_SHA256 = hashlib.sha256(CSV_HEADER).hexdigest()


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_sha256(value):
    return sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )


def regular_nofollow(path):
    try:
        value = os.lstat(path)
    except FileNotFoundError as error:
        raise RuntimeError(f"missing regular evidence file: {path}") from error
    if not stat.S_ISREG(value.st_mode):
        raise RuntimeError(f"evidence path is not a regular file: {path}")
    return value


def read_regular_bytes(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"evidence input is not regular: {path}")
        chunks = []
        offset = 0
        while True:
            block = os.pread(descriptor, 1024 * 1024, offset)
            if not block:
                break
            chunks.append(block)
            offset += len(block)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise RuntimeError(f"evidence input changed while read: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def read_json_nofollow(path):
    try:
        value = json.loads(read_regular_bytes(path))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid JSON evidence: {path}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON evidence is not an object: {path}")
    return value


def atomic_bytes(path, raw):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = pathlib.Path(name)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RuntimeError("no-clobber publication made no progress")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.link(temporary, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_bytes(
        path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    )


def publish_or_verify(path, raw):
    path = pathlib.Path(path)
    try:
        atomic_bytes(path, raw)
        return "published"
    except FileExistsError:
        if read_regular_bytes(path) != raw:
            raise RuntimeError(f"existing publication differs: {path}")
        return "reused_exact"


def git_identity(root, commit=None, tree=None, require_clean=True):
    actual_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    actual_tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True
    ).strip()
    status = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=root, text=True
    )
    if commit is not None and actual_commit != commit:
        raise RuntimeError(f"pinned commit changed: {root}")
    if tree is not None and actual_tree != tree:
        raise RuntimeError(f"pinned tree changed: {root}")
    if require_clean and status:
        raise RuntimeError(f"worktree is not clean: {root}")
    return {
        "worktree": str(pathlib.Path(root).resolve()),
        "commit": actual_commit,
        "tree": actual_tree,
        "clean": not status,
    }


def verify_fixed_inputs():
    fixed = {
        DRY_PLAN: DRY_PLAN_SHA256,
        DRY_REVIEW: DRY_REVIEW_SHA256,
    }
    for path, digest in fixed.items():
        regular_nofollow(path)
        if sha256(path) != digest:
            raise RuntimeError(f"fixed input changed: {path}")
    source = git_identity(SOURCE, SOURCE_COMMIT, SOURCE_TREE)
    plan = read_json_nofollow(DRY_PLAN)
    review = read_json_nofollow(DRY_REVIEW)
    arms = plan.get("dispatch", {}).get("arms", {})
    if (
        plan.get("schema")
        != "lanl-maa-umt-pki4-gate-b-lifecycle-live-plan-v22"
        or set(arms) != set(CASES)
        or plan["dispatch"].get("maximum_concurrent_arms") != 2
        or review.get("status")
        != "passed_dry_plan_implementation_only_authorized"
        or review.get("authorization", {}).get("live_launch") is not False
    ):
        raise RuntimeError("reviewed dry plan/review semantics changed")
    roots = [arms[name]["root"] for name in CASES]
    units = [arms[name]["unit"] for name in CASES]
    if len(set(roots)) != 2 or len(set(units)) != 2:
        raise RuntimeError("reviewed arm roots/units are not distinct")
    for name in CASES:
        arm = arms[name]
        if (
            json_sha256(arm["gem5_argv"]) != arm["gem5_argv_sha256"]
            or json_sha256(arm["wrapper_argv"]) != arm["wrapper_argv_sha256"]
            or json_sha256(arm["systemd_run_argv"])
            != arm["systemd_run_argv_sha256"]
        ):
            raise RuntimeError(f"reviewed {name} command hash changed")
    return plan, review, source


def build_finalizer_status():
    regular_nofollow(BUILD_FINALIZER)
    observed = sha256(BUILD_FINALIZER)
    return {
        "path": str(BUILD_FINALIZER),
        "reviewed_sha256": BUILD_FINALIZER_SHA256,
        "proof_audit_successor_sha256": BUILD_FINALIZER_AUDITED_SHA256,
        "observed_sha256": observed,
        "matches_reviewed_dry_plan": observed == BUILD_FINALIZER_SHA256,
        "matches_proof_audit_successor": (
            observed == BUILD_FINALIZER_AUDITED_SHA256
        ),
    }


def verify_build_finalizer():
    value = build_finalizer_status()
    if value["matches_reviewed_dry_plan"] is not True:
        raise RuntimeError(
            "v21 build finalizer changed after the reviewed dry plan"
        )
    return value


def verify_build_finalizer_for_audit(audit):
    status = build_finalizer_status()
    reviewed = audit.get("independent_revalidation", {}).get(
        "current_validator", {}
    )
    delta = audit.get("finalizer_delta_audit", {})
    if (
        reviewed.get("path") != str(BUILD_FINALIZER)
        or reviewed.get("sha256") != BUILD_FINALIZER_AUDITED_SHA256
        or reviewed.get("schema")
        != "lanl-maa-umt-pki4-gate-b-lifecycle-build-proof-validation-v21"
        or reviewed.get("status") != "passed"
        or delta.get("plan_pinned_sha256") != BUILD_FINALIZER_SHA256
        or delta.get("current_sha256") != BUILD_FINALIZER_AUDITED_SHA256
        or delta.get("allowed_changes_only") is not True
        or delta.get("proof_validation_weakened") is not False
        or delta.get("artifact_identity_validation_weakened") is not False
        or delta.get("forgery_acceptance_added") is not False
        or status["matches_proof_audit_successor"] is not True
    ):
        raise RuntimeError(
            "proof audit does not resolve the exact build-finalizer identity"
        )
    return status


def verify_proof_audit(path, digest, proof_digest):
    path = pathlib.Path(path).resolve()
    if path != BUILD_PROOF_AUDIT:
        raise RuntimeError("proof audit path is not the canonical successor")
    regular_nofollow(path)
    if digest != BUILD_PROOF_AUDIT_SHA256 or sha256(path) != digest:
        raise RuntimeError("proof audit SHA-256 mismatch")
    value = read_json_nofollow(path)
    scope = value.get("authorization", {}).get("scope", "")
    if (
        value.get("schema") != SCHEMA_BUILD_PROOF_AUDIT
        or value.get("decision") != "PASS"
        or value.get("status")
        != "passed_exact_terminal_build_proof_consumption_authorized"
        or proof_digest != BUILD_PROOF_SHA256
        or value.get("authorization", {}).get(
            "proof_consumption_by_live_freezer"
        )
        is not True
        or value.get("authorization", {}).get("proof_path") != str(BUILD_PROOF)
        or value.get("authorization", {}).get("proof_sha256") != proof_digest
        or value.get("authorization", {}).get("binary_path") != str(GEM5)
        or value.get("authorization", {}).get("binary_sha256") != GEM5_SHA256
        or "does not itself authorize a command" not in scope
        or "gem5/opcode launch" not in scope
        or "RTL replay" not in scope
        or value.get("independent_revalidation", {})
        .get("source", {})
        .get("commit")
        != SOURCE_COMMIT
        or value.get("independent_revalidation", {})
        .get("source", {})
        .get("tree")
        != SOURCE_TREE
        or value.get("independent_revalidation", {})
        .get("source", {})
        .get("clean")
        is not True
        or value.get("cleanup", {}).get("proof_current_validation") != "passed"
        or value.get("findings") != []
    ):
        raise RuntimeError("independent proof audit is not an exact PASS")
    verify_build_finalizer_for_audit(value)
    return value


def harness_files():
    names = (
        "tests/lanl_maa/umt_pki4_gate_b_live_harness.py",
        "tests/lanl_maa/postprocess_umt_pki4_gate_b_live.py",
        "tests/lanl_maa/test_umt_pki4_gate_b_live_harness.py",
        "tests/lanl_maa/umt_ingress_micro_harness.py",
        "docs/plans/umt_pki4_gate_b_live_implementation_v23_20260901.md",
    )
    return {name: sha256(ROOT / name) for name in names}


def audited_build_anchors():
    return {
        "proof": {"path": str(BUILD_PROOF), "sha256": BUILD_PROOF_SHA256},
        "proof_audit": {
            "path": str(BUILD_PROOF_AUDIT),
            "sha256": BUILD_PROOF_AUDIT_SHA256,
        },
        "binary": {"path": str(GEM5), "sha256": GEM5_SHA256},
        "validator": {
            "path": str(BUILD_FINALIZER),
            "reviewed_dry_plan_sha256": BUILD_FINALIZER_SHA256,
            "audited_successor_sha256": BUILD_FINALIZER_AUDITED_SHA256,
            "validation_stdout_sha256": BUILD_VALIDATION_STDOUT_SHA256,
        },
    }


def expected_implementation_plan(
    test_artifact, require_clean=True, recorded_finalizer=None
):
    dry, _, source = verify_fixed_inputs()
    identity = git_identity(ROOT, require_clean=require_clean)
    commands = {
        name: {
            "unit": dry["dispatch"]["arms"][name]["unit"],
            "root": dry["dispatch"]["arms"][name]["root"],
            "gem5_argv_sha256": dry["dispatch"]["arms"][name][
                "gem5_argv_sha256"
            ],
            "wrapper_argv_sha256": dry["dispatch"]["arms"][name][
                "wrapper_argv_sha256"
            ],
            "systemd_run_argv": dry["dispatch"]["arms"][name][
                "systemd_run_argv"
            ],
            "systemd_run_argv_sha256": dry["dispatch"]["arms"][name][
                "systemd_run_argv_sha256"
            ],
        }
        for name in CASES
    }
    finalizer = (
        build_finalizer_status()
        if recorded_finalizer is None
        else recorded_finalizer
    )
    verify_proof_audit(
        BUILD_PROOF_AUDIT, BUILD_PROOF_AUDIT_SHA256, BUILD_PROOF_SHA256
    )
    if (
        finalizer.get("path") != str(BUILD_FINALIZER)
        or finalizer.get("reviewed_sha256") != BUILD_FINALIZER_SHA256
        or finalizer.get("proof_audit_successor_sha256")
        != BUILD_FINALIZER_AUDITED_SHA256
        or not re.fullmatch(
            r"[0-9a-f]{64}", finalizer.get("observed_sha256", "")
        )
        or finalizer.get("matches_reviewed_dry_plan")
        != (finalizer["observed_sha256"] == BUILD_FINALIZER_SHA256)
        or finalizer.get("matches_proof_audit_successor")
        != (finalizer["observed_sha256"] == BUILD_FINALIZER_AUDITED_SHA256)
    ):
        raise RuntimeError("recorded build finalizer identity is malformed")
    return {
        "schema": SCHEMA_IMPLEMENTATION_PLAN,
        "status": (
            "implemented_tested_audited_proof_waiting_exact_command_review"
            if finalizer["matches_proof_audit_successor"]
            else "implemented_tested_blocked_audited_finalizer_changed"
        ),
        "campaign_root": str(CAMPAIGN),
        "reviewed_dry_plan": {
            "path": str(DRY_PLAN),
            "sha256": DRY_PLAN_SHA256,
        },
        "reviewed_dry_plan_review": {
            "path": str(DRY_REVIEW),
            "sha256": DRY_REVIEW_SHA256,
        },
        "implementation": {
            **identity,
            "reviewed_file_sha256": harness_files(),
        },
        "source": source,
        "future_build_dependency": {
            "proof_path": str(BUILD_PROOF),
            "proof_sha256": BUILD_PROOF_SHA256,
            "binary_path": str(GEM5),
            "binary_sha256": GEM5_SHA256,
            "proof_schema": "lanl-maa-umt-pki4-gate-b-lifecycle-build-proof-v21",
            "proof_must_bind_source_commit": SOURCE_COMMIT,
            "proof_must_bind_source_tree": SOURCE_TREE,
            "proof_validation_argv": [
                "/usr/bin/python3",
                str(BUILD_FINALIZER),
                "validate",
            ],
            "proof_validation_stdout_must_be_frozen": True,
            "independent_proof_audit": {
                "path": str(BUILD_PROOF_AUDIT),
                "sha256": BUILD_PROOF_AUDIT_SHA256,
                "required_schema": SCHEMA_BUILD_PROOF_AUDIT,
                "required_status": (
                    "passed_exact_terminal_build_proof_consumption_authorized"
                ),
                "required_decision": "PASS",
                "must_authorize_proof_consumption": True,
                "must_not_authorize_live_or_rtl_launch": True,
            },
            "validator_identity": finalizer,
            "freeze_requires_current_validator_match_audited_successor": True,
        },
        "exact_commands": commands,
        "concurrency": {
            "only_arms": list(CASES),
            "maximum_concurrent": 2,
            "distinct_units_and_roots": True,
        },
        "implemented_gates": [
            "audited exact v21 proof, binary, validator, and validation transcript identity",
            "separate implementation review binding",
            "fresh roots and no-clobber dispatch reservation",
            "service-owned reservation/launch/terminal receipts",
            "manager-owned live systemd/proc and terminal systemd/journal receipts",
            "one O_NOFOLLOW terminal-bound snapshot feeding both prefixes",
            "canonical-v3 C+1 and four-bank depth-two C+2 queue reference",
            "canonical-v4 full drain, reuse, generation, masks, and all-free terminal",
            "cross-stream request/callback/token/digest identity",
        ],
        "tests": test_artifact,
        "live_launch_authorized_by_this_plan": False,
        "rtl": {
            "canonical_v4_transactor_present": False,
            "rtl_launch_authorized": False,
            "claim": "No observed RTL ready/accept/queue-commit or C++/RTL equivalence.",
        },
        "claim_boundary": (
            "Implementation and offline adversarial tests only. No build, gem5, "
            "opcode, systemd, or RTL launch occurred. A separate PASS review must "
            "authorize only the two exact commands before freeze/dispatch."
        ),
    }


def implementation_review_request(plan_digest, test_artifact):
    plan = read_json_nofollow(IMPLEMENTATION_PLAN)
    return {
        "schema": SCHEMA_IMPLEMENTATION_REQUEST,
        "status": "independent_review_requested_no_launch",
        "reviewed_input": {
            "path": str(IMPLEMENTATION_PLAN),
            "sha256": plan_digest,
        },
        "implementation": plan["implementation"],
        "audited_build_anchors": audited_build_anchors(),
        "tests": test_artifact,
        "requested_checks": [
            "recompute every exact command argv hash from the reviewed v22 dry plan",
            "audit v21 proof validation and transcript freeze for stale/symlink/forgery failure",
            "require the separate exact-terminal-proof audit before consuming the proof",
            "audit two-arm uniqueness, max-two concurrency, and no-clobber manager capture",
            "audit terminal-bound snapshot and same-snapshot dual-prefix processing",
            "audit correctness/work equations before lifecycle or queue interpretation",
            "audit canonical-v3 C+1/C+2 and canonical-v4 drain/reuse/all-free/cross-stream gates",
            "confirm canonical-v4 RTL transactor remains absent and no RTL claim is made",
        ],
        "pass_document_required": {
            "path": str(IMPLEMENTATION_REVIEW),
            "schema": SCHEMA_IMPLEMENTATION_REVIEW,
            "status": "passed_exact_two_command_launch_authorized",
            "must_bind_plan_sha256": plan_digest,
            "must_bind_review_request_sha256": "sha256 of this request",
            "must_bind_audited_build_anchors": audited_build_anchors(),
            "authorized_arms": list(CASES),
            "maximum_concurrent": 2,
            "rtl_launch_authorized": False,
        },
        "authorization": {
            "build": False,
            "systemd": False,
            "gem5": False,
            "opcode": False,
            "rtl": False,
            "remote_git": False,
            "maximum_next_step": "independent review of this exact implementation and commands",
        },
    }


def publish_review_bundle():
    if any(
        path.exists() or path.is_symlink()
        for path in (
            IMPLEMENTATION_TESTS,
            IMPLEMENTATION_PLAN,
            IMPLEMENTATION_REQUEST,
        )
    ):
        raise RuntimeError("implementation review bundle already exists")
    git_identity(ROOT, require_clean=True)
    command = [
        "/usr/bin/python3",
        str(ROOT / "tests/lanl_maa/test_umt_pki4_gate_b_live_harness.py"),
        "-v",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("implementation adversarial tests failed")
    atomic_bytes(IMPLEMENTATION_TESTS, completed.stdout)
    test_artifact = {
        "path": str(IMPLEMENTATION_TESTS),
        "sha256": sha256(IMPLEMENTATION_TESTS),
        "bytes": IMPLEMENTATION_TESTS.stat().st_size,
        "argv": command,
        "returncode": 0,
    }
    plan = expected_implementation_plan(test_artifact)
    atomic_json(IMPLEMENTATION_PLAN, plan)
    request = implementation_review_request(
        sha256(IMPLEMENTATION_PLAN), test_artifact
    )
    atomic_json(IMPLEMENTATION_REQUEST, request)
    return {
        "implementation_plan": {
            "path": str(IMPLEMENTATION_PLAN),
            "sha256": sha256(IMPLEMENTATION_PLAN),
        },
        "review_request": {
            "path": str(IMPLEMENTATION_REQUEST),
            "sha256": sha256(IMPLEMENTATION_REQUEST),
        },
        "tests": test_artifact,
    }


def verify_implementation_plan():
    regular_nofollow(IMPLEMENTATION_PLAN)
    regular_nofollow(IMPLEMENTATION_REQUEST)
    plan = read_json_nofollow(IMPLEMENTATION_PLAN)
    tests = plan.get("tests", {})
    if (
        tests.get("path") != str(IMPLEMENTATION_TESTS)
        or sha256(IMPLEMENTATION_TESTS) != tests.get("sha256")
        or plan
        != expected_implementation_plan(
            tests,
            recorded_finalizer=plan.get("future_build_dependency", {}).get(
                "validator_identity", {}
            ),
        )
    ):
        raise RuntimeError("implementation plan or test transcript changed")
    request = read_json_nofollow(IMPLEMENTATION_REQUEST)
    if request != implementation_review_request(
        sha256(IMPLEMENTATION_PLAN), tests
    ):
        raise RuntimeError("implementation review request changed")
    return plan, request


def verify_launch_review(path, digest):
    plan, request = verify_implementation_plan()
    path = pathlib.Path(path).resolve()
    if path != IMPLEMENTATION_REVIEW or sha256(path) != digest:
        raise RuntimeError("implementation review path/hash mismatch")
    value = read_json_nofollow(path)
    command_hashes = {
        name: plan["exact_commands"][name]["systemd_run_argv_sha256"]
        for name in CASES
    }
    if (
        value.get("schema") != SCHEMA_IMPLEMENTATION_REVIEW
        or value.get("status") != "passed_exact_two_command_launch_authorized"
        or value.get("reviewed_inputs", {}).get("implementation_plan")
        != {
            "path": str(IMPLEMENTATION_PLAN),
            "sha256": sha256(IMPLEMENTATION_PLAN),
        }
        or value.get("reviewed_inputs", {}).get("review_request")
        != {
            "path": str(IMPLEMENTATION_REQUEST),
            "sha256": sha256(IMPLEMENTATION_REQUEST),
        }
        or value.get("authorization", {}).get("live_launch") is not True
        or value.get("authorization", {}).get("authorized_arms") != list(CASES)
        or value.get("authorization", {}).get("maximum_concurrent") != 2
        or value.get("authorization", {}).get("rtl_launch") is not False
        or value.get("command_hashes") != command_hashes
        or value.get("audited_build_anchors") != audited_build_anchors()
    ):
        raise RuntimeError(
            "implementation review does not authorize exact commands"
        )
    return value


def validate_build_proof(path, digest, validation_raw=None):
    path = pathlib.Path(path).resolve()
    if (
        path != BUILD_PROOF
        or digest != BUILD_PROOF_SHA256
        or sha256(path) != digest
    ):
        raise RuntimeError("exact v21 build proof path/hash mismatch")
    proof = read_json_nofollow(path)
    contract = proof.get("compile_and_link_contract", {})
    binary = contract.get("binary", {})
    source = proof.get("source", {})
    terminal = proof.get("build_invocation", {}).get("terminal_state", {})
    markers = contract.get("compiled_marker_checks", {})
    if (
        proof.get("schema")
        != "lanl-maa-umt-pki4-gate-b-lifecycle-build-proof-v21"
        or proof.get("status") != "passed_retained_terminal_build_only"
        or source.get("commit") != SOURCE_COMMIT
        or source.get("tree") != SOURCE_TREE
        or source.get("clean") is not True
        or contract.get("fixed_ordered_defines") != DEFINES
        or binary.get("path") != str(GEM5)
        or binary.get("sha256") != GEM5_SHA256
        or terminal
        not in (
            {
                "ExecMainCode": "exited",
                "ExecMainStatus": "0",
                "Result": "success",
            },
            {
                "ExecMainCode": "exited",
                "ExecMainCodeRaw": "1",
                "ExecMainStatus": "0",
                "Result": "success",
            },
        )
        or set(markers) != {"lanl_maa_o", "gem5"}
        or any(
            checks != {marker: True for marker in MARKERS}
            for checks in markers.values()
        )
    ):
        raise RuntimeError("v21 build proof semantics mismatch")
    regular_nofollow(GEM5)
    if sha256(GEM5) != binary.get("sha256"):
        raise RuntimeError("v21 build proof binary changed")
    if validation_raw is not None:
        try:
            validation = json.loads(validation_raw)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "build proof validator stdout is invalid JSON"
            ) from error
        if (
            sha256_bytes(validation_raw) != BUILD_VALIDATION_STDOUT_SHA256
            or validation.get("schema")
            != "lanl-maa-umt-pki4-gate-b-lifecycle-build-proof-validation-v21"
            or validation.get("status") != "passed"
            or validation.get("proof")
            != {"path": str(BUILD_PROOF), "sha256": digest}
            or validation.get("binary")
            != {"path": str(GEM5), "sha256": binary["sha256"]}
        ):
            raise RuntimeError("build proof validator result mismatch")
    return proof


def run_build_validator():
    if build_finalizer_status()["matches_proof_audit_successor"] is not True:
        raise RuntimeError(
            "current validator no longer matches the proof audit"
        )
    completed = subprocess.run(
        ["/usr/bin/python3", str(BUILD_FINALIZER), "validate"],
        cwd=BUILD_FINALIZER.parent,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or completed.stderr:
        raise RuntimeError("exact v21 proof validator failed or wrote stderr")
    if sha256_bytes(completed.stdout) != BUILD_VALIDATION_STDOUT_SHA256:
        raise RuntimeError("exact v21 proof validator stdout changed")
    return completed.stdout


def expected_contract(
    proof_digest, validation_raw, proof_audit_digest, review_digest
):
    verify_proof_audit(BUILD_PROOF_AUDIT, proof_audit_digest, proof_digest)
    plan, _, source = verify_fixed_inputs()
    implementation, _ = verify_implementation_plan()
    proof = validate_build_proof(BUILD_PROOF, proof_digest, validation_raw)
    verify_launch_review(IMPLEMENTATION_REVIEW, review_digest)
    binary = proof["compile_and_link_contract"]["binary"]
    return {
        "schema": SCHEMA_CONTRACT,
        "status": "frozen_reviewed_exact_two_arm_live_launch_authorized",
        "campaign_root": str(CAMPAIGN),
        "frozen_inputs": {
            "dry_plan": {"path": str(DRY_PLAN), "sha256": DRY_PLAN_SHA256},
            "dry_review": {
                "path": str(DRY_REVIEW),
                "sha256": DRY_REVIEW_SHA256,
            },
            "implementation_plan": {
                "path": str(IMPLEMENTATION_PLAN),
                "sha256": sha256(IMPLEMENTATION_PLAN),
            },
            "implementation_review_request": {
                "path": str(IMPLEMENTATION_REQUEST),
                "sha256": sha256(IMPLEMENTATION_REQUEST),
            },
            "implementation_review": {
                "path": str(IMPLEMENTATION_REVIEW),
                "sha256": review_digest,
            },
            "build_proof": {"path": str(BUILD_PROOF), "sha256": proof_digest},
            "build_proof_independent_audit": {
                "path": str(BUILD_PROOF_AUDIT),
                "sha256": proof_audit_digest,
            },
            "build_proof_validation_stdout": {
                "path": str(VALIDATION_TRANSCRIPT),
                "sha256": sha256_bytes(validation_raw),
            },
            "gem5": {"path": str(GEM5), "sha256": binary["sha256"]},
        },
        "source": source,
        "implementation": implementation["implementation"],
        "concurrency": {
            "only_arms": list(CASES),
            "maximum_concurrent": 2,
            "distinct_units_and_roots": True,
        },
        "arms": {name: plan["dispatch"]["arms"][name] for name in CASES},
        "publication": {
            "no_clobber": True,
            "service_owned_receipts": True,
            "manager_owned_live_and_terminal_receipts": True,
            "terminal_bound_single_snapshot_two_prefixes": True,
        },
        "rtl": {
            "canonical_v4_transactor_present": False,
            "rtl_launch_authorized": False,
            "claim": "No RTL timing or C++/RTL equivalence claim.",
        },
        "claim_boundary": (
            "Launch contract for exactly D32/G32 and D64/G31. It authorizes no "
            "build, remote Git, RTL replay, performance, mapped-cost, or promotion claim."
        ),
    }


def arm_paths_absent(plan):
    for name in CASES:
        root = pathlib.Path(plan["dispatch"]["arms"][name]["root"])
        if root.exists() or root.is_symlink():
            raise RuntimeError(f"arm root is not fresh: {root}")
        for path in plan["dispatch"]["arms"][name][
            "manager_evidence"
        ].values():
            if pathlib.Path(path).exists() or pathlib.Path(path).is_symlink():
                raise RuntimeError(f"manager evidence is not fresh: {path}")
        dispatch_io = CAMPAIGN / "identity/dispatch-manager" / name
        if dispatch_io.exists() or dispatch_io.is_symlink():
            raise RuntimeError(
                f"manager dispatch evidence is not fresh: {dispatch_io}"
            )


def freeze_contract(proof_digest, proof_audit_digest, review_digest):
    if CONTRACT.exists() or CONTRACT.is_symlink():
        raise RuntimeError("Gate-B v23 contract already exists")
    plan, _, _ = verify_fixed_inputs()
    # Check the exact independent PASS before consuming the proof, binary, or
    # validator in any way.
    verify_proof_audit(BUILD_PROOF_AUDIT, proof_audit_digest, proof_digest)
    arm_paths_absent(plan)
    validation_raw = run_build_validator()
    validate_build_proof(BUILD_PROOF, proof_digest, validation_raw)
    verify_launch_review(IMPLEMENTATION_REVIEW, review_digest)
    arm_paths_absent(plan)
    publish_or_verify(VALIDATION_TRANSCRIPT, validation_raw)
    contract = expected_contract(
        proof_digest, validation_raw, proof_audit_digest, review_digest
    )
    arm_paths_absent(plan)
    atomic_json(CONTRACT, contract)
    return contract


def validate_contract(path, digest, require_fresh=False):
    path = pathlib.Path(path).resolve()
    if path != CONTRACT or sha256(path) != digest:
        raise RuntimeError("frozen Gate-B contract path/hash mismatch")
    value = read_json_nofollow(path)
    frozen = value.get("frozen_inputs", {})
    validation = read_regular_bytes(VALIDATION_TRANSCRIPT)
    expected = expected_contract(
        frozen.get("build_proof", {}).get("sha256", ""),
        validation,
        frozen.get("build_proof_independent_audit", {}).get("sha256", ""),
        frozen.get("implementation_review", {}).get("sha256", ""),
    )
    if value != expected:
        raise RuntimeError("frozen Gate-B contract semantics changed")
    if require_fresh:
        plan, _, _ = verify_fixed_inputs()
        arm_paths_absent(plan)
    return value


def proc_start_ticks(pid):
    raw = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    suffix = raw[raw.rfind(")") + 2 :].split()
    value = suffix[19]
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise RuntimeError("process start ticks are invalid")
    return value


def parse_show(raw):
    fields = {}
    for line in raw.decode("utf-8", errors="strict").splitlines():
        if not line or "=" not in line:
            raise RuntimeError("systemd show contains a malformed line")
        key, value = line.split("=", 1)
        if key not in SHOW_PROPERTIES or key in fields:
            raise RuntimeError(
                "systemd show contains an unexpected/duplicate property"
            )
        fields[key] = value
    if set(fields) != set(SHOW_PROPERTIES):
        raise RuntimeError("systemd show property set is incomplete")
    return fields


def show_command(unit):
    return [
        "systemctl",
        "--user",
        "show",
        "--all",
        "--property=" + ",".join(SHOW_PROPERTIES),
        unit,
    ]


def validate_show(fields, arm, phase, live=None):
    wrapper = arm["wrapper_argv"]
    if (
        fields["Id"] != arm["unit"]
        or not re.fullmatch(r"[0-9a-f]{32}", fields["InvocationID"])
        or fields["WorkingDirectory"] not in ("", str(pathlib.Path.home()))
        or {key: fields[key] for key in RESOURCE_SHOW} != RESOURCE_SHOW
        or str(wrapper[1]) not in fields["ExecStart"]
        or arm["gem5_argv_sha256"] not in fields["ExecStart"]
        or fields["Environment"] != ""
        or not re.fullmatch(
            r"[1-9][0-9]*", fields["ExecMainStartTimestampMonotonic"]
        )
    ):
        raise RuntimeError(f"{phase} systemd identity/resource/argv mismatch")
    if phase == "live":
        if (
            not re.fullmatch(r"[1-9][0-9]*", fields["MainPID"])
            or fields["ExecMainPID"] != fields["MainPID"]
            or fields["ExecMainCode"] not in ("", "0", "(null)")
            or fields["ExecMainStatus"] not in ("", "0")
            or fields["Result"] not in ("", "success")
        ):
            raise RuntimeError("live systemd PID/state mismatch")
    elif (
        live is None
        or fields["InvocationID"] != live["InvocationID"]
        or fields["ExecMainPID"] != live["ExecMainPID"]
        or fields["MainPID"] not in ("0", live["MainPID"])
        or fields["ExecMainCode"] != "1"
        or fields["ExecMainStatus"] != "0"
        or fields["Result"] != "success"
    ):
        raise RuntimeError("terminal systemd PID/status mismatch")


def wait_live_show(arm, timeout=30.0):
    deadline = time.monotonic() + timeout
    command = show_command(arm["unit"])
    while time.monotonic() < deadline:
        completed = subprocess.run(command, capture_output=True)
        if completed.returncode == 0:
            try:
                fields = parse_show(completed.stdout)
                validate_show(fields, arm, "live")
                return completed.stdout, fields, command
            except RuntimeError:
                pass
        time.sleep(0.05)
    raise RuntimeError(
        f"unit did not publish a valid live state: {arm['unit']}"
    )


def capture_live_manager(arm, contract_digest):
    raw, fields, command = wait_live_show(arm)
    pid = int(fields["MainPID"])
    ticks = proc_start_ticks(pid)
    root = pathlib.Path(arm["root"])
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline and not root.is_dir():
        time.sleep(0.05)
    if not root.is_dir():
        raise RuntimeError("service did not create its owned arm root")
    manager = root / ".manager-owned"
    manager.mkdir(mode=0o700, exist_ok=False)
    live_path = pathlib.Path(arm["manager_evidence"]["live_show"])
    proc_path = pathlib.Path(arm["manager_evidence"]["proc_start"])
    atomic_bytes(live_path, raw)
    receipt = {
        "schema": SCHEMA_PROC,
        "pid": pid,
        "proc_start_ticks": ticks,
        "invocation_id": fields["InvocationID"],
        "exec_main_start_timestamp_monotonic": fields[
            "ExecMainStartTimestampMonotonic"
        ],
        "contract_sha256": contract_digest,
        "systemd_live_show": {
            "path": str(live_path),
            "sha256": sha256(live_path),
        },
        "systemd_show_argv": command,
    }
    atomic_json(proc_path, receipt)
    live_receipt = {
        "schema": SCHEMA_MANAGER_LIVE,
        "status": "passed_live_process_bound",
        "unit": arm["unit"],
        "root": arm["root"],
        "contract_sha256": contract_digest,
        "systemd_run_argv_sha256": arm["systemd_run_argv_sha256"],
        "invocation_id": fields["InvocationID"],
        "main_pid": pid,
        "proc_start_ticks": ticks,
        "systemd_live_show": receipt["systemd_live_show"],
        "proc_start": {"path": str(proc_path), "sha256": sha256(proc_path)},
    }
    path = manager / "manager-live.json"
    atomic_json(path, live_receipt)
    return {"path": str(path), "sha256": sha256(path)}


def dispatch(contract_path, contract_digest):
    contract = validate_contract(
        contract_path, contract_digest, require_fresh=True
    )
    if DISPATCH_RESERVATION.exists() or DISPATCH_RECEIPT.exists():
        raise RuntimeError("dispatch is already reserved or published")
    reservation = {
        "schema": SCHEMA_DISPATCH_RESERVATION,
        "status": "reserved_before_any_systemd_run",
        "contract": {"path": str(CONTRACT), "sha256": contract_digest},
        "manager_pid": os.getpid(),
        "manager_proc_start_ticks": proc_start_ticks(os.getpid()),
        "arms": {
            name: {
                "unit": contract["arms"][name]["unit"],
                "root": contract["arms"][name]["root"],
                "systemd_run_argv_sha256": contract["arms"][name][
                    "systemd_run_argv_sha256"
                ],
            }
            for name in CASES
        },
        "maximum_concurrent": 2,
    }
    atomic_json(DISPATCH_RESERVATION, reservation)
    dispatch_roots = {}
    for name in CASES:
        dispatch_root = CAMPAIGN / "identity/dispatch-manager" / name
        dispatch_root.mkdir(parents=True, exist_ok=False)
        dispatch_roots[name] = dispatch_root
    launched = {}
    for name in CASES:
        arm = contract["arms"][name]
        completed = subprocess.run(
            arm["systemd_run_argv"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        record = {
            "argv_sha256": json_sha256(arm["systemd_run_argv"]),
            "returncode": completed.returncode,
            "stdout_sha256": sha256_bytes(completed.stdout),
            "stderr_sha256": sha256_bytes(completed.stderr),
        }
        manager_parent = dispatch_roots[name]
        atomic_bytes(manager_parent / "systemd-run.stdout", completed.stdout)
        atomic_bytes(manager_parent / "systemd-run.stderr", completed.stderr)
        if completed.returncode != 0:
            raise RuntimeError(f"systemd-run failed for {name}")
        record["manager_live"] = capture_live_manager(arm, contract_digest)
        launched[name] = record
    receipt = {
        "schema": SCHEMA_DISPATCH_RECEIPT,
        "status": "passed_exact_two_arms_live",
        "contract": {"path": str(CONTRACT), "sha256": contract_digest},
        "dispatch_reservation": {
            "path": str(DISPATCH_RESERVATION),
            "sha256": sha256(DISPATCH_RESERVATION),
        },
        "maximum_concurrent": 2,
        "arms": launched,
    }
    atomic_json(DISPATCH_RECEIPT, receipt)
    return receipt


def parse_journal_export(raw):
    records, position = [], 0
    while position < len(raw):
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
                if position + 8 > len(raw):
                    raise RuntimeError("journal binary field lacks length")
                size = int.from_bytes(raw[position : position + 8], "little")
                position += 8
                if position + size >= len(raw) or raw[position + size] != 10:
                    raise RuntimeError(
                        "journal binary field length is invalid"
                    )
                value = raw[position : position + size]
                position += size + 1
            try:
                key = key.decode("ascii")
            except UnicodeDecodeError as error:
                raise RuntimeError(
                    "journal field name is not ASCII"
                ) from error
            if not key or key in fields:
                raise RuntimeError("journal field is empty or duplicated")
            fields[key] = value
        if fields:
            records.append(fields)
    if not records:
        raise RuntimeError("journal export is empty")
    return records


def validate_journal(raw, unit, invocation):
    expected_unit, expected_invocation = unit.encode(), invocation.encode()
    matching = 0
    for record in parse_journal_export(raw):
        service_unit = record.get("_SYSTEMD_USER_UNIT")
        service_invocation = record.get("_SYSTEMD_INVOCATION_ID")
        manager_unit = record.get("USER_UNIT")
        manager_invocation = record.get("USER_INVOCATION_ID")
        mentions = unit.encode() in record.get("MESSAGE", b"")
        if (service_unit, service_invocation) == (
            expected_unit,
            expected_invocation,
        ):
            matching += 1
        if (
            service_unit == expected_unit
            and service_invocation != expected_invocation
        ):
            raise RuntimeError(
                "journal service unit carries a forged invocation"
            )
        if manager_unit == expected_unit and manager_invocation not in (
            None,
            expected_invocation,
        ):
            raise RuntimeError(
                "journal manager unit carries a forged invocation"
            )
        if mentions and service_unit not in (
            expected_unit,
            b"init.scope",
            None,
        ):
            raise RuntimeError(
                "journal message has mismatched service provenance"
            )
    if not matching:
        raise RuntimeError("journal lacks an exact unit/invocation record")
    return matching


def validate_service_receipts(arm):
    root = pathlib.Path(arm["root"])
    evidence = root / ".service-owned"
    values = {}
    for name, schema in SERVICE_SCHEMAS.items():
        path = evidence / name
        value = read_json_nofollow(path)
        if value.get("schema") != schema:
            raise RuntimeError(f"service receipt schema mismatch: {name}")
        values[name] = value
    ownership = values["arm-output-ownership.json"]
    launch = values["arm-launch.json"]
    terminal = values["arm-terminal.json"]
    if (
        ownership.get("status") != "reserved_before_child"
        or launch.get("status") != "child_launch_authorized"
        or terminal.get("status") != "exited"
        or terminal.get("gem5_returncode") != 0
        or terminal.get("wrapper_returncode") != 0
        or launch.get("gem5_argv") != arm["gem5_argv"]
        or launch.get("wrapper_argv") != arm["wrapper_argv"]
        or launch.get("gem5_argv_sha256") != arm["gem5_argv_sha256"]
        or launch.get("wrapper_argv_sha256") != arm["wrapper_argv_sha256"]
        or terminal.get("gem5_argv_sha256") != arm["gem5_argv_sha256"]
        or terminal.get("wrapper_argv_sha256") != arm["wrapper_argv_sha256"]
    ):
        raise RuntimeError("service launch/terminal semantics mismatch")
    expected_outputs = {
        "gem5.stdout",
        "gem5.stderr",
        "app.stdout",
        "app.stderr",
        "debug.log",
        "submission.json",
        f"lanl_maa_umt_ingress_micro_{root.name}.csv",
        "m5out/stats.txt",
        "m5out/config.ini",
        "m5out/config.json",
    }
    if (
        set(terminal.get("outputs", {})) != expected_outputs
        or set(ownership.get("outputs", {})) != expected_outputs
    ):
        raise RuntimeError("service output set changed")
    expected_receipts = set(SERVICE_SCHEMAS)
    if set(ownership.get("receipts", {})) != expected_receipts:
        raise RuntimeError("service receipt reservation set changed")
    for name, reserved in ownership["receipts"].items():
        path = evidence / name
        status = regular_nofollow(path)
        if reserved.get("path") != str(path) or (
            reserved.get("device"),
            reserved.get("inode"),
        ) != (status.st_dev, status.st_ino):
            raise RuntimeError(f"service receipt identity changed: {name}")
    ownership_path = evidence / "arm-output-ownership.json"
    launch_path = evidence / "arm-launch.json"
    if (
        launch.get("output_ownership")
        != {"path": str(ownership_path), "sha256": sha256(ownership_path)}
        or terminal.get("output_ownership") != launch.get("output_ownership")
        or terminal.get("launch_evidence")
        != {"path": str(launch_path), "sha256": sha256(launch_path)}
    ):
        raise RuntimeError("service receipt hash chain changed")
    for relative, reserved in ownership["outputs"].items():
        output = terminal["outputs"][relative]
        path = root / relative
        status = regular_nofollow(path)
        expected_initial = (
            CSV_HEADER_SHA256 if relative.endswith(".csv") else EMPTY_SHA256
        )
        if (
            reserved.get("initial_sha256") != expected_initial
            or output.get("reservation_identity_match") is not True
            or output.get("path") != str(path)
            or output.get("device") != reserved.get("device")
            or output.get("inode") != reserved.get("inode")
            or (status.st_dev, status.st_ino)
            != (reserved.get("device"), reserved.get("inode"))
            or sha256(path) != output.get("sha256")
        ):
            raise RuntimeError(
                f"service output identity/hash changed: {relative}"
            )
        if relative.endswith(".csv"):
            with path.open("rb") as stream:
                if stream.read(len(CSV_HEADER)) != CSV_HEADER:
                    raise RuntimeError("service CSV lost its reserved header")
    return {
        "launch": {
            "path": str(evidence / "arm-launch.json"),
            "sha256": sha256(evidence / "arm-launch.json"),
        },
        "ownership": {
            "path": str(evidence / "arm-output-ownership.json"),
            "sha256": sha256(evidence / "arm-output-ownership.json"),
        },
        "terminal": {
            "path": str(evidence / "arm-terminal.json"),
            "sha256": sha256(evidence / "arm-terminal.json"),
        },
        "terminal_value": terminal,
    }


def validate_live_manager(arm, contract_digest):
    root = pathlib.Path(arm["root"])
    manager = root / ".manager-owned"
    live_receipt_path = manager / "manager-live.json"
    live_receipt = read_json_nofollow(live_receipt_path)
    live_path = pathlib.Path(arm["manager_evidence"]["live_show"])
    proc_path = pathlib.Path(arm["manager_evidence"]["proc_start"])
    fields = parse_show(read_regular_bytes(live_path))
    validate_show(fields, arm, "live")
    proc = read_json_nofollow(proc_path)
    expected_proc = {
        "schema": SCHEMA_PROC,
        "pid": int(fields["MainPID"]),
        "proc_start_ticks": proc.get("proc_start_ticks"),
        "invocation_id": fields["InvocationID"],
        "exec_main_start_timestamp_monotonic": fields[
            "ExecMainStartTimestampMonotonic"
        ],
        "contract_sha256": contract_digest,
        "systemd_live_show": {
            "path": str(live_path),
            "sha256": sha256(live_path),
        },
        "systemd_show_argv": show_command(arm["unit"]),
    }
    expected_live_receipt = {
        "schema": SCHEMA_MANAGER_LIVE,
        "status": "passed_live_process_bound",
        "unit": arm["unit"],
        "root": arm["root"],
        "contract_sha256": contract_digest,
        "systemd_run_argv_sha256": arm["systemd_run_argv_sha256"],
        "invocation_id": fields["InvocationID"],
        "main_pid": int(fields["MainPID"]),
        "proc_start_ticks": proc.get("proc_start_ticks"),
        "systemd_live_show": {
            "path": str(live_path),
            "sha256": sha256(live_path),
        },
        "proc_start": {"path": str(proc_path), "sha256": sha256(proc_path)},
    }
    if (
        live_receipt != expected_live_receipt
        or proc != expected_proc
        or not re.fullmatch(r"[1-9][0-9]*", proc.get("proc_start_ticks", ""))
        or proc.get("systemd_live_show")
        != {"path": str(live_path), "sha256": sha256(live_path)}
    ):
        raise RuntimeError("manager live systemd/proc receipt mismatch")
    return (
        fields,
        proc,
        {
            "path": str(live_receipt_path),
            "sha256": sha256(live_receipt_path),
        },
    )


def capture_terminal(contract_path, contract_digest, case):
    contract = validate_contract(contract_path, contract_digest)
    if case not in CASES:
        raise RuntimeError(
            "terminal capture case is outside exact two-arm contract"
        )
    arm = contract["arms"][case]
    service = validate_service_receipts(arm)
    live, proc, live_receipt = validate_live_manager(arm, contract_digest)
    root = pathlib.Path(arm["root"])
    manager = root / ".manager-owned"
    terminal_path = pathlib.Path(arm["manager_evidence"]["terminal_show"])
    journal_path = pathlib.Path(arm["manager_evidence"]["journal_export"])
    manager_terminal_path = manager / "manager-terminal.json"
    if any(
        path.exists() or path.is_symlink()
        for path in (
            terminal_path,
            journal_path,
            manager_terminal_path,
        )
    ):
        raise RuntimeError("manager terminal evidence already exists")
    show = show_command(arm["unit"])
    completed = subprocess.run(show, capture_output=True)
    if completed.returncode != 0 or completed.stderr:
        raise RuntimeError("terminal systemd show failed")
    terminal = parse_show(completed.stdout)
    validate_show(terminal, arm, "terminal", live)
    journal_command = [
        "journalctl",
        "--user",
        "--unit=" + arm["unit"],
        "--no-pager",
        "--output=export",
    ]
    journal = subprocess.run(journal_command, capture_output=True)
    if journal.returncode != 0 or journal.stderr:
        raise RuntimeError("terminal journal export failed")
    journal_matches = validate_journal(
        journal.stdout, arm["unit"], live["InvocationID"]
    )
    atomic_bytes(terminal_path, completed.stdout)
    atomic_bytes(journal_path, journal.stdout)
    receipt = {
        "schema": SCHEMA_MANAGER_TERMINAL,
        "status": "passed_terminal_systemd_service_and_journal_bound",
        "case": case,
        "unit": arm["unit"],
        "root": arm["root"],
        "contract": {"path": str(CONTRACT), "sha256": contract_digest},
        "invocation_id": live["InvocationID"],
        "main_pid": int(live["MainPID"]),
        "proc_start_ticks": proc["proc_start_ticks"],
        "manager_live": live_receipt,
        "service_receipts": {
            key: value
            for key, value in service.items()
            if key != "terminal_value"
        },
        "systemd_terminal_show": {
            "path": str(terminal_path),
            "sha256": sha256(terminal_path),
            "argv": show,
        },
        "journal_export": {
            "path": str(journal_path),
            "sha256": sha256(journal_path),
            "argv": journal_command,
            "exact_unit_invocation_records": journal_matches,
        },
    }
    atomic_json(manager_terminal_path, receipt)
    return receipt


def validate_manager_terminal(arm, contract_digest):
    service = validate_service_receipts(arm)
    live_fields, proc, live_receipt = validate_live_manager(
        arm, contract_digest
    )
    root = pathlib.Path(arm["root"])
    manager_path = root / ".manager-owned/manager-terminal.json"
    value = read_json_nofollow(manager_path)
    terminal_path = pathlib.Path(arm["manager_evidence"]["terminal_show"])
    journal_path = pathlib.Path(arm["manager_evidence"]["journal_export"])
    terminal_raw = read_regular_bytes(terminal_path)
    terminal_fields = parse_show(terminal_raw)
    validate_show(terminal_fields, arm, "terminal", live_fields)
    journal_raw = read_regular_bytes(journal_path)
    journal_count = validate_journal(
        journal_raw, arm["unit"], live_fields["InvocationID"]
    )
    show = show_command(arm["unit"])
    journal_command = [
        "journalctl",
        "--user",
        "--unit=" + arm["unit"],
        "--no-pager",
        "--output=export",
    ]
    expected = {
        "schema": SCHEMA_MANAGER_TERMINAL,
        "status": "passed_terminal_systemd_service_and_journal_bound",
        "case": root.name,
        "unit": arm["unit"],
        "root": arm["root"],
        "contract": {"path": str(CONTRACT), "sha256": contract_digest},
        "invocation_id": live_fields["InvocationID"],
        "main_pid": int(live_fields["MainPID"]),
        "proc_start_ticks": proc["proc_start_ticks"],
        "manager_live": live_receipt,
        "service_receipts": {
            key: item
            for key, item in service.items()
            if key != "terminal_value"
        },
        "systemd_terminal_show": {
            "path": str(terminal_path),
            "sha256": sha256(terminal_path),
            "argv": show,
        },
        "journal_export": {
            "path": str(journal_path),
            "sha256": sha256(journal_path),
            "argv": journal_command,
            "exact_unit_invocation_records": journal_count,
        },
    }
    if value != expected:
        raise RuntimeError("manager terminal evidence or hash chain changed")
    return {
        "path": str(manager_path),
        "sha256": sha256(manager_path),
        "value": value,
        "service": service,
        "live_fields": live_fields,
        "terminal_fields": terminal_fields,
    }


def main():
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("publish-review-bundle")
    freeze = actions.add_parser("freeze-contract")
    freeze.add_argument("--build-proof-sha256", required=True)
    freeze.add_argument("--build-proof-audit-sha256", required=True)
    freeze.add_argument("--implementation-review-sha256", required=True)
    for action in ("dispatch", "capture-terminal"):
        child = actions.add_parser(action)
        child.add_argument("--contract", required=True)
        child.add_argument("--contract-sha256", required=True)
        if action == "capture-terminal":
            child.add_argument("--case", choices=CASES, required=True)
    args = parser.parse_args()
    if args.action == "publish-review-bundle":
        result = publish_review_bundle()
    elif args.action == "freeze-contract":
        result = freeze_contract(
            args.build_proof_sha256,
            args.build_proof_audit_sha256,
            args.implementation_review_sha256,
        )
    elif args.action == "dispatch":
        result = dispatch(args.contract, args.contract_sha256)
    else:
        result = capture_terminal(
            args.contract, args.contract_sha256, args.case
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
