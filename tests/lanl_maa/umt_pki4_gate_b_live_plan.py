#!/usr/bin/env python3
"""Build and validate the fresh Gate-B v25 lifecycle campaign plan.

This module is intentionally incapable of launching systemd, gem5, an opcode,
or an RTL replay.  It publishes only an exact, hash-bound plan.  A later
dispatcher must first consume the separately validated v21 terminal build
proof and a new PASS review of this exact successor implementation.
"""

import argparse
import copy
import hashlib
import json
import os
import pathlib
import secrets
import stat
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
CAMPAIGN = pathlib.Path(
    "/data1/nier/dx100-runs/" "2026-09-01-umt-pki4-gate-b-lifecycle-v25-live"
)
EVIDENCE = (
    ROOT / "experiments/evidence/umt_pki4_gate_b_fresh_successor_v25_20260901"
)
PLAN = EVIDENCE / "dry-plan-v25.json"
REVIEW_REQUEST = EVIDENCE / "implementation-review-request-v25.json"

OLD_CAMPAIGN = pathlib.Path(
    "/data1/nier/dx100-runs/" "2026-09-01-umt-pki4-gate-b-lifecycle-v22-live"
)
REPAIR_COMMIT = "f7ace47631793ead7ab3af0de192184defa03b7f"
REPAIR_TREE = "bf08f415487c69010ad445e044ea68c18aa703f5"
REPAIR_REVIEW_COMMIT = "d0199bdc4a4d0b0fbf41427a960776a71478344e"
REPAIR_REVIEW_TREE = "75027c12a9f55a004a0aec8d477bb8406f977859"
REPAIR_REVIEW = EVIDENCE / "repair-independent-review-v25.json"
REPAIR_REVIEW_SHA256 = (
    "7e44107c67b3d3d9a12244d704f620ca2c545c733f47091b39692513b3e95873"
)

OLD_UNITS = {
    "d32-g32": "umt-pki4-gate-b-live-v22-d32-g32-20260901.service",
    "d64-g31": "umt-pki4-gate-b-live-v22-d64-g31-20260901.service",
}
OLD_FAILURE_ANCHORS = {
    "capture_script": {
        "path": str(OLD_CAMPAIGN / "capture_failed_dispatch_live_v23.py"),
        "sha256": "924d4edf8574ff87c482e57f25aa97141f1dc89887a1d44d76f26d0d4bafe150",
    },
    "contract": {
        "path": str(OLD_CAMPAIGN / "gate-b-lifecycle-live-contract-v23.json"),
        "sha256": "21a3dc8a50b6a6ebac3771e6e2b4e3b526638ecfcd257b65b77078d62924ad0b",
    },
    "dispatch_reservation": {
        "path": str(OLD_CAMPAIGN / "identity/dispatch-reservation-v23.json"),
        "sha256": "88f5e4002ed7aad43285616a0450e927de073bb89a30f2b3a101cbe5892cc5aa",
    },
    "forensic_receipt": {
        "path": str(
            OLD_CAMPAIGN
            / "identity/failed-dispatch-v23-live/forensic-receipt.json"
        ),
        "sha256": "6259f981a508716811a1f20872985e39f67d5293f1f0467cf0f4d1b3f786706e",
    },
}

SOURCE = pathlib.Path(
    "/data1/nier/worktrees/DX100-umt-pki4-gate-b-router-repair-20260831"
)
SOURCE_COMMIT = "40c8861ba4242101c6f9235a893ccfe2f1a13ab0"
SOURCE_TREE = "07bfe75479fd2d901a5cd138a318e10701c0d5b5"
GEM5 = SOURCE / "build/X86_UMT_T32_W2/gem5.opt"

BUILD_CAMPAIGN = pathlib.Path(
    "/data1/nier/dx100-runs/"
    "2026-08-31-umt-pki4-gate-b-lifecycle-build-v21-live"
)
BUILD_PROOF = (
    BUILD_CAMPAIGN / "identity/pki4-gate-b-lifecycle-build-proof-v21.json"
)
BUILD_PLAN = BUILD_CAMPAIGN / "build-plan-v21.json"
BUILD_REVIEW = (
    BUILD_CAMPAIGN / "gate-b-lifecycle-build-independent-review-v21.json"
)
BUILD_FINALIZER = BUILD_CAMPAIGN / "finalize_gate_b_build_v21.py"
BUILD_FINALIZER_DRY_PLAN_SHA256 = (
    "5736178e3bb83fbfbfba610047e81e746e5dbc3b850744628612c2e5a873c6d7"
)
BUILD_FINALIZER_AUDITED_SUCCESSOR_SHA256 = (
    "77d4ddeaa6fffc4af3ec7acb685006ce37592929b11e4f09c1faff8a65756eb3"
)
HOST_REVIEW = pathlib.Path(
    "/data1/nier/dx100-runs/"
    "2026-08-31-umt-pki4-gate-b-lifecycle-host-v4/"
    "gate-b-lifecycle-host-independent-review-v4.json"
)

LIFECYCLE_NORMALIZER = (
    SOURCE / "tests/lanl_maa/umt_pki4_lifecycle_normalizer.py"
)
ROUTER = SOURCE / (
    "experiments/lanl_maa_fp64_physical/scripts/" "umt_pki4_gate_b_schema.py"
)
CONFORMANCE_NORMALIZER = SOURCE / (
    "tests/lanl_maa/umt_pki4_conformance_normalizer.py"
)
LIVE_CONFORMANCE_POSTPROCESSOR = (
    ROOT / "tests/lanl_maa/normalize_umt_pki4_live_trace.py"
)
ARM_WRAPPER = ROOT / "tests/lanl_maa/run_umt_ingress_micro_arm.py"
GEM5_CONFIG = ROOT / "tests/lanl_maa/umt_ingress_micro_process_cpu.py"
NATIVE_BINARY = pathlib.Path(
    "/data1/nier/dx100-runs/"
    "2026-08-09-umt-adaptive-streamed-successor-v2/identity/test_driver"
)
NATIVE_CWD = pathlib.Path(
    "/data1/nier/worktrees/umt-lanl-maa-adaptive-d32-d64-20260808"
)

GATE_A_REPLAY = pathlib.Path(
    "/data1/nier/worktrees/DX100-umt-pki4-gate-a-replay-v2-20260831/"
    "experiments/lanl_maa_fp64_physical/scripts/"
    "generate_umt_pki4_gate_a_replay.py"
)
GATE_A_REPLAY_REVIEW = pathlib.Path(
    "/data1/nier/dx100-runs/"
    "2026-08-31-umt-pki4-gate-a-replay-v2-review/"
    "pki4-gate-a-replay-v28-d1e60928-g31-independent-review-v7.json"
)
LIVE_NORMALIZER_REVIEW = pathlib.Path(
    "/data1/nier/dx100-runs/"
    "2026-08-31-umt-pki4-normalizer-v4-d32-repair/"
    "pki4-normalizer-v4-live-semantics-262ab23e-independent-review-v3.json"
)

PINNED = {
    str(REPAIR_REVIEW): REPAIR_REVIEW_SHA256,
    str(
        HOST_REVIEW
    ): "4a20183db04f520246785ca1e776e5c802fb117fcd9e37932a136cac87fee2c4",
    str(
        BUILD_PLAN
    ): "bb8bed1aa86e4ba68a05d4fde8d5454eab4fa8ebb099b967ee31925582b392c1",
    str(
        BUILD_REVIEW
    ): "c5be9c70f04f1e754bc2b568941de32aba84442f261adadc0497462d0e441426",
    str(BUILD_FINALIZER): BUILD_FINALIZER_AUDITED_SUCCESSOR_SHA256,
    str(
        LIFECYCLE_NORMALIZER
    ): "6adc78f3a41a6a0f088622aeb3a7abbcd7456c531f329dce1086405fcbefebdc",
    str(
        ROUTER
    ): "a3912db4dfeab162dcc44533d6c84e8abc0489d6212762a1fa5dd059a473cdb2",
    str(
        CONFORMANCE_NORMALIZER
    ): "de2c140c638884aa876756c81be3de832ac14ccb938ee863a69f84a006146fb7",
    str(
        LIVE_CONFORMANCE_POSTPROCESSOR
    ): "8edaa3d0630c56461d7f21b1c300831256e394067ccc30a2a791258141e31aea",
    str(
        ARM_WRAPPER
    ): "2d5e64568062ce391677f37cbf089b755729e174d8342c7688eeb22997f01faa",
    str(
        GEM5_CONFIG
    ): "f08d5355260b10bcabb19ab79bb18bc7987c034e2580cb0f23e6e98015aeb4ba",
    str(
        NATIVE_BINARY
    ): "7db125ac6d0846c50f98042e8b42696db81c7b1f89ae4ed88b7e341bb0873f2c",
    str(
        LIVE_NORMALIZER_REVIEW
    ): "a03f7ce82864e1ac81f695b9a478c1408e8a7fc5e4ee221c76adb87209fb4360",
    str(
        GATE_A_REPLAY
    ): "daaccaff536697b4625d42f9318d2dc2b0159ec60f13136f2587d514684b2c04",
    str(
        GATE_A_REPLAY_REVIEW
    ): "05c9ff5dfc1e2bb1e3e9845ce8500ca0102b979c142b89e878dae485d1cb2824",
}

RESOURCE_PROPERTIES = (
    ("CPUQuota", "400%"),
    ("CPUWeight", "1000"),
    ("MemoryHigh", str(14 * 1024**3)),
    ("MemoryMax", str(16 * 1024**3)),
    ("MemorySwapMax", "0"),
    ("RuntimeMaxSec", "4h"),
)
CASES = {
    "d32-g32": {
        "abi": "D32",
        "abi_version": 4,
        "groups": 32,
        "mode": "wave_d32",
    },
    "d64-g31": {
        "abi": "D64",
        "abi_version": 5,
        "groups": 31,
        "mode": "wave_d64",
    },
}
LABEL_PREFIX = "lanl_maa_umt_ingress_micro"
REQUIRED_OUTPUTS = (
    "gem5.stdout",
    "gem5.stderr",
    "app.stdout",
    "app.stderr",
    "debug.log",
    "submission.json",
    "m5out/stats.txt",
    "m5out/config.ini",
    "m5out/config.json",
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


def regular_nofollow(path):
    value = os.lstat(path)
    if not stat.S_ISREG(value.st_mode):
        raise RuntimeError(f"pinned input is not a regular file: {path}")


def verify_pins():
    for name, expected in PINNED.items():
        regular_nofollow(name)
        if sha256(name) != expected:
            raise RuntimeError(f"pinned input changed: {name}")


def git_identity(root, expected_commit=None, expected_tree=None, clean=True):
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=root, text=True
    ).strip()
    status = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=root, text=True
    )
    if expected_commit is not None and commit != expected_commit:
        raise RuntimeError(f"commit changed: {root}")
    if expected_tree is not None and tree != expected_tree:
        raise RuntimeError(f"tree changed: {root}")
    if clean and status:
        raise RuntimeError(f"worktree is not clean: {root}")
    return {
        "worktree": str(root),
        "commit": commit,
        "tree": tree,
        "clean": not status,
    }


def gem5_argv(name, root):
    case = CASES[name]
    return [
        str(GEM5),
        "--listener-mode=off",
        "--dot-config=",
        f"--outdir={root / 'm5out'}",
        "--debug-flags=LANLMAA",
        f"--debug-file={root / 'debug.log'}",
        str(GEM5_CONFIG),
        f"--binary={NATIVE_BINARY}",
        f"--cwd={NATIVE_CWD}",
        f"--output-dir={root}",
        f"--app-stdout={root / 'app.stdout'}",
        f"--app-stderr={root / 'app.stderr'}",
        f"--submission-report={root / 'submission.json'}",
        f"--groups={case['groups']}",
        f"--umt-mode={case['mode']}",
        f"--label={LABEL_PREFIX}_{name}",
    ]


def arm(name):
    root = CAMPAIGN / "arms" / name
    unit = f"umt-pki4-gate-b-live-v25-{name}-20260901.service"
    command = gem5_argv(name, root)
    command_digest = json_sha256(command)
    wrapper = [
        "/usr/bin/python3",
        str(ARM_WRAPPER),
        "--arm-root",
        str(root),
        "--gem5-argv-sha256",
        command_digest,
        "--",
        *command,
    ]
    systemd = [
        "systemd-run",
        "--user",
        "--collect",
        f"--unit={unit}",
        *[f"--property={key}={value}" for key, value in RESOURCE_PROPERTIES],
        *wrapper,
    ]
    return {
        **CASES[name],
        "root": str(root),
        "unit": unit,
        "gem5_argv": command,
        "gem5_argv_sha256": command_digest,
        "wrapper_argv": wrapper,
        "wrapper_argv_sha256": json_sha256(wrapper),
        "systemd_run_argv": systemd,
        "systemd_run_argv_sha256": json_sha256(systemd),
        "required_absent_before_dispatch": [
            str(root),
            str(CAMPAIGN / "identity/dispatch-manager" / name),
        ],
        "manager_evidence": {
            "live_show": str(root / ".manager-owned/systemd-live.show"),
            "terminal_show": str(
                root / ".manager-owned/systemd-terminal.show"
            ),
            "proc_start": str(root / ".manager-owned/proc-start.json"),
            "journal_export": str(
                root / ".manager-owned/systemd-journal.export"
            ),
        },
        "service_owned_receipts": [
            str(root / ".service-owned/arm-launch.json"),
            str(root / ".service-owned/arm-output-ownership.json"),
            str(root / ".service-owned/arm-terminal.json"),
        ],
        "terminal_snapshot": str(
            root / "analysis/gate-b/terminal-validated-gem5.stderr.snapshot"
        ),
        "canonical_v4": str(root / "analysis/gate-b/full-canonical-v4.json"),
        "router_receipt": str(
            root / "analysis/gate-b/full-successor-router.json"
        ),
        "conformance_v3": str(root / "analysis/gate-b/full-canonical-v3.json"),
        "arm_report": str(root / "analysis/gate-b/arm-report-v25.json"),
    }


def expected_plan(require_clean=True):
    verify_pins()
    source = git_identity(SOURCE, SOURCE_COMMIT, SOURCE_TREE, clean=True)
    git_identity(ROOT, clean=require_clean)
    reviewed_files = {
        str(path.relative_to(ROOT)): sha256(path)
        for path in (
            pathlib.Path(__file__).resolve(),
            ARM_WRAPPER,
            GEM5_CONFIG,
            LIVE_CONFORMANCE_POSTPROCESSOR,
            ROOT / "tests/lanl_maa/test_umt_pki4_gate_b_live_plan.py",
            ROOT / "tests/lanl_maa/umt_pki4_gate_b_live_harness.py",
            ROOT / "tests/lanl_maa/postprocess_umt_pki4_gate_b_live.py",
            ROOT / "tests/lanl_maa/test_umt_pki4_gate_b_live_harness.py",
            ROOT
            / "docs/plans/umt_pki4_gate_b_fresh_successor_v25_20260901.md",
        )
    }
    arms = {name: arm(name) for name in CASES}
    return {
        "schema": "lanl-maa-umt-pki4-gate-b-lifecycle-live-plan-v25",
        "status": "fresh_successor_implemented_no_launch_review_required",
        "campaign_root": str(CAMPAIGN),
        "lineage": {
            "source": source,
            "repair": {
                "commit": REPAIR_COMMIT,
                "tree": REPAIR_TREE,
                "relationship": "direct_required_base",
            },
            "repair_independent_review": {
                "path": str(REPAIR_REVIEW),
                "sha256": REPAIR_REVIEW_SHA256,
                "commit": REPAIR_REVIEW_COMMIT,
                "tree": REPAIR_REVIEW_TREE,
                "decision": "PASS_REPAIR_AND_FRESH_SUCCESSOR_PLAN_NO_LAUNCH",
            },
            "candidate_files": reviewed_files,
            "host_review": {
                "path": str(HOST_REVIEW),
                "sha256": PINNED[str(HOST_REVIEW)],
            },
            "build_plan": {
                "path": str(BUILD_PLAN),
                "sha256": PINNED[str(BUILD_PLAN)],
            },
            "build_review": {
                "path": str(BUILD_REVIEW),
                "sha256": PINNED[str(BUILD_REVIEW)],
            },
        },
        "failed_predecessor": {
            "campaign_root": str(OLD_CAMPAIGN),
            "preservation": "read_only_byte_exact_no_reuse_no_backfill",
            "failure_anchors": copy.deepcopy(OLD_FAILURE_ANCHORS),
            "old_units": OLD_UNITS,
            "required_state_before_freeze_and_dispatch": {
                "LoadState": "not-found",
                "ActiveState": "inactive",
                "SubState": "dead",
                "MainPID": "0",
                "InvocationID": "",
            },
            "old_pid_identity_adoption": "forbidden",
        },
        "build_dependency": {
            "proof_path": str(BUILD_PROOF),
            "proof_schema": "lanl-maa-umt-pki4-gate-b-lifecycle-build-proof-v21",
            "proof_status": "passed_retained_terminal_build_only",
            "proof_must_exist_before_contract_freeze": True,
            "proof_must_bind_source_commit": SOURCE_COMMIT,
            "proof_must_bind_source_tree": SOURCE_TREE,
            "proof_must_bind_binary_path": str(GEM5),
            "proof_must_bind_t32_w2": {
                "compute_tokens": 32,
                "fp_issue_width": 2,
            },
            "proof_must_bind_compiled_defines": [
                "-DLANL_MAA_UMT_PKI4_CONFORMANCE_TEST",
                "-DLANL_MAA_UMT_PKI4_LIFECYCLE_TEST",
            ],
            "proof_must_bind_markers": [
                "UMT_PKI4_CONFORMANCE ",
                "lanl-maa-umt-pki4-conformance-v3",
                "UMT_PKI4_LIFECYCLE ",
                "lanl-maa-umt-pki4-lifecycle-v1",
            ],
            "validator": {
                "path": str(BUILD_FINALIZER),
                "reviewed_dry_plan_sha256": BUILD_FINALIZER_DRY_PLAN_SHA256,
                "audited_successor_sha256": PINNED[str(BUILD_FINALIZER)],
                "argv": ["/usr/bin/python3", str(BUILD_FINALIZER), "validate"],
                "expected_schema": "lanl-maa-umt-pki4-gate-b-lifecycle-build-proof-validation-v21",
                "expected_status": "passed",
            },
            "contract_freeze_adds": [
                "proof_sha256",
                "gem5_sha256",
                "proof_validation_stdout_sha256",
                "fresh absent arm roots and manager-owned receipt paths",
                "a PASS independent review of this exact plan",
            ],
            "launch_before_all_fields_are_frozen": "forbidden",
        },
        "dispatch": {
            "mode": "concurrent_exactly_two_arms_one_per_abi",
            "maximum_concurrent_arms": 2,
            "arms": arms,
            "resource_properties": dict(RESOURCE_PROPERTIES),
            "no_build_or_scons": True,
            "no_shell_wrapped_child": True,
            "no_clobber": True,
            "fail_closed": True,
            "fresh_namespace": {
                "campaign_root_absent_before_freeze": True,
                "contract_and_validation_paths_reserved_no_clobber": True,
                "dispatch_reservation_before_any_systemd_run": True,
                "all_arm_and_dispatch_manager_paths_absent_before_dispatch": True,
            },
        },
        "host_working_directory_identity": {
            "systemd_show_exact_value": "!/home/nier",
            "comparison": "byte_exact_string_equality",
            "normalization": False,
            "validation_precedes_manager_live_publication": True,
        },
        "output_ownership_and_snapshots": {
            "service_wrapper": {
                "path": str(ARM_WRAPPER),
                "sha256": PINNED[str(ARM_WRAPPER)],
            },
            "service_wrapper_contract": {
                "reserve_all_output_and_receipt_names_before_child": True,
                "hold_reservation_fds_until_terminal_publication": True,
                "record_device_inode_initial_hash_and_terminal_hash": True,
                "require_wrapper_and_gem5_returncode_zero": True,
                "required_outputs": list(REQUIRED_OUTPUTS),
            },
            "post_terminal_snapshot_contract": {
                "input": "terminal-receipt-bound gem5.stderr",
                "open": "O_NOFOLLOW regular-file fd",
                "copy": "fd-stable chunked copy with source pre/post device/inode/size/mtime and SHA-256",
                "publication": "temporary fsync plus no-clobber hardlink plus directory fsync",
                "postcondition": "rehash the published snapshot and revalidate unchanged through both normalizers",
                "single_snapshot_feeds_both_prefixes": [
                    "UMT_PKI4_CONFORMANCE ",
                    "UMT_PKI4_LIFECYCLE ",
                ],
            },
        },
        "analysis": {
            "lifecycle_normalizer": {
                "path": str(LIFECYCLE_NORMALIZER),
                "sha256": PINNED[str(LIFECYCLE_NORMALIZER)],
                "raw_schema": "lanl-maa-umt-pki4-lifecycle-v1",
                "output_schema": "lanl-maa-umt-pki4-canonical-stimulus-v4",
                "allow_open_tail": False,
            },
            "full_successor_router": {
                "path": str(ROUTER),
                "sha256": PINNED[str(ROUTER)],
                "entrypoint": "select_profile(document, require_full_successor=True)",
                "required_profile": "canonical-v4-gate-b",
                "host_review": {
                    "path": str(HOST_REVIEW),
                    "sha256": PINNED[str(HOST_REVIEW)],
                },
            },
            "full_successor_required_observations_per_arm": {
                "phase_counts_strictly_positive": [
                    "token_admission",
                    "token_issue",
                    "token_completion",
                    "token_release",
                    "token_reuse",
                ],
                "replay_authorized": True,
                "terminal_live_token_count": 0,
                "terminal_token_free_mask": "all 32 T32 bits set",
                "at_least_one_generation_greater_than_one": True,
                "at_least_one_token_reuse_marker": True,
                "lowest_free_admission_and_issue_slot_width_checked": True,
                "completion_release_same_cycle_and_immediate_reuse_checked": True,
                "request_callback_operation_group_corner_identity_checked": True,
            },
            "queue_timing": {
                "conformance_normalizer": {
                    "path": str(CONFORMANCE_NORMALIZER),
                    "sha256": PINNED[str(CONFORMANCE_NORMALIZER)],
                },
                "reviewed_snapshot_design_predecessor": {
                    "path": str(LIVE_CONFORMANCE_POSTPROCESSOR),
                    "sha256": PINNED[str(LIVE_CONFORMANCE_POSTPROCESSOR)],
                    "review": {
                        "path": str(LIVE_NORMALIZER_REVIEW),
                        "sha256": PINNED[str(LIVE_NORMALIZER_REVIEW)],
                    },
                    "direct_reuse": "forbidden_v16_v19_contract_does_not_accept_v21_gate_b_proof",
                },
                "required_live_evidence": [
                    "canonical-v3 request/callback/lane/end ordering",
                    "every next_engine_tick equals callback cycle C+1",
                    "four-bank group modulo mapping",
                    "per-bank depth never exceeds two",
                    "at least one same-bank second source with nominal visibility C+2",
                    "source and denominator conservation/work accounting",
                ],
                "claim_scope": "live C++ conformance observer plus queue-timed reference; not observed RTL queue timing",
            },
            "correctness": {
                "exact_terminal_marker_count": 1,
                "exact_result_check_passed_count": 1,
                "gem5_and_wrapper_returncode": 0,
                "reject_fatal_or_panic": True,
                "submission_opcode": 11,
                "submission_mode": "ordered_wave",
                "selected_abi_only": True,
                "no_scalar_fallback_or_copy_readback": True,
                "all_completions_valid": True,
                "required_work_counters": list(WORK_COUNTERS),
                "counter_equations": [
                    "selected ABI descriptors == wave_calls",
                    "other ABI descriptors == 0",
                    "groups_loaded == submitted_groups",
                    "input_reads == submitted_groups * 16",
                    "state denominators/results conserve submitted work",
                ],
            },
            "publication": "exclusive-create/no-overwrite/fsync receipts; preserve raw, snapshot, canonical-v3, canonical-v4, router result, and arm report",
        },
        "pre_dispatch_implementation_gate": {
            "status": "required_not_implemented_by_dry_plan",
            "reason": (
                "The reviewed snapshot predecessor is bound to the v16 live "
                "contract and v19 build proof. This dry plan deliberately does "
                "not weaken that validator or impersonate a reviewed Gate-B "
                "postprocessor."
            ),
            "required_before_launch": [
                "a no-launch contract freezer that validates the exact v21 proof and binds its proof and gem5 SHA-256",
                "a dispatcher/capture wrapper that checks fresh arm roots and publishes manager receipts without clobber",
                "a Gate-B postprocessor implementing the declared one-snapshot/two-prefix contract",
                "full raw canonical-v3 plus canonical-v4 normalizer and router receipts bound to the snapshot SHA-256",
                "adversarial replacement/symlink/truncation/stale-proof/partial-epoch and concurrent-publication tests",
                "an independent PASS review explicitly authorizing the exact two systemd commands",
            ],
            "launch_authorized_without_gate": False,
        },
        "rtl_full_successor_replay": {
            "status": "blocked_no_reviewed_canonical_v4_rtl_transactor",
            "concrete_blocker": (
                "The only hash-bound RTL generator/review accepts canonical-v3 "
                "callback-ingress shards. It has no canonical-v4 lifecycle-event "
                "consumer and cannot drive or observe admission, issue, completion, "
                "release, reuse, ready/accept, or queue-commit identities."
            ),
            "known_callback_only_predecessor": {
                "generator": {
                    "path": str(GATE_A_REPLAY),
                    "sha256": PINNED[str(GATE_A_REPLAY)],
                },
                "review": {
                    "path": str(GATE_A_REPLAY_REVIEW),
                    "sha256": PINNED[str(GATE_A_REPLAY_REVIEW)],
                },
                "reuse_for_canonical_v4": "forbidden",
            },
            "required_successor_before_rtl_launch": [
                "a new canonical-v4 parser that calls the reviewed full-successor router before stimulus generation",
                "an RTL transactor mapping all five lifecycle phases and their token generation/ordinal identities",
                "observed ready/accept/queue-commit and C+1/C+2 checks at the RTL public interface",
                "D32/G32 and D64/G31 positive fixtures plus malformed/missing/reordered/digest/mask/timing negatives",
                "Icarus and Yosys receipts with exact RTL/source/tool hashes",
                "an independent PASS review explicitly authorizing replay",
            ],
            "rtl_launch_authorized": False,
        },
        "authorization": {
            "build": False,
            "systemd": False,
            "gem5": False,
            "opcode": False,
            "rtl": False,
            "remote_git": False,
            "maximum_next_step": "independent review of this exact no-launch v25 implementation",
        },
        "claim_boundary": (
            "Fresh Gate-B v25 implementation plan only. It launches nothing and proves no "
            "live lifecycle behavior. A later reviewed contract may run exactly one "
            "D32/G32 and one D64/G31 arm concurrently after the exact v21 terminal "
            "build proof. Canonical-v4 lifecycle and C++ queue-timing evidence do not "
            "establish RTL ready/accept/queue-commit behavior, C++/RTL equivalence, "
            "performance, mapped cost, generality, a universal default, or Gate-B promotion."
        ),
    }


def atomic_no_clobber(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    temporary = path.parent / (
        "." + path.name + ".tmp-" + secrets.token_hex(16)
    )
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444
    )
    try:
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(temporary, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def validate(path, require_clean=True):
    regular_nofollow(path)
    value = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    expected = expected_plan(require_clean=require_clean)
    validate_value(value, expected)
    return value


def validate_value(value, expected):
    if value != expected:
        raise RuntimeError(
            "dry Gate-B live plan is not byte-semantically exact"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("render", "write", "validate"))
    parser.add_argument("--path", default=str(PLAN))
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="permit candidate publication while file hashes bind the exact tree",
    )
    args = parser.parse_args()
    if args.action == "validate":
        value = validate(args.path, require_clean=not args.allow_dirty)
    else:
        value = expected_plan(require_clean=not args.allow_dirty)
        if args.action == "write":
            if pathlib.Path(args.path).resolve() != PLAN:
                raise RuntimeError("plan publication path is not canonical")
            atomic_no_clobber(args.path, value)
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
