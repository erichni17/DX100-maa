#!/usr/bin/env python3
"""Adversarial, dry-only tests for the v4 UMT ingress harness."""
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


def callback(kind, callback_id, lane, waiters, pre, post, abi=4):
    return f"UMT_INGRESS kind={kind} cycle={100 + callback_id} callback={callback_id} lane={lane} packet=0x10 line=0x10 abi={abi} stage=0 group=0 corner=0 order={lane} waiters={waiters} token={1000 + callback_id * 10 + lane} pre=0x{pre:x} post=0x{post:x} next_engine_tick={200 + callback_id}"


def line(label, kind, cycle, waiters, abi=4):
    return f"UMT_INGRESS kind={label}_{kind} cycle={cycle} line=0x10 abi={abi} stage=0 group=0 corner=0 waiters={waiters} pre=0x1 post=0x1"


class IngressHarnessTest(unittest.TestCase):
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
                        "{ path=/usr/bin/scons ; argv[]="
                        + " ".join(ingress.BUILD_ARGV)
                        + " ; ignore_errors=no ; start_time=[n/a] ; }"
                    ),
                    "Environment": "CPPDEFINES=" + ingress.TRACE_BUILD_DEFINE,
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
            marker = (
                f"{ingress.JOURNAL_TERMINAL_PROTOCOL} unit={ingress.BUILD_UNIT} "
                f"invocation={invocation_id} pid={pid} "
                f"proc_start_ticks={start_ticks} result=SUCCESS exit=0"
            )
            journal = file(
                "build.journal",
                "_SYSTEMD_UNIT="
                + ingress.BUILD_UNIT
                + "\n"
                + "INVOCATION_ID="
                + invocation_id
                + "\n"
                + "MESSAGE="
                + marker
                + "\n\n",
            )

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
            report = root / "observer-report.json"
            report.write_text(
                json.dumps(
                    {
                        "schema": "lanl-maa-umt-production-ingress-trace-v1",
                        "status": "passed",
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
            proof = {
                "schema": ingress.SCHEMA_BUILD_PROOF,
                "status": "passed",
                "producer": "systemd-build-proof-v4",
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
                "build_stdout": file("build.out"),
                "build_stderr": file("build.err"),
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
                },
                "observer_gate": {
                    "command": [
                        "python3",
                        str(
                            source
                            / "tests/lanl_maa/run_umt_production_ingress_trace_gate.py"
                        ),
                        "--cxx",
                        "g++",
                    ],
                    "input_source_sha256": source_hashes,
                    "binary": str(gem5),
                    "binary_sha256": ingress.sha256(gem5),
                    "stdout": file("gate.out"),
                    "stderr": file("gate.err"),
                    "report": {
                        "path": str(report),
                        "sha256": ingress.sha256(report),
                    },
                    "transcript": file("gate.journal", "status=0/SUCCESS"),
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
                self.assertEqual(attempt(proof), root / "proof.json")

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
                        lambda text: text.replace(" -j4 ", " -j8 "),
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
                        "journal-substring",
                        lambda text: text.replace(
                            marker, "prefix " + marker + " suffix"
                        ),
                    ),
                    (
                        "journal-exit",
                        lambda text: text.replace("exit=0", "exit=1"),
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


if __name__ == "__main__":
    unittest.main()
