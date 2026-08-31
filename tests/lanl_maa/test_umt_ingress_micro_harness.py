#!/usr/bin/env python3
"""Adversarial dry tests for the combined v13 UMT ingress harness."""
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
arm_wrapper_spec = importlib.util.spec_from_file_location(
    "ingress_arm_wrapper", HERE / "run_umt_ingress_micro_arm.py"
)
arm_wrapper = importlib.util.module_from_spec(arm_wrapper_spec)
arm_wrapper_spec.loader.exec_module(arm_wrapper)


def callback(kind, callback_id, lane, waiters, pre, post, abi=4, group=0):
    return (
        f"UMT_INGRESS kind={kind} cycle={100 + callback_id} "
        f"callback={callback_id} lane={lane} packet=0x10 line=0x10 "
        f"abi={abi} stage=0 group={group} corner=0 order={lane} "
        f"waiters={waiters} token={1000 + callback_id * 10 + lane} "
        f"pre=0x{pre:x} post=0x{post:x} "
        f"next_engine_tick={200 + callback_id}"
    )


def line(label, kind, cycle, waiters, abi=4):
    return f"UMT_INGRESS kind={label}_{kind} cycle={cycle} line=0x10 abi={abi} stage=0 group=0 corner=0 waiters={waiters} pre=0x1 post=0x1"


class IngressHarnessTest(unittest.TestCase):
    def test_v13_contract_accepts_exactly_the_v13_build_proof_generation(self):
        self.assertEqual(
            ingress.SCHEMA_CONTRACT,
            "lanl-maa-umt-ingress-contract-v13",
        )
        self.assertEqual(
            ingress.SCHEMA_BUILD_PROOF,
            "lanl-maa-umt-ingress-instrumented-gem5-build-proof-v13",
        )
        self.assertEqual(
            ingress.SCHEMA_DISPATCH_PLAN,
            "lanl-maa-umt-ingress-dispatch-plan-v13",
        )
        self.assertEqual(
            ingress.SCHEMA_ARM_REPORT,
            "lanl-maa-umt-ingress-arm-report-v13",
        )
        self.assertEqual(
            ingress.CONTRACT_FILENAME, "ingress-contract-v13.json"
        )
        self.assertEqual(
            ingress.DISPATCH_FILENAME,
            "ingress-dry-dispatch-v13.json",
        )
        self.assertTrue(
            all(
                value.startswith("umt-ingress-micro-v13-")
                for value in (
                    f"umt-ingress-micro-v13-{case}-20260830.service"
                    for case in ingress.CASES
                )
            )
        )

    def test_v13_build_spelling_and_sanitized_environment_are_exact(self):
        self.assertEqual(
            ingress.BUILD_ARGV,
            (
                "/usr/bin/scons",
                "--ignore-style",
                "build/X86_UMT_T32_W2/gem5.opt",
                "-j4",
            ),
        )
        self.assertEqual(
            ingress.BUILD_ENVIRONMENT,
            {
                "sanitized": [
                    "CCFLAGS_EXTRA",
                    "LANG",
                    "LC_ALL",
                    "PATH",
                    "TZ",
                ],
                "fixed_values": {
                    "CCFLAGS_EXTRA": "-DLANL_MAA_UMT_INGRESS_TRACE_TEST"
                },
                "inherited_tool_affecting_names": [],
                "inherited_tool_affecting_count": 0,
            },
        )
        self.assertEqual(
            ingress.OBJECT_PREBUILD_ARGV,
            (
                "/usr/bin/scons",
                "--ignore-style",
                "--verbose",
                "build/X86_UMT_T32_W2/mem/LANLMAA/lanl_maa.o",
                "-j1",
            ),
        )
        self.assertEqual(
            ingress.BUILD_UNIT, "umt-ingress-trace-build-v13-20260830.service"
        )
        self.assertEqual(
            ingress.SCHEMA_BUILD_PROOF,
            "lanl-maa-umt-ingress-instrumented-gem5-build-proof-v13",
        )

    def test_build_argv_is_assignment_free_and_fixed_env_has_exact_dash_d(
        self,
    ):
        valid = (
            "/usr/bin/scons",
            "--ignore-style",
            "build/X86_UMT_T32_W2/gem5.opt",
            "-j4",
        )
        for validator in (
            wrapper.validate_build_argv,
            ingress.validate_build_argv,
        ):
            validator(valid)
            for assignment in (
                "CPPDEFINES=LANL_MAA_UMT_INGRESS_TRACE_TEST",
                "CCFLAGS_EXTRA=-DLANL_MAA_UMT_INGRESS_TRACE_TEST",
            ):
                with self.assertRaisesRegex(RuntimeError, "assignment-free"):
                    invalid = valid + (assignment,)
                    validator(invalid)
        wrapper.validate_safe_child_environment(wrapper.SAFE_CHILD_ENV)
        ingress.validate_fixed_build_environment(
            {"CCFLAGS_EXTRA": ingress.TRACE_DEFINE_FLAG}
        )
        for invalid in (
            {"CCFLAGS_EXTRA": "LANL_MAA_UMT_INGRESS_TRACE_TEST"},
            {"CCFLAGS_EXTRA": "-DLANL_MAA_UMT_INGRESS_TRACE_TEST=1"},
            {},
        ):
            with self.assertRaisesRegex(RuntimeError, "environment"):
                ingress.validate_fixed_build_environment(invalid)

    def test_build_system_sources_bind_ccflags_declaration_and_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = pathlib.Path(temporary)
            sconstruct = source / "SConstruct"
            defaults = source / "site_scons/gem5_scons/defaults.py"
            defaults.parent.mkdir(parents=True)

            def write(sconstruct_text, defaults_text):
                sconstruct.write_text(sconstruct_text, encoding="utf-8")
                defaults.write_text(defaults_text, encoding="utf-8")
                return {
                    "SConstruct": ingress.sha256(sconstruct),
                    "site_scons/gem5_scons/defaults.py": ingress.sha256(
                        defaults
                    ),
                }

            valid_sconstruct = "env.Append(CCFLAGS='$CCFLAGS_EXTRA')\n"
            valid_defaults = (
                "def EnvDefaults(env):\n"
                "    use_vars = {'CCFLAGS_EXTRA'}\n"
                "    var_overrides = {'CCFLAGS_EXTRA': ''}\n"
                "    for key, default in var_overrides.items():\n"
                "        env[key] = env['ENV'].get(key, default)\n"
            )

            def validate(sconstruct_text, defaults_text, pattern=None):
                hashes = write(sconstruct_text, defaults_text)
                contexts = (
                    mock.patch.object(wrapper, "BUILD_SYSTEM_SHA256", hashes),
                    mock.patch.object(ingress, "BUILD_SYSTEM_SOURCES", hashes),
                )
                with contexts[0], contexts[1]:
                    if pattern is None:
                        self.assertEqual(
                            wrapper.validate_build_system_contract(source),
                            hashes,
                        )
                        self.assertEqual(
                            ingress.validate_build_system_contract(source),
                            hashes,
                        )
                    else:
                        for validator in (
                            wrapper.validate_build_system_contract,
                            ingress.validate_build_system_contract,
                        ):
                            with self.assertRaisesRegex(RuntimeError, pattern):
                                validator(source)

            validate(valid_sconstruct, valid_defaults)
            validate("env.Append(CCFLAGS='')\n", valid_defaults, "SConstruct")
            validate(
                valid_sconstruct,
                valid_defaults.replace("{'CCFLAGS_EXTRA'}", "set()"),
                "declarations|declared/default",
            )
            validate(
                valid_sconstruct,
                valid_defaults.replace("'CCFLAGS_EXTRA': ''", "'OTHER': ''"),
                "declared/default",
            )
            validate(
                valid_sconstruct,
                valid_defaults.replace(
                    "env[key] = env['ENV'].get(key, default)",
                    "env[key] = default",
                ),
                "ENV.get assignment flow",
            )

    def test_targeted_preservation_and_invalidation_is_inode_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = pathlib.Path(temporary) / "source"
            evidence = pathlib.Path(temporary) / "ingress-build-evidence-v13"
            evidence.mkdir()
            values = {}
            for relative in wrapper.INVALIDATED_RELATIVES:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((relative + " rejected").encode())
                values[relative] = wrapper.sha256(path)
            with mock.patch.object(wrapper, "REJECTED_SHA256", values):
                records = wrapper.preserve_and_invalidate(source, evidence)
            self.assertEqual(set(records), set(wrapper.INVALIDATED_RELATIVES))
            for relative, record in records.items():
                self.assertFalse((source / relative).exists())
                preserved = pathlib.Path(record["preserved"]["path"])
                self.assertTrue(preserved.is_file())
                self.assertEqual(
                    (record["original_device"], record["original_inode"]),
                    (record["preserved_device"], record["preserved_inode"]),
                )
                self.assertEqual(wrapper.sha256(preserved), values[relative])

    def test_failed_clean_and_target_not_removed_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = pathlib.Path(temporary) / "source"
            evidence = pathlib.Path(temporary) / "ingress-build-evidence-v13"
            evidence.mkdir()
            target = source / wrapper.TARGET_RELATIVE
            target.parent.mkdir(parents=True)
            target.write_bytes(b"rejected")
            values = {wrapper.TARGET_RELATIVE: wrapper.sha256(target)}
            with mock.patch.object(wrapper, "REJECTED_SHA256", values):
                with self.assertRaisesRegex(
                    RuntimeError, "absent|clean input"
                ):
                    wrapper.preserve_and_invalidate(source, evidence)

    def test_failed_phases_restore_both_exact_rejected_identities(self):
        for phase, create_new in (
            ("object_prebuild", False),
            ("full_build", True),
            ("full_build_validation", True),
            ("observer_gate", True),
        ):
            with tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary)
                source = root / "source"
                evidence = root / "evidence"
                evidence.mkdir()
                invalidated = {}
                for relative in wrapper.INVALIDATED_RELATIVES:
                    preserved = evidence / wrapper.PRESERVED_NAMES[relative]
                    preserved.write_bytes(("old:" + relative).encode())
                    stat_value = preserved.stat()
                    invalidated[relative] = {
                        "preserved": {
                            "path": str(preserved),
                            "sha256": wrapper.sha256(preserved),
                        },
                        "preserved_device": stat_value.st_dev,
                        "preserved_inode": stat_value.st_ino,
                    }
                if create_new:
                    for relative in wrapper.INVALIDATED_RELATIVES:
                        current = source / relative
                        current.parent.mkdir(parents=True, exist_ok=True)
                        current.write_bytes(("new:" + relative).encode())
                for name in (
                    "object-prebuild.stdout",
                    "object-prebuild.stderr",
                    "build.stdout",
                    "build.stderr",
                ):
                    (evidence / name).write_text(name, encoding="utf-8")
                receipt = wrapper.restore_canonical_paths(
                    source,
                    evidence,
                    invalidated,
                    phase,
                    RuntimeError("failed"),
                )
                self.assertEqual(receipt["phase"], phase)
                self.assertEqual(len(receipt["phase_outputs"]), 4)
                self.assertEqual(
                    receipt["status"], "failed_phase_restored_exact_identities"
                )
                for relative in wrapper.INVALIDATED_RELATIVES:
                    canonical = source / relative
                    self.assertTrue(canonical.is_file())
                    preserved = pathlib.Path(
                        invalidated[relative]["preserved"]["path"]
                    )
                    self.assertEqual(
                        canonical.stat().st_ino, preserved.stat().st_ino
                    )
                    self.assertEqual(
                        wrapper.sha256(canonical), wrapper.sha256(preserved)
                    )
                    self.assertTrue(
                        receipt["restored"][relative][
                            "identity_equal_preserved"
                        ]
                    )
                    self.assertEqual(
                        receipt["restored"][relative]["action"],
                        (
                            "replaced_different_with_preserved"
                            if create_new
                            else "restored_missing_from_preserved"
                        ),
                    )

        with tempfile.TemporaryDirectory() as temporary:
            source = pathlib.Path(temporary) / "source"
            evidence = pathlib.Path(temporary) / "ingress-build-evidence-v13"
            evidence.mkdir()
            values = {}
            for relative in wrapper.INVALIDATED_RELATIVES:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode())
                values[relative] = wrapper.sha256(path)
            with mock.patch.object(
                wrapper, "REJECTED_SHA256", values
            ), mock.patch.object(
                pathlib.Path, "unlink", autospec=True, return_value=None
            ):
                with self.assertRaisesRegex(RuntimeError, "did not remove"):
                    wrapper.preserve_and_invalidate(source, evidence)

    def test_full_build_requires_link_but_not_a_second_compile(self):
        with self.assertRaisesRegex(RuntimeError, "gem5 link"):
            wrapper.validate_link_transcript(
                b"scons: `build/X86_UMT_T32_W2/gem5.opt' is up to date.\n"
            )
        with self.assertRaisesRegex(RuntimeError, "gem5 link"):
            wrapper.validate_link_transcript(
                b"[ CXX ] X86_UMT_T32_W2/mem/LANLMAA/lanl_maa.o\n"
            )
        wrapper.validate_link_transcript(b"[ LINK ] X86_UMT_T32_W2/gem5.opt\n")

    def test_object_prebuild_transcript_requires_exact_environment_define(
        self,
    ):
        prefix = (
            "g++ -o build/X86_UMT_T32_W2/mem/LANLMAA/lanl_maa.o "
            "-c src/mem/LANLMAA/lanl_maa.cc "
        )
        valid = (prefix + "-DLANL_MAA_UMT_INGRESS_TRACE_TEST\n").encode()
        for validator in (
            wrapper.validate_object_compile_transcript,
            ingress.validate_object_compile_transcript,
        ):
            validator(valid)
            for suffix in (
                "",
                "-DLANL_MAA_UMT_INGRESS_TRACE_TEST=1",
                "CPPDEFINES=LANL_MAA_UMT_INGRESS_TRACE_TEST",
                "CCFLAGS_EXTRA=-DLANL_MAA_UMT_INGRESS_TRACE_TEST",
            ):
                with self.assertRaisesRegex(RuntimeError, "define"):
                    validator((prefix + suffix + "\n").encode())

    def test_each_phase_requires_its_invalidated_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = pathlib.Path(temporary)
            records = {
                relative: {
                    "preserved_device": 1,
                    "preserved_inode": index + 1,
                    "preserved": {"sha256": str(index) * 64},
                }
                for index, relative in enumerate(wrapper.INVALIDATED_RELATIVES)
            }
            for relative in wrapper.INVALIDATED_RELATIVES:
                with self.assertRaisesRegex(RuntimeError, "recreate"):
                    wrapper.validate_rebuilt_path(source, relative, records)
            target = source / wrapper.TARGET_RELATIVE
            target.parent.mkdir(parents=True)
            target.write_bytes(b"new target")
            self.assertEqual(
                wrapper.validate_rebuilt_path(
                    source, wrapper.TARGET_RELATIVE, records
                )["path"],
                str(target),
            )

    def test_missing_compiled_literal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = pathlib.Path(temporary) / "gem5.opt"
            target.write_bytes(b"UMT_INGRESS kind= but no hold marker")
            with self.assertRaisesRegex(RuntimeError, "compiled ingress"):
                wrapper.validate_compiled_literals(target)
            target.write_bytes(b"UMT_INGRESS kind=\0d64_hold cycle=")
            wrapper.validate_compiled_literals(target)

    def test_no_clobber_evidence_writer_rejects_existing_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "attestation.json"
            path.write_text("preserve", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                wrapper.no_clobber_json(path, {"forged": True})
            self.assertEqual(path.read_text(encoding="utf-8"), "preserve")

    def test_build_unit_is_retained_for_terminal_snapshot(self):
        evidence = pathlib.Path(
            "/campaign/identity/ingress-build-evidence-v13"
        )
        command = ingress.build_systemd_run_command(evidence)
        self.assertIn("--remain-after-exit", command)
        self.assertNotIn("--collect", command)
        self.assertIn("--property=CPUQuota=400%", command)
        self.assertIn("--property=MemoryHigh=" + str(14 * 1024**3), command)
        self.assertIn("--property=MemoryMax=" + str(16 * 1024**3), command)
        self.assertIn("--property=MemorySwapMax=0", command)
        self.assertIn("--property=RuntimeMaxSec=4h", command)
        self.assertNotIn("--property=RuntimeMaxUSec=4h", command)

        pid, invocation, ticks = 77, "a" * 32, "1234"
        fields = {
            "Id": ingress.BUILD_UNIT,
            "InvocationID": invocation,
            "MainPID": "0",
            "ExecMainPID": str(pid),
            "ExecMainStartTimestampMonotonic": "9999",
            "WorkingDirectory": str(ingress.CANONICAL_SOURCE),
            **ingress.RESOURCE_POLICY,
            "ExecStart": (
                "{ path=/usr/bin/python3 ; argv[]="
                + " ".join(ingress.wrapper_command(evidence))
                + " ; ignore_errors=no ; start_time=[n/a] ; }"
            ),
            "Environment": "",
            "ExecMainCode": "1",
            "ExecMainStatus": "0",
            "Result": "success",
        }
        ingress.validate_show_snapshot(
            fields,
            phase="terminal",
            pid=pid,
            proc_start_ticks=ticks,
            invocation=invocation,
            command=ingress.wrapper_command(evidence),
        )
        fields["ExecMainStatus"] = "1"
        with self.assertRaisesRegex(RuntimeError, "terminal"):
            ingress.validate_show_snapshot(
                fields,
                phase="terminal",
                pid=pid,
                proc_start_ticks=ticks,
                invocation=invocation,
                command=ingress.wrapper_command(evidence),
            )

    def test_launch_and_show_runtime_property_names_are_distinct(self):
        launch = (
            ("CPUQuota", "400%"),
            ("CPUWeight", "1000"),
            ("MemoryHigh", str(14 * 1024**3)),
            ("MemoryMax", str(16 * 1024**3)),
            ("MemorySwapMax", "0"),
            ("RuntimeMaxSec", "4h"),
        )
        show = {
            "CPUQuotaPerSecUSec": "4s",
            "CPUWeight": "1000",
            "MemoryHigh": str(14 * 1024**3),
            "MemoryMax": str(16 * 1024**3),
            "MemorySwapMax": "0",
            "RuntimeMaxUSec": "4h",
        }
        self.assertEqual(ingress.DISPATCH_PROPERTIES, launch)
        self.assertEqual(ingress.BUILD_DISPATCH_PROPERTIES, launch)
        self.assertEqual(ingress.RESOURCE_POLICY, show)
        self.assertIn("RuntimeMaxUSec", ingress.SYSTEMD_SHOW_PROPERTIES)
        self.assertNotIn("RuntimeMaxSec", ingress.SYSTEMD_SHOW_PROPERTIES)

        build = ingress.build_systemd_run_command(
            "/campaign/identity/ingress-build-evidence-v13"
        )
        arm = ingress.systemd_run_command("arm.service", ["/bin/true"])
        for command in (build, arm):
            properties = [
                value.removeprefix("--property=")
                for value in command
                if value.startswith("--property=")
            ]
            self.assertEqual(
                properties,
                [f"{key}={value}" for key, value in launch],
            )
            self.assertNotIn("RuntimeMaxUSec=4h", properties)

        rejected = launch[:-1] + (("RuntimeMaxUSec", "4h"),)
        with mock.patch.object(
            ingress, "BUILD_DISPATCH_PROPERTIES", rejected
        ), self.assertRaisesRegex(RuntimeError, "resource mapping"):
            ingress.build_systemd_run_command(
                "/campaign/identity/ingress-build-evidence-v13"
            )
        with mock.patch.object(
            ingress, "DISPATCH_PROPERTIES", rejected
        ), self.assertRaisesRegex(RuntimeError, "resource mapping"):
            ingress.systemd_run_command("arm.service", ["/bin/true"])

    def test_dry_build_plan_is_fresh_exact_and_no_clobber(self):
        with tempfile.TemporaryDirectory() as temporary:
            campaign = pathlib.Path(temporary) / "campaign"
            output = campaign / "build-plan-v13.json"
            plan = ingress.dry_build_plan(campaign, output)
            self.assertEqual(plan["schema"], ingress.BUILD_PLAN_SCHEMA)
            self.assertEqual(plan["status"], "dry_only_not_dispatched")
            self.assertEqual(plan["build_argv"], list(ingress.BUILD_ARGV))
            self.assertEqual(
                plan["object_prebuild_argv"],
                list(ingress.OBJECT_PREBUILD_ARGV),
            )
            self.assertEqual(
                plan["fixed_child_environment"],
                {"CCFLAGS_EXTRA": ingress.TRACE_DEFINE_FLAG},
            )
            self.assertIn("--remain-after-exit", plan["launch_command"])
            self.assertNotIn("--collect", plan["launch_command"])
            self.assertEqual(
                plan["cleanup_receipt"]["schema"],
                ingress.BUILD_CLEANUP_RECEIPT_SCHEMA,
            )
            self.assertTrue(
                plan["cleanup_receipt"]["must_follow_terminal_proof"]
            )
            self.assertEqual(
                plan["rejected_sha256"][str(ingress.CANONICAL_GEM5)],
                ingress.REJECTED_TARGET_SHA256,
            )
            before = output.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "fresh canonical"):
                ingress.dry_build_plan(campaign, output)
            self.assertEqual(output.read_bytes(), before)

    def test_post_terminal_cleanup_receipt_is_bound_and_no_clobber(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            proof = root / "proof.json"
            show = root / "cleanup.show"
            output = root / "cleanup-receipt.json"
            proof.write_text('{"status":"passed"}\n', encoding="utf-8")
            show.write_text(
                "LoadState=not-found\nActiveState=inactive\nSubState=dead\n",
                encoding="utf-8",
            )
            receipt = ingress.record_build_cleanup_receipt(
                output,
                proof,
                ingress.sha256(proof),
                show,
                ingress.sha256(show),
            )
            self.assertEqual(
                receipt["schema"], ingress.BUILD_CLEANUP_RECEIPT_SCHEMA
            )
            self.assertEqual(
                receipt["cleanup_commands"],
                [list(command) for command in ingress.BUILD_CLEANUP_COMMANDS],
            )
            self.assertEqual(
                receipt["observed_state"],
                {
                    "LoadState": "not-found",
                    "ActiveState": "inactive",
                    "SubState": "dead",
                },
            )
            before = output.read_bytes()
            with self.assertRaises(FileExistsError):
                ingress.record_build_cleanup_receipt(
                    output,
                    proof,
                    ingress.sha256(proof),
                    show,
                    ingress.sha256(show),
                )
            self.assertEqual(output.read_bytes(), before)

            failure_anchor = root / "failure-restore.json"
            failure_anchor.write_text(
                '{"status":"failed_phase_restored_exact_identities"}\n',
                encoding="utf-8",
            )
            failure_receipt = ingress.record_build_cleanup_receipt(
                root / "failure-cleanup-receipt.json",
                failure_anchor,
                ingress.sha256(failure_anchor),
                show,
                ingress.sha256(show),
                anchor_kind="failure_restore",
            )
            self.assertEqual(
                failure_receipt["status"],
                "cleanup_observed_after_failure_restore",
            )
            self.assertEqual(
                failure_receipt["cleanup_show"]["sha256"],
                ingress.sha256(show),
            )

            bad = root / "bad-cleanup.show"
            bad.write_text(
                "LoadState=loaded\nActiveState=inactive\nSubState=dead\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "not-found"):
                ingress.record_build_cleanup_receipt(
                    root / "bad-receipt.json",
                    proof,
                    ingress.sha256(proof),
                    bad,
                    ingress.sha256(bad),
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
                "CCFLAGS_EXTRA": "-DLANL_MAA_UMT_INGRESS_TRACE_TEST",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "LC_ALL": "C",
                "LANG": "C",
                "TZ": "UTC",
            },
        )
        wrapper.validate_safe_child_environment(wrapper.SAFE_CHILD_ENV)
        forged_environment = dict(wrapper.SAFE_CHILD_ENV)
        forged_environment["CCFLAGS_EXTRA"] += "=1"
        with self.assertRaisesRegex(RuntimeError, "environment"):
            wrapper.validate_safe_child_environment(forged_environment)
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

    def test_source_bank_pressure_is_trace_derived_for_four_banks(self):
        rows, digest = [], 1

        def emit(callback_id, kind, groups):
            nonlocal digest
            for lane_id, group in enumerate(groups):
                rows.append(
                    callback(
                        kind,
                        callback_id,
                        lane_id,
                        len(groups),
                        digest,
                        digest + 1,
                        group=group,
                    )
                )
                digest += 1

        emit(1, "source", [0, 1, 2, 3])
        emit(2, "source", [0, 4])
        emit(3, "denominator", list(range(8)))
        rows.append(line("d32", "release", 110, 8))
        report = ingress.validate_trace(
            ingress.parse_debug_file_text("\n".join(rows)), "d32-g16"
        )["source_bank_pressure"]
        self.assertEqual(
            report,
            {
                "bank_count": 4,
                "bank_mapping": "bank=group%4",
                "source_callbacks": 2,
                "max_source_writes_per_callback": 4,
                "max_same_bank_multiplicity": 2,
                "callbacks_with_duplicate_banks": 1,
                "four_distinct_bank_accepted_callbacks": 1,
                "claim_boundary": (
                    "Trace-derived pressure for the current four-bank stream "
                    "state; not RTL timing or physical equivalence."
                ),
            },
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

    def test_atomic_publication_rejects_concurrent_precreation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            target = root / "identity/contract-or-dispatch.json"
            real_link = ingress.os.link

            def concurrent_winner(source, destination, **kwargs):
                destination = pathlib.Path(destination)
                descriptor = ingress.os.open(
                    destination,
                    ingress.os.O_WRONLY
                    | ingress.os.O_CREAT
                    | ingress.os.O_EXCL,
                    0o600,
                )
                with ingress.os.fdopen(descriptor, "wb") as stream:
                    stream.write(b"concurrent winner\n")
                return real_link(source, destination, **kwargs)

            with mock.patch.object(
                ingress.os, "link", side_effect=concurrent_winner
            ):
                with self.assertRaises(FileExistsError):
                    ingress.atomic_no_clobber(target, {"ours": True})
            self.assertEqual(target.read_bytes(), b"concurrent winner\n")
            self.assertEqual(
                list(target.parent.glob(f".{target.name}.*.tmp")), []
            )
            target.unlink()
            ingress.atomic_no_clobber(target, {"ours": True})
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"ours": True},
            )
            with self.assertRaises(FileExistsError):
                ingress.atomic_no_clobber(target, {"overwrite": True})
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"ours": True},
            )

    def test_harness_identity_requires_clean_commit_tree_and_file_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            reviewed = root / "reviewed.py"
            reviewed.write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "add", "reviewed.py"], cwd=root, check=True)
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
                cwd=root,
                check=True,
            )
            identity = ingress.verify_harness_identity(
                root=root, reviewed_files=("reviewed.py",)
            )
            self.assertEqual(
                identity["reviewed_file_sha256"]["reviewed.py"],
                ingress.sha256(reviewed),
            )
            forged = json.loads(json.dumps(identity))
            forged["source_tree"] = "f" * 40
            with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
                ingress.verify_harness_identity(
                    forged, root=root, reviewed_files=("reviewed.py",)
                )
            forged = json.loads(json.dumps(identity))
            forged["reviewed_file_sha256"]["reviewed.py"] = "f" * 64
            with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
                ingress.verify_harness_identity(
                    forged, root=root, reviewed_files=("reviewed.py",)
                )
            reviewed.write_text("VALUE = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "not clean"):
                ingress.verify_harness_identity(
                    identity, root=root, reviewed_files=("reviewed.py",)
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
            sconstruct = source / "SConstruct"
            defaults = source / "site_scons/gem5_scons/defaults.py"
            defaults.parent.mkdir(parents=True)
            sconstruct.write_text(
                "env.Append(CCFLAGS='$CCFLAGS_EXTRA')\n", encoding="utf-8"
            )
            defaults.write_text(
                "def EnvDefaults(env):\n"
                "    use_vars = {'CCFLAGS_EXTRA'}\n"
                "    var_overrides = {'CCFLAGS_EXTRA': ''}\n"
                "    for key, default in var_overrides.items():\n"
                "        env[key] = env['ENV'].get(key, default)\n",
                encoding="utf-8",
            )
            build_system_hashes = {
                "SConstruct": ingress.sha256(sconstruct),
                "site_scons/gem5_scons/defaults.py": ingress.sha256(defaults),
            }
            build = source / "build/X86_UMT_T32_W2"
            build.mkdir(parents=True)
            gem5, object_file, config_hh, config_cc = (
                build / "gem5.opt",
                build / "mem/LANLMAA/lanl_maa.o",
                build / "config.hh",
                build / "config.cc",
            )
            object_file.parent.mkdir(parents=True)
            for item in (object_file, config_hh, config_cc):
                item.write_text(item.name, encoding="utf-8")
            gem5.write_bytes(b"gem5 UMT_INGRESS kind= d64_hold cycle=")
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
            evidence = root / ingress.BUILD_EVIDENCE_NAME
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
                "lanl_maa_o": {
                    "path": str(object_file),
                    "sha256": ingress.sha256(object_file),
                },
                "config_hh": {
                    "path": str(config_hh),
                    "sha256": ingress.sha256(config_hh),
                },
                "config_cc": {
                    "path": str(config_cc),
                    "sha256": ingress.sha256(config_cc),
                },
            }
            rejected_gem5 = evidence / "rejected-gem5.opt"
            rejected_object = evidence / "rejected-lanl_maa.o"
            rejected_gem5.write_bytes(b"rejected gem5")
            rejected_object.write_bytes(b"rejected object")
            rejected_hashes = {
                "gem5": ingress.sha256(rejected_gem5),
                "object": ingress.sha256(rejected_object),
            }
            invalidated = {
                "build/X86_UMT_T32_W2/gem5.opt": {
                    "canonical_path": str(gem5),
                    "preserved": {
                        "path": str(rejected_gem5),
                        "sha256": rejected_hashes["gem5"],
                    },
                    "original_device": rejected_gem5.stat().st_dev,
                    "original_inode": rejected_gem5.stat().st_ino,
                    "preserved_device": rejected_gem5.stat().st_dev,
                    "preserved_inode": rejected_gem5.stat().st_ino,
                    "canonical_absent_after_clean": True,
                },
                "build/X86_UMT_T32_W2/mem/LANLMAA/lanl_maa.o": {
                    "canonical_path": str(object_file),
                    "preserved": {
                        "path": str(rejected_object),
                        "sha256": rejected_hashes["object"],
                    },
                    "original_device": rejected_object.stat().st_dev,
                    "original_inode": rejected_object.stat().st_ino,
                    "preserved_device": rejected_object.stat().st_dev,
                    "preserved_inode": rejected_object.stat().st_ino,
                    "canonical_absent_after_clean": True,
                },
            }
            attestation_value = {
                "schema": "lanl-maa-umt-ingress-build-attestation-v13",
                "unit": ingress.BUILD_UNIT,
                "invocation_id": invocation_id,
                "wrapper_pid": pid,
                "wrapper_proc_start_ticks": start_ticks,
                "status": "passed",
                "source_commit": commit,
                "source_tree": tree,
                "source_clean_before": True,
                "source_clean_after": True,
                "source_identity_unchanged": True,
                "clean_method": ingress.BUILD_CLEAN_METHOD,
                "invalidated_artifacts": invalidated,
                "target_paths_absent_after_clean": True,
                "object_prebuild_argv": list(ingress.OBJECT_PREBUILD_ARGV),
                "object_prebuild_returncode": 0,
                "object_prebuild_define_verified": True,
                "object_prebuild_artifact": {
                    "path": str(object_file),
                    "sha256": ingress.sha256(object_file),
                    "device": object_file.stat().st_dev,
                    "inode": object_file.stat().st_ino,
                },
                "object_identity_unchanged_after_link": True,
                "build_argv": list(ingress.BUILD_ARGV),
                "build_environment": ingress.BUILD_ENVIRONMENT,
                "build_returncode": 0,
                "required_link_observed": True,
                "instrumentation_source_sha256": source_hashes,
                "build_system_source_sha256": build_system_hashes,
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
                "clean_stdout": evidence_file(
                    "clean.stdout",
                    "clean_method="
                    + ingress.BUILD_CLEAN_METHOD
                    + "\ninvalidated=build/X86_UMT_T32_W2/gem5.opt,"
                    "build/X86_UMT_T32_W2/mem/LANLMAA/lanl_maa.o\n"
                    "status=0/SUCCESS\n",
                ),
                "clean_stderr": evidence_file("clean.stderr", ""),
                "object_prebuild_stdout": evidence_file(
                    "object-prebuild.stdout",
                    "g++ -o build/X86_UMT_T32_W2/mem/LANLMAA/lanl_maa.o "
                    "-c src/mem/LANLMAA/lanl_maa.cc "
                    "-DLANL_MAA_UMT_INGRESS_TRACE_TEST\n",
                ),
                "object_prebuild_stderr": evidence_file(
                    "object-prebuild.stderr", ""
                ),
                "build_stdout": evidence_file(
                    "build.stdout",
                    "[ LINK ] X86_UMT_T32_W2/gem5.opt\n",
                ),
                "build_stderr": evidence_file("build.stderr", ""),
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
                "build_system_manifest": evidence_file(
                    "build-system-source-sha256.json",
                    json.dumps(build_system_hashes),
                ),
                "target_config_literal_scan": evidence_file(
                    "target-config-literal-scan.json",
                    json.dumps(
                        {
                            "target": str(gem5),
                            "target_sha256": ingress.sha256(gem5),
                            "object": str(object_file),
                            "object_sha256": ingress.sha256(object_file),
                            "config_hh": str(config_hh),
                            "config_hh_sha256": ingress.sha256(config_hh),
                            "config_cc": str(config_cc),
                            "config_cc_sha256": ingress.sha256(config_cc),
                            "compiled_binary_markers": [
                                "UMT_INGRESS kind=",
                                "d64_hold cycle=",
                            ],
                        }
                    ),
                ),
                "rejected_gem5": {
                    "path": str(rejected_gem5),
                    "sha256": ingress.sha256(rejected_gem5),
                },
                "rejected_lanl_maa_o": {
                    "path": str(rejected_object),
                    "sha256": ingress.sha256(rejected_object),
                },
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
                "producer": "systemd-build-proof-v13-service-wrapper",
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
                "build_system_source_sha256": build_system_hashes,
                "clean_method": ingress.BUILD_CLEAN_METHOD,
                "invalidated_artifacts": invalidated,
                "target_paths_absent_after_clean": True,
                "clean_stdout": evidence_items["clean_stdout"],
                "clean_stderr": evidence_items["clean_stderr"],
                "object_prebuild_argv": list(ingress.OBJECT_PREBUILD_ARGV),
                "object_prebuild_returncode": 0,
                "object_prebuild_define_verified": True,
                "object_prebuild_artifact": {
                    "path": str(object_file),
                    "sha256": ingress.sha256(object_file),
                    "device": object_file.stat().st_dev,
                    "inode": object_file.stat().st_ino,
                },
                "object_identity_unchanged_after_link": True,
                "object_prebuild_stdout": evidence_items[
                    "object_prebuild_stdout"
                ],
                "object_prebuild_stderr": evidence_items[
                    "object_prebuild_stderr"
                ],
                "build_returncode": 0,
                "required_link_observed": True,
                "build_stdout": evidence_items["build_stdout"],
                "build_stderr": evidence_items["build_stderr"],
                "build_artifacts": artifacts,
                "build_invocation": {
                    "unit": ingress.BUILD_UNIT,
                    "launch_command": ingress.build_systemd_run_command(
                        evidence
                    ),
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
                    "cleanup_commands_after_terminal_capture": [
                        list(item) for item in ingress.BUILD_CLEANUP_COMMANDS
                    ],
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
                "BUILD_OBJECT": object_file,
                "INSTRUMENTATION_SOURCES": source_hashes,
                "BUILD_SYSTEM_SOURCES": build_system_hashes,
                "REJECTED_TARGET_SHA256": rejected_hashes["gem5"],
                "REJECTED_OBJECT_SHA256": rejected_hashes["object"],
            }
            with mock.patch.multiple(ingress, **patches):
                proof["build_invocation"]["wrapper_command"] = list(
                    ingress.wrapper_command(evidence)
                )
                proof["build_invocation"][
                    "launch_command"
                ] = ingress.build_systemd_run_command(evidence)
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

                predecessor = json.loads(json.dumps(proof))
                predecessor[
                    "schema"
                ] = "lanl-maa-umt-ingress-instrumented-gem5-build-proof-v12"
                with self.assertRaisesRegex(RuntimeError, "schema/status"):
                    attempt(predecessor)

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
                with self.assertRaisesRegex(RuntimeError, "report"):
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
                    ("required_link_observed", False),
                    ("build_returncode", 1),
                ):
                    forged = dict(proof)
                    forged[field] = value
                    with self.assertRaisesRegex(
                        RuntimeError, "command/clean/source"
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
                            '"build_argv": [',
                            '"build_argv": ['
                            '"CPPDEFINES=LANL_MAA_UMT_INGRESS_TRACE_TEST", ',
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
                with self.assertRaisesRegex(RuntimeError, "canonical source"):
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

    def test_v12_combined_contract_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            campaign = pathlib.Path(temporary) / "campaign"
            campaign.mkdir()
            contract = campaign / ingress.CONTRACT_FILENAME
            contract.write_text(
                json.dumps(
                    {
                        "schema": "lanl-maa-umt-ingress-contract-v12",
                        "status": "frozen_before_dispatch",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RuntimeError, "v13 contract semantics"
            ):
                ingress.dispatch_plan(
                    contract,
                    ingress.sha256(contract),
                    campaign,
                    campaign / "identity" / ingress.DISPATCH_FILENAME,
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
                "--property=RuntimeMaxSec=4h",
            ],
        )
        self.assertNotIn("--property=CPUQuotaPerSecUSec=4s", command)

    def test_v13_dispatch_runs_the_pinned_wrapper_not_gem5_directly(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(
                set(ingress.CASES),
                {"d32-g16", "d32-g31", "d32-g32", "d64-g32"},
            )
            for case in ingress.CASES:
                root = pathlib.Path(temporary) / "arms" / case
                gem5_argv = ingress.case_command(
                    ingress.CANONICAL_GEM5, root, case
                )
                wrapper_argv = ingress.arm_wrapper_argv(root, gem5_argv)
                arm = self.arm_spec(root, case)
                plan = ingress.systemd_arm_plan(arm)
                command = plan["systemd_run_argv"]
                self.assertEqual(command[10:], wrapper_argv)
                self.assertEqual(command[10], "/usr/bin/python3")
                self.assertEqual(
                    pathlib.Path(command[11]).resolve(),
                    ingress.ARM_WRAPPER.resolve(),
                )
                self.assertEqual(
                    ingress.sha256(ingress.ARM_WRAPPER),
                    ingress.ARM_WRAPPER_SHA256,
                )
                self.assertEqual(command[command.index("--") + 1 :], gem5_argv)
                self.assertIn("--dot-config=", gem5_argv)
                self.assertNotEqual(command[10], str(ingress.CANONICAL_GEM5))
                self.assertEqual(plan["wrapper"], arm["wrapper"])
                self.assertEqual(
                    plan["wrapper_argv_sha256"],
                    ingress.json_sha256(wrapper_argv),
                )
                self.assertEqual(
                    plan["systemd_run_argv_sha256"],
                    ingress.json_sha256(command),
                )

    def arm_spec(self, root, case="d32-g16"):
        gem5_argv = ingress.case_command(ingress.CANONICAL_GEM5, root, case)
        wrapper_argv = ingress.arm_wrapper_argv(root, gem5_argv)
        return {
            "root": str(root),
            "unit": f"umt-ingress-micro-v13-{case}-20260830.service",
            "gem5_argv": gem5_argv,
            "gem5_argv_sha256": ingress.json_sha256(gem5_argv),
            "wrapper": {
                "path": str(ingress.ARM_WRAPPER.resolve()),
                "sha256": ingress.ARM_WRAPPER_SHA256,
            },
            "wrapper_argv": wrapper_argv,
            "wrapper_argv_sha256": ingress.json_sha256(wrapper_argv),
            "binary_sha256": ingress.ADAPTIVE_NATIVE_SHA256,
        }

    def test_arm_wrapper_owns_streams_receipts_and_refuses_clobber(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "arms/d32-g16"
            arm = self.arm_spec(root)

            def fake_run(argv, **kwargs):
                self.assertEqual(argv, arm["gem5_argv"])
                self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
                evidence = root / arm_wrapper.EVIDENCE_DIRECTORY
                self.assertEqual(
                    evidence.stat().st_mode & 0o222,
                    0,
                )
                for name in arm_wrapper.RECEIPT_NAMES:
                    receipt = evidence / name
                    self.assertTrue(receipt.is_file())
                    with self.assertRaises(FileExistsError):
                        arm_wrapper.reserve_file(receipt)
                self.assertEqual(
                    (evidence / "arm-terminal.json").read_bytes(), b""
                )
                with self.assertRaises(PermissionError):
                    (evidence / "arm-terminal.json").unlink()
                for relative in (
                    "app.stdout",
                    "app.stderr",
                    "debug.log",
                    "submission.json",
                    f"{ingress.LABEL_PREFIX}_d32-g16.csv",
                    "m5out/stats.txt",
                    "m5out/config.ini",
                    "m5out/config.json",
                ):
                    with self.assertRaises(FileExistsError):
                        arm_wrapper.reserve_file(root / relative)
                kwargs["stdout"].write(b"captured stdout\n")
                kwargs["stderr"].write(b"captured stderr\n")
                return subprocess.CompletedProcess(argv, 0)

            with mock.patch.object(
                arm_wrapper.subprocess, "run", side_effect=fake_run
            ) as run:
                wrong_root = pathlib.Path(temporary) / "arms/wrong"
                with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
                    arm_wrapper.run_arm(wrong_root, "0" * 64, arm["gem5_argv"])
                self.assertFalse(wrong_root.exists())
                self.assertEqual(
                    arm_wrapper.run_arm(
                        root,
                        arm["gem5_argv_sha256"],
                        arm["gem5_argv"],
                    ),
                    0,
                )
                self.assertEqual(run.call_count, 1)
                with self.assertRaises(FileExistsError):
                    arm_wrapper.run_arm(
                        root,
                        arm["gem5_argv_sha256"],
                        arm["gem5_argv"],
                    )
                self.assertEqual(run.call_count, 1)

            existing = pathlib.Path(temporary) / "already-present.stdout"
            existing.write_bytes(b"preserve")
            with self.assertRaises(FileExistsError):
                arm_wrapper.reserve_file(existing)
            self.assertEqual(existing.read_bytes(), b"preserve")

            execution = ingress.validate_arm_execution_evidence(
                root, "d32-g16", arm
            )
            self.assertEqual(
                execution["wrapper_sha256"], ingress.ARM_WRAPPER_SHA256
            )
            self.assertEqual(
                (root / "gem5.stdout").read_bytes(), b"captured stdout\n"
            )
            self.assertEqual(
                (root / "gem5.stderr").read_bytes(), b"captured stderr\n"
            )
            csv = root / f"{ingress.LABEL_PREFIX}_d32-g16.csv"
            self.assertEqual(csv.read_bytes(), arm_wrapper.CSV_HEADER)
            self.assertEqual(
                ingress.sha256(csv), ingress.ARM_CSV_HEADER_SHA256
            )

    def test_arm_analysis_rejects_forged_or_clobbered_wrapper_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "arms/d32-g16"
            arm = self.arm_spec(root)

            def fake_run(argv, **kwargs):
                kwargs["stdout"].write(b"stdout\n")
                kwargs["stderr"].write(b"stderr\n")
                return subprocess.CompletedProcess(argv, 0)

            with mock.patch.object(
                arm_wrapper.subprocess, "run", side_effect=fake_run
            ):
                arm_wrapper.run_arm(
                    root, arm["gem5_argv_sha256"], arm["gem5_argv"]
                )
            ingress.validate_arm_execution_evidence(root, "d32-g16", arm)

            stdout = root / "gem5.stdout"
            stdout.write_bytes(stdout.read_bytes() + b"clobber")
            with self.assertRaisesRegex(RuntimeError, "stdout SHA-256"):
                ingress.validate_arm_execution_evidence(root, "d32-g16", arm)
            stdout.write_bytes(b"stdout\n")

            evidence = root / ingress.ARM_EVIDENCE_DIRECTORY
            launch_path = evidence / "arm-launch.json"
            launch_original = launch_path.read_bytes()
            launch = json.loads(launch_original)
            launch["wrapper_sha256"] = "f" * 64
            launch_path.chmod(0o600)
            launch_path.write_text(json.dumps(launch), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "launch/ownership"):
                ingress.validate_arm_execution_evidence(root, "d32-g16", arm)
            launch_path.write_bytes(launch_original)
            launch_path.chmod(0o400)

            terminal_path = evidence / "arm-terminal.json"
            terminal_original = terminal_path.read_bytes()
            terminal = json.loads(terminal_original)
            terminal["gem5_returncode"] = 9
            terminal_path.chmod(0o600)
            terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "terminal wrapper"):
                ingress.validate_arm_execution_evidence(root, "d32-g16", arm)
            terminal_path.write_bytes(terminal_original)
            terminal_path.chmod(0o400)

            forged_arm = json.loads(json.dumps(arm))
            forged_arm["wrapper_argv"][-1] = "forged-label"
            forged_arm["wrapper_argv_sha256"] = ingress.json_sha256(
                forged_arm["wrapper_argv"]
            )
            with self.assertRaisesRegex(RuntimeError, "argv contract"):
                ingress.validate_arm_execution_evidence(
                    root, "d32-g16", forged_arm
                )

    def test_arm_wrapper_detects_runner_output_replacement_race(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "arms/d32-g16"
            arm = self.arm_spec(root)

            def replace_after_admission(argv, **kwargs):
                target = root / "submission.json"
                target.unlink()
                target.write_text("racing replacement", encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0)

            with mock.patch.object(
                arm_wrapper.subprocess,
                "run",
                side_effect=replace_after_admission,
            ):
                self.assertEqual(
                    arm_wrapper.run_arm(
                        root,
                        arm["gem5_argv_sha256"],
                        arm["gem5_argv"],
                    ),
                    125,
                )
            terminal = json.loads(
                (
                    root / ingress.ARM_EVIDENCE_DIRECTORY / "arm-terminal.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(terminal["status"], "output_identity_failed")
            self.assertFalse(
                terminal["outputs"]["submission.json"][
                    "reservation_identity_match"
                ]
            )
            with self.assertRaisesRegex(RuntimeError, "ownership|identity"):
                ingress.validate_arm_execution_evidence(root, "d32-g16", arm)

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
