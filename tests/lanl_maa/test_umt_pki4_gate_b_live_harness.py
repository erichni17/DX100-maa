#!/usr/bin/env python3
"""Offline adversarial tests for the Gate-B v23 live implementation."""

import copy
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import postprocess_umt_pki4_gate_b_live as post
import umt_pki4_gate_b_live_harness as live


def write_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def proof_fixture(root):
    root = pathlib.Path(root)
    binary = root / "gem5.opt"
    binary.write_bytes(b"gate-b-binary")
    proof_path = root / "proof.json"
    binary_sha = live.sha256(binary)
    proof = {
        "schema": "lanl-maa-umt-pki4-gate-b-lifecycle-build-proof-v21",
        "status": "passed_retained_terminal_build_only",
        "source": {
            "commit": live.SOURCE_COMMIT,
            "tree": live.SOURCE_TREE,
            "clean": True,
        },
        "build_invocation": {
            "terminal_state": {
                "ExecMainCode": "exited",
                "ExecMainStatus": "0",
                "Result": "success",
            }
        },
        "compile_and_link_contract": {
            "fixed_ordered_defines": list(live.DEFINES),
            "binary": {"path": str(binary), "sha256": binary_sha},
            "compiled_marker_checks": {
                name: {marker: True for marker in live.MARKERS}
                for name in ("lanl_maa_o", "gem5")
            },
        },
    }
    write_json(proof_path, proof)
    validation = {
        "schema": "lanl-maa-umt-pki4-gate-b-lifecycle-build-proof-validation-v21",
        "status": "passed",
        "proof": {"path": str(proof_path), "sha256": live.sha256(proof_path)},
        "binary": {"path": str(binary), "sha256": binary_sha},
    }
    return binary, proof_path, proof, json.dumps(validation).encode()


def service_fixture(root, arm):
    root = pathlib.Path(root)
    evidence = root / ".service-owned"
    evidence.mkdir(parents=True)
    output_names = {
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
    for name in live.SERVICE_SCHEMAS:
        (evidence / name).write_bytes(b"")
    receipt_reservations = {}
    for name in live.SERVICE_SCHEMAS:
        path = evidence / name
        status = path.stat()
        receipt_reservations[name] = {
            "path": str(path),
            "device": status.st_dev,
            "inode": status.st_ino,
        }
    outputs = {}
    for name in output_names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            live.CSV_HEADER
            if name.endswith(".csv")
            else (name + "\n").encode()
        )
        status = path.stat()
        outputs[name] = {
            "path": str(path.resolve()),
            "device": status.st_dev,
            "inode": status.st_ino,
            "initial_sha256": (
                live.CSV_HEADER_SHA256
                if name.endswith(".csv")
                else live.EMPTY_SHA256
            ),
        }
    ownership = {
        "schema": live.SERVICE_SCHEMAS["arm-output-ownership.json"],
        "status": "reserved_before_child",
        "outputs": outputs,
        "receipts": receipt_reservations,
    }
    write_json(evidence / "arm-output-ownership.json", ownership)
    ownership_artifact = {
        "path": str(evidence / "arm-output-ownership.json"),
        "sha256": live.sha256(evidence / "arm-output-ownership.json"),
    }
    launch = {
        "schema": live.SERVICE_SCHEMAS["arm-launch.json"],
        "status": "child_launch_authorized",
        "gem5_argv": arm["gem5_argv"],
        "wrapper_argv": arm["wrapper_argv"],
        "gem5_argv_sha256": arm["gem5_argv_sha256"],
        "wrapper_argv_sha256": arm["wrapper_argv_sha256"],
        "output_ownership": ownership_artifact,
    }
    write_json(evidence / "arm-launch.json", launch)
    terminal_outputs = {
        name: {
            "path": value["path"],
            "device": value["device"],
            "inode": value["inode"],
            "sha256": live.sha256(value["path"]),
            "reservation_identity_match": True,
        }
        for name, value in outputs.items()
    }
    terminal = {
        "schema": live.SERVICE_SCHEMAS["arm-terminal.json"],
        "status": "exited",
        "gem5_returncode": 0,
        "wrapper_returncode": 0,
        "gem5_argv_sha256": arm["gem5_argv_sha256"],
        "wrapper_argv_sha256": arm["wrapper_argv_sha256"],
        "output_ownership": ownership_artifact,
        "launch_evidence": {
            "path": str(evidence / "arm-launch.json"),
            "sha256": live.sha256(evidence / "arm-launch.json"),
        },
        "outputs": terminal_outputs,
    }
    write_json(evidence / "arm-terminal.json", terminal)
    return ownership, launch, terminal


def show_bytes(arm, invocation, pid, terminal=False):
    values = {
        "Id": arm["unit"],
        "InvocationID": invocation,
        "MainPID": "0" if terminal else str(pid),
        "ExecMainPID": str(pid),
        "ExecMainStartTimestampMonotonic": "1234567",
        "WorkingDirectory": str(pathlib.Path.home()),
        **live.RESOURCE_SHOW,
        "ExecStart": (
            "{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 "
            + str(arm["wrapper_argv"][1])
            + " --gem5-argv-sha256 "
            + arm["gem5_argv_sha256"]
            + " ; ignore_errors=no ; }"
        ),
        "Environment": "",
        "ExecMainCode": "1" if terminal else "",
        "ExecMainStatus": "0" if terminal else "",
        "Result": "success" if terminal else "",
    }
    return "".join(
        f"{name}={values[name]}\n" for name in live.SHOW_PROPERTIES
    ).encode()


def manager_fixture(root, arm, contract_digest):
    root = pathlib.Path(root)
    manager = root / ".manager-owned"
    manager.mkdir()
    invocation, pid, ticks = "a" * 32, 1234, "98765"
    live_path = pathlib.Path(arm["manager_evidence"]["live_show"])
    terminal_path = pathlib.Path(arm["manager_evidence"]["terminal_show"])
    proc_path = pathlib.Path(arm["manager_evidence"]["proc_start"])
    journal_path = pathlib.Path(arm["manager_evidence"]["journal_export"])
    live_path.write_bytes(show_bytes(arm, invocation, pid))
    proc = {
        "schema": live.SCHEMA_PROC,
        "pid": pid,
        "proc_start_ticks": ticks,
        "invocation_id": invocation,
        "exec_main_start_timestamp_monotonic": "1234567",
        "contract_sha256": contract_digest,
        "systemd_live_show": {
            "path": str(live_path),
            "sha256": live.sha256(live_path),
        },
        "systemd_show_argv": live.show_command(arm["unit"]),
    }
    write_json(proc_path, proc)
    manager_live = {
        "schema": live.SCHEMA_MANAGER_LIVE,
        "status": "passed_live_process_bound",
        "unit": arm["unit"],
        "root": arm["root"],
        "contract_sha256": contract_digest,
        "systemd_run_argv_sha256": arm["systemd_run_argv_sha256"],
        "invocation_id": invocation,
        "main_pid": pid,
        "proc_start_ticks": ticks,
        "systemd_live_show": proc["systemd_live_show"],
        "proc_start": {
            "path": str(proc_path),
            "sha256": live.sha256(proc_path),
        },
    }
    write_json(manager / "manager-live.json", manager_live)
    terminal_path.write_bytes(show_bytes(arm, invocation, pid, terminal=True))
    journal_path.write_bytes(
        (
            f"_SYSTEMD_USER_UNIT={arm['unit']}\n"
            f"_SYSTEMD_INVOCATION_ID={invocation}\n"
            "MESSAGE=terminal\n\n"
        ).encode()
    )
    service = live.validate_service_receipts(arm)
    manager_terminal = {
        "schema": live.SCHEMA_MANAGER_TERMINAL,
        "status": "passed_terminal_systemd_service_and_journal_bound",
        "case": root.name,
        "unit": arm["unit"],
        "root": arm["root"],
        "contract": {"path": str(live.CONTRACT), "sha256": contract_digest},
        "invocation_id": invocation,
        "main_pid": pid,
        "proc_start_ticks": ticks,
        "manager_live": {
            "path": str(manager / "manager-live.json"),
            "sha256": live.sha256(manager / "manager-live.json"),
        },
        "service_receipts": {
            key: value
            for key, value in service.items()
            if key != "terminal_value"
        },
        "systemd_terminal_show": {
            "path": str(terminal_path),
            "sha256": live.sha256(terminal_path),
            "argv": live.show_command(arm["unit"]),
        },
        "journal_export": {
            "path": str(journal_path),
            "sha256": live.sha256(journal_path),
            "argv": [
                "journalctl",
                "--user",
                "--unit=" + arm["unit"],
                "--no-pager",
                "--output=export",
            ],
            "exact_unit_invocation_records": 1,
        },
    }
    write_json(manager / "manager-terminal.json", manager_terminal)
    return proc_path, journal_path


def lifecycle_document():
    fixtures = post.load_module(
        "gate_b_lifecycle_test_fixtures",
        post.SOURCE / "tests/lanl_maa/test_umt_pki4_lifecycle_normalizer.py",
    )
    normalizer = post.load_module(
        "gate_b_lifecycle_test_normalizer", post.LIFECYCLE_NORMALIZER
    )
    old_free, new_free = (1 << 24) - 1, (1 << 32) - 1
    records = copy.deepcopy(fixtures.valid_records())
    for record in records:
        record["compute_tokens"] = 32
        for field in ("token_free_pre_mask", "token_free_post_mask"):
            record[field] = (
                new_free
                if record[field] == old_free
                else new_free & ~1
                if record[field] == (old_free & ~1)
                else record[field]
            )
    return normalizer.validate_records(records)


def cross_v3(v4):
    callbacks = []
    for event in v4["events"]:
        if event["phase"] != "token_admission":
            continue
        callbacks.append(
            {
                "descriptor_epoch": event["descriptor_epoch"],
                "reset_sequence": event["reset_sequence"],
                "cycle": event["cycle"],
                "callback_sequence": event["callback_sequence"],
                "request_id": event["request_id"],
                "compute_tokens": event["compute_tokens"],
                "fp_issue_width": event["fp_issue_width"],
                "lanes": [
                    {
                        "kind": "denominator",
                        "accepted": True,
                        "selected_token": event["token"],
                        "operation": event["operation_index"],
                        "group": event["group"],
                        "corner": event["corner"],
                        "cpp_pre_digest": event["pre_state_digest"],
                        "cpp_post_digest": event["post_state_digest"],
                        "token_free_pre_mask": event["token_free_pre_mask"],
                        "token_free_post_mask": event["token_free_post_mask"],
                    }
                ],
            }
        )
    return {"callbacks": callbacks}


def queue_document():
    callback = {
        "descriptor_epoch": 1,
        "reset_sequence": 0,
        "cycle": 100,
        "callback_sequence": 1,
        "request_id": 7,
        "aborted": False,
        "lanes": [
            {
                "order": 0,
                "operation": 0,
                "kind": "source",
                "group": 0,
                "stage": 0,
                "corner": 0,
                "bank": 0,
                "row": 0,
                "payload_word": "0x1",
                "accepted": True,
                "error": 0,
            },
            {
                "order": 1,
                "operation": 4,
                "kind": "source",
                "group": 4,
                "stage": 0,
                "corner": 0,
                "bank": 0,
                "row": 1,
                "payload_word": "0x2",
                "accepted": True,
                "error": 0,
            },
            {
                "order": 2,
                "operation": 1,
                "kind": "denominator",
                "group": 1,
                "stage": 8,
                "corner": 0,
                "bank": 1,
                "row": 0,
                "accepted": False,
                "error": 9,
                "token_free_pre_mask": (1 << 32) - 1,
                "token_free_post_mask": (1 << 32) - 1,
                "selected_token": (1 << 64) - 1,
            },
        ],
    }
    expected = [
        {
            "phase": "denominator_admit",
            "cycle": 100,
            "callback_sequence": 1,
            "request_id": 7,
            "order": 2,
            "group": 1,
            "corner": 0,
            "token_free_pre_mask": (1 << 32) - 1,
            "token_free_post_mask": (1 << 32) - 1,
            "selected_token": (1 << 64) - 1,
            "accepted": False,
            "error": 9,
        },
        {
            "phase": "source_commit",
            "cycle": 101,
            "callback_sequence": 1,
            "request_id": 7,
            "order": 0,
            "bank": 0,
            "row": 0,
            "corner": 0,
            "payload_word": "0x1",
        },
        {
            "phase": "source_commit",
            "cycle": 102,
            "callback_sequence": 1,
            "request_id": 7,
            "order": 1,
            "bank": 0,
            "row": 1,
            "corner": 0,
            "payload_word": "0x2",
        },
    ]
    return {
        "schema": post.V3_CANONICAL_SCHEMA,
        "descriptors": [
            {
                "abi_version": 4,
                "group_count": 32,
                "compute_tokens": 32,
                "fp_issue_width": 2,
            }
        ],
        "callbacks": [callback],
        "issue_decisions": [{"cycle": 90, "next_engine_tick": 91}],
        "expected_events": expected,
    }


class GateBLiveHarnessTest(unittest.TestCase):
    def test_reviewed_plan_has_only_two_exact_distinct_arms(self):
        plan, review, source = live.verify_fixed_inputs()
        self.assertEqual(set(plan["dispatch"]["arms"]), set(live.CASES))
        self.assertEqual(plan["dispatch"]["maximum_concurrent_arms"], 2)
        self.assertEqual(source["commit"], live.SOURCE_COMMIT)
        self.assertFalse(review["authorization"]["live_launch"])
        for arm in plan["dispatch"]["arms"].values():
            self.assertEqual(
                live.json_sha256(arm["systemd_run_argv"]),
                arm["systemd_run_argv_sha256"],
            )

    def test_changed_build_finalizer_anchor_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            finalizer = pathlib.Path(temporary) / "finalizer.py"
            finalizer.write_text("changed\n", encoding="utf-8")
            with mock.patch.object(live, "BUILD_FINALIZER", finalizer):
                status = live.build_finalizer_status()
                self.assertFalse(status["matches_reviewed_dry_plan"])
                with self.assertRaisesRegex(RuntimeError, "changed"):
                    live.verify_build_finalizer()

    def test_successor_proof_audit_binds_proof_and_validator_delta(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            audit_path = root / "audit.json"
            finalizer = root / "finalizer.py"
            finalizer.write_text("reviewed successor\n", encoding="utf-8")
            proof_digest = "1" * 64
            audit = {
                "schema": live.SCHEMA_BUILD_PROOF_AUDIT,
                "decision": "PASS",
                "status": (
                    "passed_exact_terminal_build_proof_consumption_authorized"
                ),
                "authorization": {
                    "proof_consumption_by_live_freezer": True,
                    "proof_path": str(live.BUILD_PROOF),
                    "proof_sha256": proof_digest,
                    "binary_path": str(live.GEM5),
                    "binary_sha256": live.GEM5_SHA256,
                    "scope": (
                        "Consume proof only; this audit does not itself authorize a command, "
                        "gem5/opcode launch, or RTL replay."
                    ),
                },
                "independent_revalidation": {
                    "current_validator": {
                        "path": str(finalizer),
                        "sha256": live.sha256(finalizer),
                        "schema": (
                            "lanl-maa-umt-pki4-gate-b-lifecycle-build-proof-validation-v21"
                        ),
                        "status": "passed",
                    },
                    "source": {
                        "commit": live.SOURCE_COMMIT,
                        "tree": live.SOURCE_TREE,
                        "clean": True,
                    },
                },
                "finalizer_delta_audit": {
                    "plan_pinned_sha256": live.BUILD_FINALIZER_SHA256,
                    "current_sha256": live.sha256(finalizer),
                    "allowed_changes_only": True,
                    "proof_validation_weakened": False,
                    "artifact_identity_validation_weakened": False,
                    "forgery_acceptance_added": False,
                },
                "cleanup": {"proof_current_validation": "passed"},
                "findings": [],
            }
            write_json(audit_path, audit)
            with mock.patch.multiple(
                live,
                BUILD_PROOF_AUDIT=audit_path,
                BUILD_PROOF_AUDIT_SHA256=live.sha256(audit_path),
                BUILD_PROOF_SHA256=proof_digest,
                BUILD_FINALIZER=finalizer,
                BUILD_FINALIZER_AUDITED_SHA256=live.sha256(finalizer),
            ):
                self.assertEqual(
                    live.verify_proof_audit(
                        audit_path, live.sha256(audit_path), proof_digest
                    ),
                    audit,
                )
                audit["findings"] = ["forged"]
                write_json(audit_path, audit)
                with self.assertRaisesRegex(
                    RuntimeError, "mismatch|not an exact PASS"
                ):
                    live.verify_proof_audit(
                        audit_path, live.sha256(audit_path), proof_digest
                    )

    def test_freeze_rejects_before_proof_validator_when_audit_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            contract = pathlib.Path(temporary) / "contract.json"
            plan = {"dispatch": {"arms": {}}}
            with (
                mock.patch.object(live, "CONTRACT", contract),
                mock.patch.object(
                    live, "verify_fixed_inputs", return_value=(plan, {}, {})
                ),
                mock.patch.object(
                    live,
                    "verify_proof_audit",
                    side_effect=RuntimeError("proof audit absent"),
                ),
                mock.patch.object(live, "run_build_validator") as validator,
            ):
                with self.assertRaisesRegex(RuntimeError, "audit absent"):
                    live.freeze_contract("1" * 64, "2" * 64, "3" * 64)
                validator.assert_not_called()

    def test_v21_proof_and_validator_binding_reject_forgery(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary, proof_path, proof, validation = proof_fixture(temporary)
            digest = live.sha256(proof_path)
            with mock.patch.multiple(
                live,
                BUILD_PROOF=proof_path,
                BUILD_PROOF_SHA256=digest,
                GEM5=binary,
                GEM5_SHA256=live.sha256(binary),
                BUILD_VALIDATION_STDOUT_SHA256=hashlib.sha256(
                    validation
                ).hexdigest(),
            ):
                self.assertEqual(
                    live.validate_build_proof(proof_path, digest, validation),
                    proof,
                )
                for mutation in ("source", "defines", "marker", "transcript"):
                    bad = copy.deepcopy(proof)
                    bad_validation = validation
                    if mutation == "source":
                        bad["source"]["tree"] = "0" * 40
                    elif mutation == "defines":
                        bad["compile_and_link_contract"][
                            "fixed_ordered_defines"
                        ].reverse()
                    elif mutation == "marker":
                        bad["compile_and_link_contract"][
                            "compiled_marker_checks"
                        ]["gem5"][live.MARKERS[-1]] = False
                    else:
                        parsed = json.loads(validation)
                        parsed["binary"]["sha256"] = "0" * 64
                        bad_validation = json.dumps(parsed).encode()
                    write_json(proof_path, bad)
                    with self.assertRaises(RuntimeError):
                        live.validate_build_proof(
                            proof_path,
                            live.sha256(proof_path),
                            bad_validation,
                        )
                    write_json(proof_path, proof)

    def test_launch_review_requires_exact_command_hashes_and_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            plan_path, request_path = root / "plan.json", root / "request.json"
            review_path = root / "review.json"
            write_json(plan_path, {"plan": True})
            write_json(request_path, {"request": True})
            plan = {
                "exact_commands": {
                    name: {"systemd_run_argv_sha256": name * 8}
                    for name in live.CASES
                }
            }
            command_hashes = {
                name: plan["exact_commands"][name]["systemd_run_argv_sha256"]
                for name in live.CASES
            }
            review = {
                "schema": live.SCHEMA_IMPLEMENTATION_REVIEW,
                "status": "passed_exact_two_command_launch_authorized",
                "reviewed_inputs": {
                    "implementation_plan": {
                        "path": str(plan_path),
                        "sha256": live.sha256(plan_path),
                    },
                    "review_request": {
                        "path": str(request_path),
                        "sha256": live.sha256(request_path),
                    },
                },
                "authorization": {
                    "live_launch": True,
                    "authorized_arms": list(live.CASES),
                    "maximum_concurrent": 2,
                    "rtl_launch": False,
                },
                "command_hashes": command_hashes,
                "audited_build_anchors": live.audited_build_anchors(),
            }
            write_json(review_path, review)
            with (
                mock.patch.multiple(
                    live,
                    IMPLEMENTATION_PLAN=plan_path,
                    IMPLEMENTATION_REQUEST=request_path,
                    IMPLEMENTATION_REVIEW=review_path,
                ),
                mock.patch.object(
                    live, "verify_implementation_plan", return_value=(plan, {})
                ),
            ):
                self.assertEqual(
                    live.verify_launch_review(
                        review_path, live.sha256(review_path)
                    ),
                    review,
                )
                review["authorization"]["maximum_concurrent"] = 3
                write_json(review_path, review)
                with self.assertRaisesRegex(
                    RuntimeError, "does not authorize"
                ):
                    live.verify_launch_review(
                        review_path, live.sha256(review_path)
                    )

    def test_concurrent_publication_rejects_second_and_preserves_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "receipt"
            results = []
            barrier = threading.Barrier(2)

            def publish(raw):
                barrier.wait()
                try:
                    live.atomic_bytes(path, raw)
                    results.append(("ok", raw))
                except FileExistsError:
                    results.append(("exists", raw))

            threads = [
                threading.Thread(target=publish, args=(payload,))
                for payload in (b"first", b"second")
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual([kind for kind, _ in results].count("ok"), 1)
            self.assertEqual([kind for kind, _ in results].count("exists"), 1)
            winner = next(raw for kind, raw in results if kind == "ok")
            self.assertEqual(path.read_bytes(), winner)

    def test_service_receipts_reject_replacement_symlink_and_truncation(self):
        for mutation in ("replacement", "symlink", "truncation"):
            with self.subTest(
                mutation=mutation
            ), tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary) / "d32-g32"
                arm = {
                    "root": str(root),
                    "gem5_argv": ["gem5", "--dot-config="],
                    "wrapper_argv": ["python", "wrapper"],
                    "gem5_argv_sha256": "a" * 64,
                    "wrapper_argv_sha256": "b" * 64,
                }
                service_fixture(root, arm)
                live.validate_service_receipts(arm)
                target = root / "gem5.stderr"
                if mutation == "replacement":
                    replacement = root / "replacement"
                    replacement.write_bytes(target.read_bytes())
                    os.replace(replacement, target)
                elif mutation == "symlink":
                    payload = root / "payload"
                    payload.write_bytes(target.read_bytes())
                    target.unlink()
                    target.symlink_to(payload)
                else:
                    target.write_bytes(b"")
                with self.assertRaises(RuntimeError):
                    live.validate_service_receipts(arm)

    def test_journal_parser_rejects_cross_invocation_forgery(self):
        unit, invocation = "gate.service", "a" * 32
        raw = (
            f"_SYSTEMD_USER_UNIT={unit}\n"
            f"_SYSTEMD_INVOCATION_ID={invocation}\n"
            "MESSAGE=started\n\n"
        ).encode()
        self.assertEqual(live.validate_journal(raw, unit, invocation), 1)
        forged = raw.replace(invocation.encode(), b"b" * 32)
        with self.assertRaisesRegex(RuntimeError, "forged|lacks"):
            live.validate_journal(forged, unit, invocation)

    def test_manager_systemd_proc_journal_chain_rejects_forgery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "d32-g32"
            arm = {
                "root": str(root),
                "unit": "umt-pki4-gate-b-test.service",
                "gem5_argv": ["gem5", "--dot-config="],
                "wrapper_argv": ["/usr/bin/python3", "/pinned/wrapper.py"],
                "gem5_argv_sha256": "a" * 64,
                "wrapper_argv_sha256": "b" * 64,
                "systemd_run_argv_sha256": "c" * 64,
                "manager_evidence": {
                    "live_show": str(
                        root / ".manager-owned/systemd-live.show"
                    ),
                    "terminal_show": str(
                        root / ".manager-owned/systemd-terminal.show"
                    ),
                    "proc_start": str(root / ".manager-owned/proc-start.json"),
                    "journal_export": str(
                        root / ".manager-owned/systemd-journal.export"
                    ),
                },
            }
            service_fixture(root, arm)
            proc_path, journal_path = manager_fixture(root, arm, "d" * 64)
            result = live.validate_manager_terminal(arm, "d" * 64)
            self.assertEqual(result["value"]["invocation_id"], "a" * 32)

            proc = live.read_json_nofollow(proc_path)
            proc["invocation_id"] = "e" * 32
            write_json(proc_path, proc)
            with self.assertRaisesRegex(RuntimeError, "receipt mismatch"):
                live.validate_manager_terminal(arm, "d" * 64)
            proc["invocation_id"] = "a" * 32
            write_json(proc_path, proc)

            journal_path.write_bytes(
                journal_path.read_bytes().replace(b"a" * 32, b"f" * 32)
            )
            with self.assertRaisesRegex(RuntimeError, "forged|lacks"):
                live.validate_manager_terminal(arm, "d" * 64)

    def test_snapshot_rejects_source_mutation_during_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "d32-g32"
            root.mkdir()
            source = root / "gem5.stderr"
            source.write_bytes(b"x" * (post.CHUNK * 2 + 17))
            status = source.stat()
            arm = {
                "root": str(root),
                "terminal_snapshot": str(
                    root / "analysis/gate-b" / post.SNAPSHOT_NAME
                ),
            }
            binding = {
                "source": str(source),
                "device": status.st_dev,
                "inode": status.st_ino,
                "sha256": live.sha256(source),
                "service_terminal": {},
                "manager_terminal": {},
            }
            original = post.os.read
            changed = False

            def mutate(descriptor, count):
                nonlocal changed
                block = original(descriptor, count)
                if block and not changed:
                    changed = True
                    with source.open("r+b") as stream:
                        stream.seek(0)
                        stream.write(b"z")
                        stream.flush()
                        os.fsync(stream.fileno())
                return block

            with (
                mock.patch.object(
                    post, "terminal_binding", return_value=binding
                ),
                mock.patch.object(post.os, "read", side_effect=mutate),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "changed|mismatched"
                ):
                    post.capture_snapshot(arm, "c" * 64)
            self.assertFalse(pathlib.Path(arm["terminal_snapshot"]).exists())

    def test_split_rejects_truncation_bad_prefix_and_cross_prefix_schema(self):
        valid_v3 = {
            "schema": post.V3_RAW_SCHEMA,
            "schema_version": 3,
        }
        valid_v4 = {
            "schema": post.V4_RAW_SCHEMA,
            "schema_version": 1,
        }
        cases = {
            "truncated": post.V3_PREFIX + json.dumps(valid_v3).encode(),
            "embedded": b"noise "
            + post.V3_PREFIX
            + json.dumps(valid_v3).encode()
            + b"\n",
            "cross": post.V3_PREFIX + json.dumps(valid_v4).encode() + b"\n",
        }
        for name, bad in cases.items():
            with self.subTest(
                name=name
            ), tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary)
                snapshot = root / "snapshot"
                snapshot.write_bytes(
                    bad
                    + post.V4_PREFIX
                    + json.dumps(valid_v4).encode()
                    + b"\n"
                )
                descriptor = post.open_regular(snapshot)
                try:
                    evidence = {"sha256": live.sha256(snapshot)}
                    with self.assertRaises(RuntimeError):
                        post.split_prefix_streams(
                            descriptor,
                            evidence,
                            root / "v3.raw",
                            root / "v4.raw",
                        )
                finally:
                    os.close(descriptor)

    def test_split_positive_binds_both_streams_to_one_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            v3 = {"schema": post.V3_RAW_SCHEMA, "schema_version": 3}
            v4 = {"schema": post.V4_RAW_SCHEMA, "schema_version": 1}
            snapshot = root / "snapshot"
            snapshot.write_bytes(
                post.V3_PREFIX
                + json.dumps(v3).encode()
                + b"\n"
                + post.V4_PREFIX
                + json.dumps(v4).encode()
                + b"\n"
            )
            descriptor = post.open_regular(snapshot)
            try:
                evidence = {"sha256": live.sha256(snapshot)}
                result = post.split_prefix_streams(
                    descriptor, evidence, root / "v3.raw", root / "v4.raw"
                )
            finally:
                os.close(descriptor)
            self.assertEqual(result["canonical_v3"]["records"], 1)
            self.assertEqual(result["canonical_v4"]["records"], 1)
            self.assertEqual(
                result["input_snapshot_sha256"], evidence["sha256"]
            )

    def test_queue_reference_requires_c1_c2_depth_two_and_backpressure(self):
        document = queue_document()
        result = post.validate_queue_reference(document, "d32-g32")
        self.assertEqual(result["c_plus_1_source_commits"], 1)
        self.assertEqual(result["c_plus_2_same_bank_source_commits"], 1)
        self.assertEqual(result["maximum_depth"], 2)

        wrong_tick = copy.deepcopy(document)
        wrong_tick["issue_decisions"][0]["next_engine_tick"] += 1
        with self.assertRaisesRegex(RuntimeError, r"not C\+1"):
            post.validate_queue_reference(wrong_tick, "d32-g32")

        bad_backpressure = copy.deepcopy(document)
        bad_backpressure["callbacks"][0]["lanes"][2]["error"] = 0
        with self.assertRaisesRegex(RuntimeError, "backpressure"):
            post.validate_queue_reference(bad_backpressure, "d32-g32")

        depth_three = copy.deepcopy(document)
        lane = copy.deepcopy(depth_three["callbacks"][0]["lanes"][1])
        lane.update({"order": 3, "group": 8, "row": 2, "payload_word": "0x3"})
        depth_three["callbacks"][0]["lanes"].append(lane)
        with self.assertRaisesRegex(RuntimeError, "depth two"):
            post.validate_queue_reference(depth_three, "d32-g32")

    def test_lifecycle_full_drain_reuse_all_free_and_request_cross_stream(
        self,
    ):
        v4 = lifecycle_document()
        result = post.validate_lifecycle(v4)
        self.assertTrue(result["full_drain"])
        self.assertTrue(result["reuse_marker_present"])
        v3 = cross_v3(v4)
        cross = post.validate_cross_stream(v3, v4)
        self.assertEqual(cross["matched_admissions"], 2)

        wrong_request = copy.deepcopy(v4)
        admission = next(
            event
            for event in wrong_request["events"]
            if event["phase"] == "token_admission"
        )
        admission["request_id"] += 1
        with self.assertRaisesRegex(RuntimeError, "diverge"):
            post.validate_cross_stream(v3, wrong_request)

        no_reuse = copy.deepcopy(v4)
        no_reuse["phase_counts"]["token_reuse"] = 0
        with self.assertRaisesRegex(RuntimeError, "phase"):
            post.validate_lifecycle(no_reuse)

        not_free = copy.deepcopy(v4)
        not_free["events"][-1]["token_free_post_mask"] &= ~1
        with self.assertRaisesRegex(RuntimeError, "all-free"):
            post.validate_lifecycle(not_free)

    def test_correctness_work_equations_fail_closed_before_mechanism(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / "gem5.stdout").write_text(
                "LANLMAA_UMT_INGRESS_TERMINAL code=0\n", encoding="utf-8"
            )
            (root / "app.stdout").write_text(
                "RESULT CHECK PASSED:\n", encoding="utf-8"
            )
            (root / "app.stderr").write_text("", encoding="utf-8")
            write_json(root / "submission.json", {})
            (root / "m5out").mkdir()
            (root / "m5out/stats.txt").write_text("stats\n", encoding="utf-8")
            calls, groups = 2, 64
            expected = {
                "descriptorDoorbells": calls,
                "descriptorFetches": calls * 4,
                "descriptorCompletionWrites": calls,
                "descriptorUmtD32Descriptors": calls,
                "descriptorUmtD64Descriptors": 0,
                "descriptorUmtGroupsLoaded": groups,
                "descriptorUmtInputReads": groups * 16,
                "descriptorUmtStateInputWrites": groups * 8,
                "descriptorUmtStateDenominatorsConsumed": groups * 8,
                "descriptorUmtStateResultWrites": groups * 8,
                "descriptorUmtResultsComputed": groups * 8,
            }
            with (
                mock.patch.object(
                    post.ingress,
                    "validate_submission",
                    return_value={
                        "wave_calls": calls,
                        "submitted_groups": groups,
                    },
                ),
                mock.patch.object(
                    post, "parse_stats_bytes", return_value=expected
                ),
            ):
                result = post.validate_correctness(root, "d32-g32")
                self.assertEqual(result["observed_work"], expected)
                bad = dict(expected)
                bad["descriptorUmtStateResultWrites"] -= 1
                with mock.patch.object(
                    post, "parse_stats_bytes", return_value=bad
                ):
                    with self.assertRaisesRegex(RuntimeError, "work equation"):
                        post.validate_correctness(root, "d32-g32")

    def test_provenance_states_no_canonical_v4_rtl_transactor_claim(self):
        provenance = post.verify_provenance()
        self.assertEqual(
            provenance["gate_b_router_40c"]["source_commit"]
            if "source_commit" in provenance["gate_b_router_40c"]
            else provenance["source_commit"],
            live.SOURCE_COMMIT,
        )
        plan = live.read_json_nofollow(live.DRY_PLAN)
        self.assertFalse(
            plan["rtl_full_successor_replay"]["rtl_launch_authorized"]
        )
        self.assertIn("blocked", plan["rtl_full_successor_replay"]["status"])


if __name__ == "__main__":
    unittest.main()
