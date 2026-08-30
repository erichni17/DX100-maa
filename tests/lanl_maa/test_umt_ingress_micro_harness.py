#!/usr/bin/env python3
"""Adversarial, dry-only tests for the v6 UMT ingress harness."""
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "ingress", HERE / "umt_ingress_micro_harness.py"
)
ingress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingress)
wrapper_spec = importlib.util.spec_from_file_location(
    "ingress_wrapper", HERE / "run_umt_ingress_build_attestation.py"
)
wrapper = importlib.util.module_from_spec(wrapper_spec)
wrapper_spec.loader.exec_module(wrapper)


def callback(kind, callback_id, lane, waiters, pre, post, abi=4):
    return f"UMT_INGRESS kind={kind} cycle={100 + callback_id} callback={callback_id} lane={lane} packet=0x10 line=0x10 abi={abi} stage=0 group=0 corner=0 order={lane} waiters={waiters} token={1000 + callback_id * 10 + lane} pre=0x{pre:x} post=0x{post:x} next_engine_tick={200 + callback_id}"


def line(label, kind, cycle, waiters, abi=4):
    return f"UMT_INGRESS kind={label}_{kind} cycle={cycle} line=0x10 abi={abi} stage=0 group=0 corner=0 waiters={waiters} pre=0x1 post=0x1"


class IngressHarnessTest(unittest.TestCase):
    def test_v6_build_spelling_and_sanitized_environment_are_exact(self):
        self.assertEqual(
            ingress.BUILD_ARGV,
            (
                "/usr/bin/scons",
                "--ignore-style",
                "build/X86_UMT_T32_W2/gem5.opt",
                "-j4",
                "CPPDEFINES=LANL_MAA_UMT_INGRESS_TRACE_TEST",
            ),
        )
        self.assertEqual(
            ingress.BUILD_ENVIRONMENT,
            {
                "sanitized": ["LANG", "LC_ALL", "PATH", "TZ"],
                "inherited_tool_affecting_names": [],
                "inherited_tool_affecting_count": 0,
            },
        )
        self.assertEqual(
            ingress.BUILD_UNIT, "umt-ingress-trace-build-v6-20260830.service"
        )

    def test_wrapper_environment_names_and_v1_gate_report_are_rejected(self):
        names = wrapper.inherited_tool_affecting_names(
            {
                "CC": "secret-compiler",
                "PYTHONPATH": "secret-path",
                "SAFE": "x",
            }
        )
        self.assertEqual(names, ["CC", "PYTHONPATH"])
        self.assertEqual(
            wrapper.SAFE_CHILD_ENV,
            {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "LC_ALL": "C",
                "LANG": "C",
                "TZ": "UTC",
            },
        )
        source, target, digest = (
            pathlib.Path("/source"),
            pathlib.Path("/target"),
            "a" * 64,
        )
        report = {
            "schema": "lanl-maa-umt-production-ingress-trace-v1",
            "status": "passed",
            "source_root": str(source),
            "input_source_sha256": {},
            "binary": str(target),
            "binary_sha256": digest,
            "required_define": ingress.TRACE_BUILD_DEFINE,
            "compiled_binary_markers": [
                "UMT_INGRESS kind=",
                "d64_hold cycle=",
            ],
            "cells": [],
        }
        with self.assertRaisesRegex(RuntimeError, "exact v2"):
            wrapper.validate_gate_report(report, source, target, digest, {})

    def rows(self, case):
        abi, rows, callback_id, digest = (
            (5 if case == "d64-g32" else 4),
            [],
            1,
            1,
        )

        def emit(kind, count):
            nonlocal callback_id, digest
            for lane_id in range(count):
                rows.append(
                    callback(
                        kind,
                        callback_id,
                        lane_id,
                        count,
                        digest,
                        digest + 1,
                        abi,
                    )
                )
                digest += 1
            callback_id += 1

        emit("source", 8)
        emit("denominator", 8)
        if case == "d32-g31":
            emit("source", 7)
            emit("denominator", 1)
        if case == "d64-g32":
            rows += [
                line("d64", "hold", 110 + count, count, abi)
                for count in range(1, 8)
            ]
            rows += [line("d64", "release", 118, 8, abi)]
        else:
            rows += [line("d32", "release", 110, 8, abi)]
        return rows

    def events(self, case):
        return ingress.parse_debug_file_text("\n".join(self.rows(case)))

    def test_valid_synthetic_witnesses(self):
        for case in ingress.CASES:
            self.assertGreater(
                ingress.validate_trace(self.events(case), case)["callbacks"], 0
            )

    def test_reversed_d64_holds_fail(self):
        rows = self.rows("d64-g32")
        holds = rows[-8:-1]
        rows[-8:-1] = list(reversed(holds))
        with self.assertRaisesRegex(
            RuntimeError, "chronology|chronologically hold"
        ):
            ingress.validate_trace(
                ingress.parse_debug_file_text("\n".join(rows)), "d64-g32"
            )

    def test_digest_reappearance_and_g31_boundary_fail(self):
        events = self.events("d32-g16")
        events[1]["pre"] = "0xdead"
        with self.assertRaisesRegex(RuntimeError, "digest"):
            ingress.validate_trace(events, "d32-g16")
        events = self.events("d32-g16")
        events[1]["callback"] = 2
        with self.assertRaisesRegex(RuntimeError, "reappears|contiguous"):
            ingress.validate_trace(events, "d32-g16")
        events = self.events("d32-g31")
        for event in events:
            if event["class"] == "callback" and event["waiters"] == 1:
                event["waiters"] = 2
        with self.assertRaisesRegex(RuntimeError, "G31|waiter"):
            ingress.validate_trace(events, "d32-g31")

    def submission(self, case):
        spec = ingress.CASES[case]
        calls = 2 if spec["groups"] in (16, 31) else 1
        selected = "d32" if spec["abi"] == "D32" else "d64"
        value = {key: 0 for key in ingress.SUBMISSION_FIELDS}
        value.update(
            {
                "schema": ingress.SCHEMA_SUBMISSION,
                "opcode": 11,
                "mode": "ordered_wave",
                "wave_calls": calls,
                "wave_corners": calls * 8,
                "direct_arena_submissions": calls,
                "direct_sink_result_words": 16,
                "direct_sink_phi_words": 16,
                "wave_descriptor_sum_area_words": 16,
                "wave_soa_arena_descriptors": calls,
                "descriptor_submissions": calls,
                "submitted_groups": calls * spec["groups"],
                "capability_probes": 1,
                "adaptive_wave_selector_threshold_groups": 32,
                "last_error": 0,
                "all_completions_valid": True,
                "ordered_corner_scalar_solves_replaced": True,
                f"wave_{selected}_descriptors": calls,
                f"wave_{selected}_groups": calls * spec["groups"],
                f"wave_{selected}_decisions": calls,
            }
        )
        return value

    def test_wrong_per_call_groups_and_abi_counters_fail(self):
        report = self.submission("d32-g31")
        report["submitted_groups"] -= 1
        with self.assertRaisesRegex(RuntimeError, "per-call group"):
            ingress.validate_submission(report, "d32-g31")
        report = self.submission("d64-g32")
        report["wave_d32_descriptors"] = 1
        with self.assertRaisesRegex(RuntimeError, "D64"):
            ingress.validate_submission(report, "d64-g32")

    def test_arbitrary_repo_and_dummy_proof_fail_before_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            gem5 = root / "gem5.opt"
            gem5.write_bytes(b"x")
            proof = root / "proof.json"
            proof.write_text(
                json.dumps(
                    {
                        "schema": ingress.SCHEMA_BUILD_PROOF,
                        "status": "passed",
                        "source_worktree": str(root),
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "exact schema"):
                ingress.read_build_proof(
                    proof,
                    ingress.sha256(proof),
                    gem5.resolve(),
                    ingress.sha256(gem5),
                )

    def test_complete_proof_rejects_relink_return_resources_and_observer_attacks(
        self,
    ):
        """Exercise the strict producer/validator boundary without a build."""
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "source"
            source.mkdir()
            source_hashes = {}
            for relative in ingress.INSTRUMENTATION_SOURCES:
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(relative, encoding="utf-8")
                source_hashes[relative] = ingress.sha256(target)
            build = source / "build/X86_UMT_T32_W2"
            build.mkdir(parents=True)
            gem5, config_hh, config_cc = (
                build / "gem5.opt",
                build / "config.hh",
                build / "config.cc",
            )
            for item in (gem5, config_hh, config_cc):
                item.write_text(item.name, encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=source, check=True)
            subprocess.run(["git", "add", "."], cwd=source, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=t",
                    "-c",
                    "user.email=t@t",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                cwd=source,
                check=True,
            )
            commit, tree = ingress.git_output(
                source, "rev-parse", "HEAD"
            ), ingress.git_output(source, "rev-parse", "HEAD^{tree}")

            def file(name, text="ok"):
                path = root / name
                path.write_text(text, encoding="utf-8")
                return {"path": str(path), "sha256": ingress.sha256(path)}

            invocation_id, pid, start_ticks = "a" * 32, 77, "12345"
            evidence = root / "wrapper-evidence"
            evidence.mkdir()

            def evidence_file(name, text="ok"):
                path = evidence / name
                path.write_text(text, encoding="utf-8")
                return {"path": str(path), "sha256": ingress.sha256(path)}

            def systemd_show(name, terminal=False):
                fields = {
                    "Id": ingress.BUILD_UNIT,
                    "InvocationID": invocation_id,
                    "MainPID": "0" if terminal else str(pid),
                    "ExecMainPID": str(pid),
                    "ExecMainStartTimestampMonotonic": "999999",
                    "WorkingDirectory": str(source),
                    **ingress.RESOURCE_POLICY,
                    "ExecStart": (
                        "{ path=/usr/bin/python3 ; argv[]="
                        + " ".join(ingress.wrapper_command(evidence))
                        + " ; ignore_errors=no ; start_time=[n/a] ; }"
                    ),
                    "Environment": "",
                    "ExecMainCode": "1" if terminal else "",
                    "ExecMainStatus": "0" if terminal else "",
                    "Result": "success" if terminal else "",
                }
                return file(
                    name,
                    "\n".join(
                        f"{key}={fields[key]}"
                        for key in ingress.SYSTEMD_SHOW_PROPERTIES
                    )
                    + "\n",
                )

            receipt_path = root / "proc-start.json"
            receipt_path.write_text(
                json.dumps(
                    {
                        "schema": "lanl-maa-proc-start-receipt-v1",
                        "pid": pid,
                        "proc_start_ticks": start_ticks,
                        "invocation_id": invocation_id,
                        "exec_main_start_timestamp_monotonic": "999999",
                    }
                ),
                encoding="utf-8",
            )
            receipt = {
                "path": str(receipt_path),
                "sha256": ingress.sha256(receipt_path),
            }
            artifacts = {
                "gem5": {"path": str(gem5), "sha256": ingress.sha256(gem5)},
                "config_hh": {
                    "path": str(config_hh),
                    "sha256": ingress.sha256(config_hh),
                },
                "config_cc": {
                    "path": str(config_cc),
                    "sha256": ingress.sha256(config_cc),
                },
            }
            attestation_value = {
                "schema": "lanl-maa-umt-ingress-build-attestation-v6",
                "unit": ingress.BUILD_UNIT,
                "invocation_id": invocation_id,
                "wrapper_pid": pid,
                "wrapper_proc_start_ticks": start_ticks,
                "status": "passed",
                "build_argv": list(ingress.BUILD_ARGV),
                "build_environment": ingress.BUILD_ENVIRONMENT,
                "build_returncode": 0,
                "required_relink_observed": True,
                "instrumentation_source_sha256": source_hashes,
                "build_artifacts": {
                    key: value["sha256"] for key, value in artifacts.items()
                },
                "compiled_binary_markers": [
                    "UMT_INGRESS kind=",
                    "d64_hold cycle=",
                ],
                "observer_gate": {
                    "command": [
                        "/usr/bin/python3",
                        str(
                            source
                            / "tests/lanl_maa/run_umt_production_ingress_trace_gate.py"
                        ),
                        "--cxx",
                        "g++",
                        "--binary",
                        str(gem5),
                        "--binary-sha256",
                        ingress.sha256(gem5),
                        "--input-source-sha256",
                        str(evidence / "observer-input-source-sha256.json"),
                    ],
                    "returncode": 0,
                    "report": {},
                    "transcript": {},
                },
                "evidence": {},
            }
            attestation = evidence / "attestation.json"
            attestation.write_text(
                json.dumps(attestation_value), encoding="utf-8"
            )

            def export_record(fields):
                return (
                    b"".join(
                        key.encode() + b"=" + value + b"\n"
                        for key, value in fields.items()
                    )
                    + b"\n"
                )

            start_marker = ingress.journal_marker(
                "START",
                invocation=invocation_id,
                pid=pid,
                proc_start_ticks=start_ticks,
            )
            success_marker = ingress.journal_marker(
                "SUCCESS",
                invocation=invocation_id,
                pid=pid,
                proc_start_ticks=start_ticks,
                target_sha256=ingress.sha256(gem5),
            )
            journal_path = root / "build.journal"
            journal_path.write_bytes(
                export_record({"MESSAGE": b"unrelated\x00binary"})
                + export_record(
                    {
                        "_SYSTEMD_USER_UNIT": ingress.BUILD_UNIT.encode(),
                        "_SYSTEMD_INVOCATION_ID": invocation_id.encode(),
                        "_PID": str(pid).encode(),
                        "MESSAGE": start_marker,
                    }
                )
                + export_record(
                    {
                        "USER_UNIT": ingress.BUILD_UNIT.encode(),
                        "USER_INVOCATION_ID": invocation_id.encode(),
                        "MESSAGE": b"systemd manager record",
                    }
                )
                + export_record(
                    {
                        "_SYSTEMD_USER_UNIT": ingress.BUILD_UNIT.encode(),
                        "_SYSTEMD_INVOCATION_ID": invocation_id.encode(),
                        "_PID": str(pid).encode(),
                        "MESSAGE": success_marker,
                    }
                )
            )
            journal = {
                "path": str(journal_path),
                "sha256": ingress.sha256(journal_path),
            }
            report = evidence / "observer-report.json"
            report.write_text(
                json.dumps(
                    {
                        "schema": "lanl-maa-umt-production-ingress-trace-v2",
                        "status": "passed",
                        "source_root": str(source),
                        "input_source_sha256": source_hashes,
                        "binary": str(gem5),
                        "binary_sha256": ingress.sha256(gem5),
                        "required_define": ingress.TRACE_BUILD_DEFINE,
                        "compiled_binary_markers": [
                            "UMT_INGRESS kind=",
                            "d64_hold cycle=",
                        ],
                        "cells": [
                            {
                                "tokens": t,
                                "issue_width": w,
                                "waiter_counts": [1, 7, 8],
                                "abi_boundaries": ["D32", "D64"],
                                "two_lane_serialization": "rejected_by_trace_difference",
                                "default_off": "compiled_without_observer_macro",
                            }
                            for t, w in ((24, 1), (24, 2), (32, 1), (32, 2))
                        ],
                    }
                ),
                encoding="utf-8",
            )
            evidence_items = {
                "scons_stdout": evidence_file("scons.stdout"),
                "scons_stderr": evidence_file("scons.stderr"),
                "observer_stdout": evidence_file("observer.stdout"),
                "observer_stderr": evidence_file("observer.stderr"),
                "observer_report": {
                    "path": str(report),
                    "sha256": ingress.sha256(report),
                },
                "observer_transcript": evidence_file(
                    "observer-transcript.txt", "status=0/SUCCESS\n"
                ),
                "source_manifest": evidence_file(
                    "observer-input-source-sha256.json",
                    json.dumps(source_hashes),
                ),
                "target_config_literal_scan": evidence_file(
                    "target-config-literal-scan.json",
                    json.dumps(
                        {
                            "target": str(gem5),
                            "target_sha256": ingress.sha256(gem5),
                            "config_hh": str(config_hh),
                            "config_hh_sha256": ingress.sha256(config_hh),
                            "config_cc": str(config_cc),
                            "config_cc_sha256": ingress.sha256(config_cc),
                            "compiled_binary_markers": [
                                "UMT_INGRESS kind=", "d64_hold cycle=",
                            ],
                        }
                    ),
                ),
            }
            attestation_value["evidence"] = evidence_items
            attestation_value["observer_gate"]["report"] = evidence_items[
                "observer_report"
            ]
            attestation_value["observer_gate"]["transcript"] = evidence_items[
                "observer_transcript"
            ]
            attestation.write_text(
                json.dumps(attestation_value), encoding="utf-8"
            )
            proof = {
                "schema": ingress.SCHEMA_BUILD_PROOF,
                "status": "passed",
                "producer": "systemd-build-proof-v6-service-wrapper",
                "source_worktree": str(source),
                "source_commit": commit,
                "source_tree": tree,
                "source_clean_before": True,
                "source_clean_after": True,
                "source_identity_unchanged": True,
                "gem5": str(gem5),
                "gem5_sha256": ingress.sha256(gem5),
                "build_cwd": str(source),
                "build_argv": list(ingress.BUILD_ARGV),
                "build_environment": ingress.BUILD_ENVIRONMENT,
                "trace_define": ingress.TRACE_BUILD_DEFINE,
                "instrumentation_source_sha256": source_hashes,
                "build_returncode": 0,
                "required_relink_observed": True,
                "build_stdout": evidence_items["scons_stdout"],
                "build_stderr": evidence_items["scons_stderr"],
                "build_artifacts": artifacts,
                "build_invocation": {
                    "unit": ingress.BUILD_UNIT,
                    "show_command": list(ingress.BUILD_SHOW_COMMAND),
                    "live_systemd_show": systemd_show("live.show"),
                    "terminal_systemd_show": systemd_show(
                        "terminal.show", terminal=True
                    ),
                    "live_process_start_receipt": receipt,
                    "journal_command": list(ingress.BUILD_JOURNAL_COMMAND),
                    "journal": journal,
                    "journal_terminal_protocol": ingress.JOURNAL_TERMINAL_PROTOCOL,
                    "wrapper": {
                        "path": str(ingress.BUILD_WRAPPER),
                        "sha256": ingress.sha256(ingress.BUILD_WRAPPER),
                    },
                    "wrapper_command": list(ingress.wrapper_command(evidence)),
                    "wrapper_attestation": {
                        "path": str(attestation),
                        "sha256": ingress.sha256(attestation),
                    },
                },
                "observer_gate": {
                    "command": [
                        "/usr/bin/python3",
                        str(
                            source
                            / "tests/lanl_maa/run_umt_production_ingress_trace_gate.py"
                        ),
                        "--cxx",
                        "g++",
                        "--binary",
                        str(gem5),
                        "--binary-sha256",
                        ingress.sha256(gem5),
                        "--input-source-sha256",
                        str(evidence / "observer-input-source-sha256.json"),
                    ],
                    "input_source_sha256": source_hashes,
                    "binary": str(gem5),
                    "binary_sha256": ingress.sha256(gem5),
                    "stdout": evidence_items["observer_stdout"],
                    "stderr": evidence_items["observer_stderr"],
                    "report": evidence_items["observer_report"],
                    "transcript": evidence_items["observer_transcript"],
                    "status": "passed",
                },
            }

            def attempt(value):
                proof_path = root / "proof.json"
                proof_path.write_text(json.dumps(value), encoding="utf-8")
                return ingress.read_build_proof(
                    proof_path,
                    ingress.sha256(proof_path),
                    gem5.resolve(),
                    ingress.sha256(gem5),
                )

            patches = {
                "CANONICAL_SOURCE_ROOT": str(source),
                "CANONICAL_SOURCE": source,
                "CANONICAL_SOURCE_COMMIT": commit,
                "CANONICAL_SOURCE_TREE": tree,
                "CANONICAL_GEM5": gem5,
                "INSTRUMENTATION_SOURCES": source_hashes,
            }
            with mock.patch.multiple(ingress, **patches):
                proof["build_invocation"]["wrapper_command"] = list(
                    ingress.wrapper_command(evidence)
                )
                for field in ("live_systemd_show", "terminal_systemd_show"):
                    old = pathlib.Path(
                        proof["build_invocation"][field]["path"]
                    )
                    updated = old.read_text(encoding="utf-8").replace(
                        "argv[]="
                        + " ".join(
                            (
                                "/usr/bin/python3",
                                str(ingress.BUILD_WRAPPER),
                                "--unit",
                                ingress.BUILD_UNIT,
                                "--source",
                                "/data1/nier/worktrees/DX100-umt-trace-replay-20260830",
                                "--evidence-dir",
                                str(evidence.resolve()),
                            )
                        ),
                        "argv[]="
                        + " ".join(ingress.wrapper_command(evidence)),
                    )
                    old.write_text(updated, encoding="utf-8")
                    proof["build_invocation"][field][
                        "sha256"
                    ] = ingress.sha256(old)
                self.assertEqual(attempt(proof), root / "proof.json")

                # v1 looked superficially valid to the earlier harness.  It
                # lacks the source/binary binding fields and must be rejected
                # even when every enclosing evidence hash is refreshed.
                v1 = json.loads(report.read_text(encoding="utf-8"))
                v1["schema"] = "lanl-maa-umt-production-ingress-trace-v1"
                report.write_text(json.dumps(v1), encoding="utf-8")
                refreshed_report = {
                    "path": str(report),
                    "sha256": ingress.sha256(report),
                }
                attestation_value["evidence"][
                    "observer_report"
                ] = refreshed_report
                attestation_value["observer_gate"]["report"] = refreshed_report
                attestation.write_text(
                    json.dumps(attestation_value), encoding="utf-8"
                )
                v1_proof = json.loads(json.dumps(proof))
                v1_proof["observer_gate"]["report"] = refreshed_report
                v1_proof["build_invocation"]["wrapper_attestation"] = {
                    "path": str(attestation),
                    "sha256": ingress.sha256(attestation),
                }
                with self.assertRaisesRegex(RuntimeError, "report/transcript"):
                    attempt(v1_proof)
                report.write_text(
                    json.dumps(
                        {
                            **v1,
                            "schema": "lanl-maa-umt-production-ingress-trace-v2",
                        }
                    ),
                    encoding="utf-8",
                )
                refreshed_report = {
                    "path": str(report),
                    "sha256": ingress.sha256(report),
                }
                attestation_value["evidence"][
                    "observer_report"
                ] = refreshed_report
                attestation_value["observer_gate"]["report"] = refreshed_report
                attestation.write_text(
                    json.dumps(attestation_value), encoding="utf-8"
                )
                proof["observer_gate"]["report"] = refreshed_report
                proof["build_invocation"]["wrapper_attestation"] = {
                    "path": str(attestation),
                    "sha256": ingress.sha256(attestation),
                }
                self.assertEqual(attempt(proof), root / "proof.json")

                forged = json.loads(json.dumps(proof))
                forged["build_environment"][
                    "inherited_tool_affecting_count"
                ] = 1
                with self.assertRaisesRegex(
                    RuntimeError, "sanitized environment"
                ):
                    attempt(forged)

                def replace_invocation_artifact(
                    forged, field, suffix, transform
                ):
                    old = forged["build_invocation"][field]
                    altered = root / ("forged-" + suffix)
                    altered.write_text(
                        transform(
                            pathlib.Path(old["path"]).read_text(
                                encoding="utf-8"
                            )
                        ),
                        encoding="utf-8",
                    )
                    forged["build_invocation"][field] = {
                        "path": str(altered),
                        "sha256": ingress.sha256(altered),
                    }

                for field, value in (
                    ("required_relink_observed", False),
                    ("build_returncode", 1),
                ):
                    forged = dict(proof)
                    forged[field] = value
                    with self.assertRaisesRegex(
                        RuntimeError, "command/environment"
                    ):
                        attempt(forged)
                forged = json.loads(json.dumps(proof))
                replace_invocation_artifact(
                    forged,
                    "live_systemd_show",
                    "bad-resource.show",
                    lambda text: text.replace(
                        "MemoryMax=" + ingress.RESOURCE_POLICY["MemoryMax"],
                        "MemoryMax=1",
                    ),
                )
                with self.assertRaises(RuntimeError):
                    attempt(forged)
                forged = json.loads(json.dumps(proof))
                forged["build_invocation"]["unit"] = "forged.service"
                with self.assertRaises(RuntimeError):
                    attempt(forged)
                for field, suffix, transform in (
                    (
                        "live_systemd_show",
                        "bad-pid.show",
                        lambda text: text.replace("MainPID=77", "MainPID=78"),
                    ),
                    (
                        "terminal_systemd_show",
                        "bad-invocation.show",
                        lambda text: text.replace(
                            "InvocationID=" + invocation_id,
                            "InvocationID=" + "b" * 32,
                        ),
                    ),
                    (
                        "live_systemd_show",
                        "bad-start.show",
                        lambda text: text.replace(
                            "ExecMainStartTimestampMonotonic=999999",
                            "ExecMainStartTimestampMonotonic=999998",
                        ),
                    ),
                    (
                        "live_systemd_show",
                        "bad-cwd.show",
                        lambda text: text.replace(
                            "WorkingDirectory=" + str(source),
                            "WorkingDirectory=/forged",
                        ),
                    ),
                    (
                        "live_systemd_show",
                        "bad-command.show",
                        lambda text: text.replace(
                            " --unit ", " --forged-unit "
                        ),
                    ),
                ):
                    forged = json.loads(json.dumps(proof))
                    replace_invocation_artifact(
                        forged, field, suffix, transform
                    )
                    with self.assertRaises(RuntimeError):
                        attempt(forged)
                forged = json.loads(json.dumps(proof))
                bad_receipt = root / "forged-proc-start.json"
                bad_receipt.write_text(
                    json.dumps(
                        {
                            "schema": "lanl-maa-proc-start-receipt-v1",
                            "pid": pid,
                            "proc_start_ticks": "99999",
                            "invocation_id": invocation_id,
                            "exec_main_start_timestamp_monotonic": "999999",
                        }
                    ),
                    encoding="utf-8",
                )
                forged["build_invocation"]["live_process_start_receipt"] = {
                    "path": str(bad_receipt),
                    "sha256": ingress.sha256(bad_receipt),
                }
                with self.assertRaises(RuntimeError):
                    attempt(forged)
                for suffix, transform in (
                    (
                        "wrapper-argv",
                        lambda text: text.replace(
                            "CPPDEFINES=LANL_MAA_UMT_INGRESS_TRACE_TEST",
                            "CCFLAGS_EXTRA=-DLANL_MAA_UMT_INGRESS_TRACE_TEST=1",
                        ),
                    ),
                    (
                        "wrapper-marker",
                        lambda text: text.replace(
                            "d64_hold cycle=", "missing-marker"
                        ),
                    ),
                ):
                    forged = json.loads(json.dumps(proof))
                    replace_invocation_artifact(
                        forged,
                        "wrapper_attestation",
                        suffix + ".json",
                        transform,
                    )
                    with self.assertRaises(RuntimeError):
                        attempt(forged)
                forged = json.loads(json.dumps(proof))
                forged["build_invocation"]["wrapper_command"][
                    -1
                ] = "/forged-evidence"
                with self.assertRaises(RuntimeError):
                    attempt(forged)
                for suffix, transform in (
                    (
                        "journal-substring",
                        lambda text: text.replace(
                            success_marker.decode(),
                            "prefix " + success_marker.decode() + " suffix",
                        ),
                    ),
                    (
                        "journal-target",
                        lambda text: text.replace(
                            "target_sha256", "forged_target_sha256"
                        ),
                    ),
                ):
                    forged = json.loads(json.dumps(proof))
                    replace_invocation_artifact(
                        forged, "journal", suffix + ".export", transform
                    )
                    with self.assertRaises(RuntimeError):
                        attempt(forged)
                forged = json.loads(json.dumps(proof))
                forged["observer_gate"]["command"][-1] = "clang++"
                with self.assertRaisesRegex(RuntimeError, "observer gate"):
                    attempt(forged)
                forged = json.loads(json.dumps(proof))
                forged["source_worktree"] = str(root / "arbitrary-repo")
                with self.assertRaisesRegex(
                    RuntimeError, "canonical instrumented source"
                ):
                    attempt(forged)

    def test_forged_resources_self_hash_and_wrong_unit_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            contract = {
                "schema": "lanl-maa-umt-ingress-contract-v4",
                "self_sha256": "0" * 64,
            }
            path = root / "ingress-contract-v4.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(
                RuntimeError, "externally fixed|semantics"
            ):
                ingress.dispatch_plan(
                    path,
                    ingress.sha256(path),
                    root,
                    root / "identity/ingress-dry-dispatch-v4.json",
                )

    def test_dispatch_cpu_quota_mapping_is_explicit_and_complete(self):
        command = ingress.systemd_run_command("arm.service", ["/bin/true"])
        self.assertEqual(
            command[:10],
            [
                "systemd-run",
                "--user",
                "--collect",
                "--unit=arm.service",
                "--property=CPUQuota=400%",
                "--property=CPUWeight=1000",
                "--property=MemoryHigh=" + str(14 * 1024**3),
                "--property=MemoryMax=" + str(16 * 1024**3),
                "--property=MemorySwapMax=0",
                "--property=RuntimeMaxUSec=4h",
            ],
        )
        self.assertNotIn("--property=CPUQuotaPerSecUSec=4s", command)

    def test_binary_export_journal_requires_bound_ordered_wrapper_markers(
        self,
    ):
        """Exercise the real export framing, not a newline-only approximation."""
        invocation, pid, ticks, digest = "c" * 32, 91, "4567", "d" * 64
        start = ingress.journal_marker(
            "START", invocation=invocation, pid=pid, proc_start_ticks=ticks
        )
        success = ingress.journal_marker(
            "SUCCESS",
            invocation=invocation,
            pid=pid,
            proc_start_ticks=ticks,
            target_sha256=digest,
        )

        def record(fields):
            encoded = b""
            for key, value in fields.items():
                if isinstance(value, tuple):
                    encoded += (
                        key.encode()
                        + b"\n"
                        + len(value[0]).to_bytes(8, "little")
                        + value[0]
                        + b"\n"
                    )
                else:
                    encoded += key.encode() + b"=" + value + b"\n"
            return encoded + b"\n"

        bound = {
            "_SYSTEMD_USER_UNIT": ingress.BUILD_UNIT.encode(),
            "_SYSTEMD_INVOCATION_ID": invocation.encode(),
            "_PID": str(pid).encode(),
        }
        raw = (
            record(
                {"BINARY": (b"unrelated\x00bytes",), "MESSAGE": b"ordinary"}
            )
            + record({**bound, "MESSAGE": start})
            + record(
                {
                    "USER_UNIT": ingress.BUILD_UNIT.encode(),
                    "USER_INVOCATION_ID": invocation.encode(),
                    "MESSAGE": b"manager",
                }
            )
            + record({**bound, "MESSAGE": success})
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "journal.export"
            path.write_bytes(raw)
            ingress.parse_export_journal(path, invocation, pid, ticks, digest)
            for bad in (
                raw + record({**bound, "MESSAGE": success}),
                raw.replace(success, b"prefix " + success + b" suffix"),
                raw.replace(
                    b"_SYSTEMD_INVOCATION_ID=" + invocation.encode(),
                    b"_SYSTEMD_INVOCATION_ID=" + b"e" * 32,
                    1,
                ),
            ):
                path.write_bytes(bad)
                with self.assertRaises(RuntimeError):
                    ingress.parse_export_journal(
                        path, invocation, pid, ticks, digest
                    )


if __name__ == "__main__":
    unittest.main()
