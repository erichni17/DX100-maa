#!/usr/bin/env python3
"""Adversarial dry tests for combined v16 with build-v19 evidence."""
import importlib.util
import json
import pathlib
import shutil
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
    def test_v16_contract_accepts_exactly_the_v19_build_proof_generation(self):
        self.assertEqual(
            ingress.SCHEMA_CONTRACT,
            "lanl-maa-umt-ingress-contract-v16",
        )
        self.assertEqual(
            ingress.SCHEMA_BUILD_PROOF,
            "lanl-maa-umt-pki4-dual-gem5-build-proof-v19",
        )
        self.assertEqual(
            ingress.SCHEMA_DISPATCH_PLAN,
            "lanl-maa-umt-ingress-dispatch-plan-v16",
        )
        self.assertEqual(
            ingress.SCHEMA_ARM_REPORT,
            "lanl-maa-umt-ingress-arm-report-v16",
        )
        self.assertEqual(
            ingress.CONTRACT_FILENAME, "ingress-contract-v16.json"
        )
        self.assertEqual(
            ingress.DISPATCH_FILENAME,
            "ingress-dry-dispatch-v16.json",
        )
        self.assertTrue(
            all(
                value.startswith("umt-ingress-micro-v16-")
                for value in (
                    f"umt-ingress-micro-v16-{case}-20260830.service"
                    for case in ingress.CASES
                )
            )
        )

    def test_v19_build_spelling_and_sanitized_environment_are_exact(self):
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
                    "CCFLAGS_EXTRA": (
                        "-DLANL_MAA_UMT_INGRESS_TRACE_TEST "
                        "-DLANL_MAA_UMT_PKI4_CONFORMANCE_TEST"
                    )
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
            ingress.BUILD_UNIT,
            "umt-pki4-conformance-build-v19-20260831.service",
        )
        self.assertEqual(
            ingress.SCHEMA_BUILD_PROOF,
            "lanl-maa-umt-pki4-dual-gem5-build-proof-v19",
        )

    def test_v19_producer_consumer_schemas_and_protocol_are_identical(self):
        self.assertEqual(wrapper.SCHEMA, ingress.BUILD_ATTESTATION_SCHEMA)
        self.assertEqual(wrapper.PROTOCOL, ingress.JOURNAL_TERMINAL_PROTOCOL)
        self.assertEqual(
            wrapper.OWNERSHIP_SCHEMA,
            ingress.GENERATED_ROOT_OWNERSHIP_SCHEMA,
        )
        self.assertEqual(wrapper.BUILD_UNIT, ingress.BUILD_UNIT)
        self.assertEqual(
            wrapper.TRACE_DEFINE_FLAGS, ingress.TRACE_DEFINE_FLAGS
        )
        marker = ingress.journal_marker(
            "START",
            invocation="a" * 32,
            pid=17,
            proc_start_ticks="123",
        )
        payload = json.loads(marker.split(b" ", 2)[2])
        self.assertEqual(payload["schema"], wrapper.SCHEMA)

    def test_v19_conformance_provenance_and_source_bindings_fail_closed(self):
        provenance = {
            "host_report": {
                "path": str(ingress.CONFORMANCE_REPORT),
                "sha256": ingress.CONFORMANCE_REPORT_SHA256,
            },
            "temporal_plan": {
                "path": str(ingress.TEMPORAL_PLAN),
                "sha256": ingress.TEMPORAL_PLAN_SHA256,
            },
            "independent_review": {
                "path": str(ingress.PROMOTION_REVIEW),
                "sha256": ingress.PROMOTION_REVIEW_SHA256,
            },
        }
        self.assertEqual(
            ingress.validate_conformance_provenance(provenance), provenance
        )
        for field in provenance:
            forged = json.loads(json.dumps(provenance))
            forged[field]["sha256"] = "0" * 64
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                ingress.validate_conformance_provenance(forged)
        forged_sources = dict(ingress.CONFORMANCE_INSTRUMENTATION_SOURCES)
        key = next(iter(forged_sources))
        forged_sources[key] = "0" * 64
        with mock.patch.object(
            ingress,
            "CONFORMANCE_INSTRUMENTATION_SOURCES",
            forged_sources,
        ), self.assertRaisesRegex(RuntimeError, "semantics"):
            ingress.validate_conformance_provenance(provenance)

    def test_v19_clean_stdout_requires_exact_ordered_three_path_tuple(self):
        expected = (
            "clean_method=require-fresh-absent-exact-two-v1\n"
            "initial_absent=build/X86_UMT_T32_W2,"
            "build/X86_UMT_T32_W2/gem5.opt,"
            "build/X86_UMT_T32_W2/mem/LANLMAA/lanl_maa.o\n"
            "status=0/SUCCESS\n"
        )
        self.assertEqual(ingress.expected_clean_stdout(), expected)
        records = {
            "live_shape": expected,
            "missing_root": expected.replace("build/X86_UMT_T32_W2,", "", 1),
            "reordered_paths": expected.replace(
                "build/X86_UMT_T32_W2," "build/X86_UMT_T32_W2/gem5.opt,",
                "build/X86_UMT_T32_W2/gem5.opt," "build/X86_UMT_T32_W2,",
                1,
            ),
            "extra_path": expected.replace(
                "\nstatus=0/SUCCESS\n",
                ",build/X86_UMT_T32_W2/extra\nstatus=0/SUCCESS\n",
                1,
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for label, record in records.items():
                path = root / (label + ".stdout")
                path.write_text(record, encoding="ascii")
                if label == "live_shape":
                    ingress.validate_clean_stdout(path)
                else:
                    with self.assertRaisesRegex(
                        RuntimeError, "initial-absence tuple"
                    ):
                        ingress.validate_clean_stdout(path)

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
            {"CCFLAGS_EXTRA": ingress.TRACE_DEFINE_VALUE}
        )
        for invalid in (
            {"CCFLAGS_EXTRA": "LANL_MAA_UMT_INGRESS_TRACE_TEST"},
            {"CCFLAGS_EXTRA": "-DLANL_MAA_UMT_INGRESS_TRACE_TEST=1"},
            {"CCFLAGS_EXTRA": ingress.TRACE_DEFINE_FLAGS[0]},
            {"CCFLAGS_EXTRA": ingress.TRACE_DEFINE_FLAGS[1]},
            {"CCFLAGS_EXTRA": " ".join(reversed(ingress.TRACE_DEFINE_FLAGS))},
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

    def test_guest_compatibility_prefix_is_exact_ordered_and_pinned(self):
        expected = list(ingress.GUEST_COMPATIBILITY_PREFIX)
        self.assertEqual(ingress.verify_guest_compatibility_source(), expected)
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            runner = root / "tests/lanl_maa/umt_ingress_micro_process_cpu.py"
            runner.parent.mkdir(parents=True)

            def validate(values, passes):
                runner.write_text(
                    "q.env = " + repr(values) + "\n", encoding="utf-8"
                )
                if passes:
                    self.assertEqual(
                        ingress.verify_guest_compatibility_source(root),
                        expected,
                    )
                else:
                    with self.assertRaisesRegex(RuntimeError, "compatibility"):
                        ingress.verify_guest_compatibility_source(root)

            validate(expected + ["LANL_MAA_UMT_SUBMIT=1"], True)
            validate(
                expected[:3] + expected[4:] + ["LANL_MAA_UMT_SUBMIT=1"], False
            )
            altered = list(expected)
            altered[4] += ",BAD"
            validate(altered + ["LANL_MAA_UMT_SUBMIT=1"], False)
            reordered = list(expected)
            reordered[3], reordered[4] = reordered[4], reordered[3]
            validate(reordered + ["LANL_MAA_UMT_SUBMIT=1"], False)
            validate(expected[:-1] + ["LANL_MAA_UMT_SUBMIT=1"], False)

    def test_fresh_build_requires_both_exact_paths_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = pathlib.Path(temporary) / "source"
            self.assertEqual(
                wrapper.require_initial_absence(source),
                [
                    str(source / wrapper.BUILD_ROOT_RELATIVE),
                    str(source / wrapper.TARGET_RELATIVE),
                    str(source / wrapper.OBJECT_RELATIVE),
                ],
            )
            for relative in wrapper.BUILD_RELATIVES:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"stale")
                with self.assertRaisesRegex(RuntimeError, "variant root"):
                    wrapper.require_initial_absence(source)
                shutil.rmtree(source / wrapper.BUILD_ROOT_RELATIVE)

    def test_failed_phases_restore_both_paths_to_exact_absence(self):
        for phase, create_new in (
            ("object_prebuild", False),
            ("full_build", True),
            ("full_build_validation", True),
            ("observer_gate", True),
            ("observer_report", True),
        ):
            with tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary)
                source = root / "source"
                source.mkdir()
                evidence = root / "evidence"
                evidence.mkdir()
                ownership = wrapper.create_generated_root_ownership(
                    source,
                    {
                        "unit": wrapper.BUILD_UNIT,
                        "invocation_id": "a" * 32,
                        "wrapper_pid": 70,
                        "wrapper_proc_start_ticks": "1234",
                    },
                )
                if create_new:
                    for relative in wrapper.BUILD_RELATIVES:
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
                    ownership,
                    phase,
                    RuntimeError("failed"),
                )
                self.assertEqual(receipt["phase"], phase)
                self.assertEqual(len(receipt["phase_outputs"]), 4)
                self.assertEqual(
                    receipt["status"], "owned_variant_removed_exact_absence"
                )
                build_root = source / wrapper.BUILD_ROOT_RELATIVE
                self.assertFalse(build_root.exists())
                restored = receipt["restored"]
                self.assertEqual(restored["restored_state"], "absent")

    def test_outer_transaction_recovers_every_post_build_publication_failure(
        self,
    ):
        injection_points = (
            "manifest",
            "literal_scan",
            "gate_stream_open",
            "report_copy",
            "transcript",
            "conformance_gate_stream_open",
            "conformance_report_copy",
            "evidence_hashing",
            "attestation_publication",
        )
        for injected in injection_points:
            with self.subTest(
                injected=injected
            ), tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary)
                source = root / "source"
                source.mkdir()
                evidence = (
                    root / "identity" / "pki4-conformance-build-evidence-v19"
                )
                calls = 0

                def fake_run(_argv, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        object_path = source / wrapper.OBJECT_RELATIVE
                        object_path.parent.mkdir(parents=True, exist_ok=True)
                        object_path.write_bytes(
                            b"\0".join(wrapper.COMPILED_MARKERS)
                        )
                        kwargs["stdout"].write(
                            b"g++ -o build/X86_UMT_T32_W2/mem/LANLMAA/lanl_maa.o "
                            b"-c src/mem/LANLMAA/lanl_maa.cc "
                            + wrapper.TRACE_DEFINE_VALUE.encode()
                            + b"\n"
                        )
                    elif calls == 2:
                        target = source / wrapper.TARGET_RELATIVE
                        target.write_bytes(b" ".join(wrapper.COMPILED_MARKERS))
                        for (
                            relative,
                            expected,
                        ) in wrapper.CONFIG_ARTIFACTS.values():
                            path = source / relative
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(expected)
                        kwargs["stdout"].write(
                            b"[ LINK ] X86_UMT_T32_W2/gem5.opt\n"
                        )
                    elif calls == 3:
                        target = source / wrapper.TARGET_RELATIVE
                        report = {
                            "schema": "lanl-maa-umt-production-ingress-trace-v3",
                            "status": "passed",
                            "source_root": str(source),
                            "input_source_sha256": {},
                            "binary": str(target),
                            "binary_sha256": wrapper.sha256(target),
                            "required_define": "LANL_MAA_UMT_INGRESS_TRACE_TEST",
                            "compiled_binary_markers": [
                                item.decode()
                                for item in wrapper.LEGACY_COMPILED_MARKERS
                            ],
                            "cells": [
                                {
                                    "tokens": tokens,
                                    "issue_width": width,
                                    "waiter_counts": [1, 7, 8],
                                    "abi_boundaries": ["D32", "D64"],
                                    "two_lane_serialization": "rejected_by_trace_difference",
                                    "selected_token_text": "numeric_for_denominator_and_source_sentinel",
                                    "default_off": "compiled_without_observer_macro",
                                }
                                for tokens, width in (
                                    (24, 1),
                                    (24, 2),
                                    (32, 1),
                                    (32, 2),
                                )
                            ],
                        }
                        kwargs["stdout"].write(json.dumps(report).encode())
                    else:
                        kwargs["stdout"].write(
                            wrapper.CONFORMANCE_REPORT.read_bytes()
                        )
                    return mock.Mock(returncode=0)

                def inject(name):
                    if name == injected:
                        raise OSError("injected " + name)

                patches = {
                    "SOURCE_ROOT": str(source),
                    "SOURCE_SHA256": {},
                    "LEGACY_SOURCE_SHA256": {},
                    "BUILD_SYSTEM_SHA256": {},
                }
                with mock.patch.multiple(
                    wrapper, **patches
                ), mock.patch.object(
                    wrapper, "validate_source", return_value=None
                ), mock.patch.object(
                    wrapper.subprocess, "run", side_effect=fake_run
                ), mock.patch.object(
                    wrapper, "fault_injection_point", side_effect=inject
                ), mock.patch.dict(
                    wrapper.os.environ, {"INVOCATION_ID": "a" * 32}
                ), self.assertRaises(
                    OSError
                ):
                    wrapper.main(
                        [
                            "--unit",
                            wrapper.BUILD_UNIT,
                            "--source",
                            str(source),
                            "--evidence-dir",
                            str(evidence),
                        ]
                    )
                build_root = source / wrapper.BUILD_ROOT_RELATIVE
                self.assertFalse(build_root.exists())
                receipt = json.loads(
                    (evidence / "failure-restore.json").read_text()
                )
                self.assertEqual(receipt["schema"], wrapper.FAILURE_SCHEMA)
                self.assertEqual(
                    receipt["status"], "owned_variant_removed_exact_absence"
                )
                self.assertEqual(
                    receipt["phase_outputs"].keys(),
                    {
                        "object-prebuild.stdout",
                        "object-prebuild.stderr",
                        "build.stdout",
                        "build.stderr",
                    },
                )

    def test_unowned_or_concurrent_generated_root_is_never_removed(self):
        for mode in ("mutated_sentinel", "replaced_root"):
            with self.subTest(
                mode=mode
            ), tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary)
                source, evidence = root / "source", root / "evidence"
                source.mkdir()
                evidence.mkdir()
                for name in (
                    "object-prebuild.stdout",
                    "object-prebuild.stderr",
                    "build.stdout",
                    "build.stderr",
                ):
                    (evidence / name).write_bytes(b"phase")
                common = {
                    "unit": wrapper.BUILD_UNIT,
                    "invocation_id": "a" * 32,
                    "wrapper_pid": 71,
                    "wrapper_proc_start_ticks": "1234",
                }
                ownership = wrapper.create_generated_root_ownership(
                    source, common
                )
                build_root = source / wrapper.BUILD_ROOT_RELATIVE
                if mode == "mutated_sentinel":
                    pathlib.Path(ownership["sentinel"]).write_text("forged")
                else:
                    owned_aside = root / "owned-aside"
                    build_root.rename(owned_aside)
                    build_root.mkdir()
                    (build_root / "concurrent").write_text("keep")
                with self.assertRaisesRegex(RuntimeError, "unowned"):
                    wrapper.restore_canonical_paths(
                        source,
                        evidence,
                        ownership,
                        "injected",
                        RuntimeError("failed"),
                    )
                self.assertTrue(build_root.exists())
                if mode == "replaced_root":
                    self.assertEqual(
                        (build_root / "concurrent").read_text(), "keep"
                    )
                receipt = json.loads(
                    (evidence / "failure-restore.json").read_text()
                )
                self.assertEqual(
                    receipt["status"],
                    "blocked_unowned_or_concurrent_root_retained",
                )

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
        valid = (prefix + wrapper.TRACE_DEFINE_VALUE + "\n").encode()
        for validator in (
            wrapper.validate_object_compile_transcript,
            ingress.validate_object_compile_transcript,
        ):
            validator(valid)
            for suffix in (
                "",
                wrapper.TRACE_DEFINE_FLAGS[0],
                wrapper.TRACE_DEFINE_FLAGS[1],
                "-DLANL_MAA_UMT_INGRESS_TRACE_TEST=1",
                "-DLANL_MAA_UMT_PKI4_CONFORMANCE_TEST=1",
                " ".join(reversed(wrapper.TRACE_DEFINE_FLAGS)),
                wrapper.TRACE_DEFINE_VALUE
                + " -DLANL_MAA_UMT_PKI4_CONFORMANCE_TEST",
                "CPPDEFINES=LANL_MAA_UMT_INGRESS_TRACE_TEST",
                "CCFLAGS_EXTRA=-DLANL_MAA_UMT_INGRESS_TRACE_TEST",
            ):
                with self.assertRaisesRegex(RuntimeError, "define"):
                    validator((prefix + suffix + "\n").encode())

    def test_each_phase_requires_its_rebuilt_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = pathlib.Path(temporary)
            for relative in wrapper.BUILD_RELATIVES:
                with self.assertRaisesRegex(RuntimeError, "recreate"):
                    wrapper.validate_rebuilt_path(source, relative)
            target = source / wrapper.TARGET_RELATIVE
            target.parent.mkdir(parents=True)
            target.write_bytes(b"new target")
            self.assertEqual(
                wrapper.validate_rebuilt_path(source, wrapper.TARGET_RELATIVE)[
                    "path"
                ],
                str(target),
            )

    def test_missing_compiled_literal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = pathlib.Path(temporary) / "gem5.opt"
            obj = pathlib.Path(temporary) / "lanl_maa.o"
            target.write_bytes(b"UMT_INGRESS kind= but no hold marker")
            obj.write_bytes(b" ".join(wrapper.COMPILED_MARKERS))
            with self.assertRaisesRegex(RuntimeError, "dual trace"):
                wrapper.validate_compiled_literals(target, obj)
            complete = b"\0".join(wrapper.COMPILED_MARKERS)
            target.write_bytes(complete)
            for missing in wrapper.COMPILED_MARKERS:
                obj.write_bytes(complete.replace(missing, b"missing", 1))
                with self.assertRaisesRegex(RuntimeError, "dual trace"):
                    wrapper.validate_compiled_literals(target, obj)
            obj.write_bytes(complete)
            wrapper.validate_compiled_literals(target, obj)

    def test_no_clobber_evidence_writer_rejects_existing_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "attestation.json"
            path.write_text("preserve", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                wrapper.no_clobber_json(path, {"forged": True})
            self.assertEqual(path.read_text(encoding="utf-8"), "preserve")

    def test_build_unit_is_retained_for_terminal_snapshot(self):
        evidence = pathlib.Path(
            "/campaign/identity/pki4-conformance-build-evidence-v19"
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
            "/campaign/identity/pki4-conformance-build-evidence-v19"
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
                "/campaign/identity/pki4-conformance-build-evidence-v19"
            )
        with mock.patch.object(
            ingress, "DISPATCH_PROPERTIES", rejected
        ), self.assertRaisesRegex(RuntimeError, "resource mapping"):
            ingress.systemd_run_command("arm.service", ["/bin/true"])

    def test_dry_build_plan_is_fresh_exact_and_no_clobber(self):
        with tempfile.TemporaryDirectory() as temporary:
            campaign = pathlib.Path(temporary) / "campaign"
            output = campaign / "build-plan-v19.json"
            with mock.patch.object(ingress, "BUILD_CAMPAIGN_ROOT", campaign):
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
                {"CCFLAGS_EXTRA": ingress.TRACE_DEFINE_VALUE},
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
                plan["required_initial_absent_paths"],
                [
                    str(ingress.BUILD_ROOT),
                    str(ingress.CANONICAL_GEM5),
                    str(ingress.BUILD_OBJECT),
                ],
            )
            self.assertEqual(
                plan["fresh_full_build_expected_cost"]["cpu_cores"], 4
            )
            self.assertEqual(
                plan["fresh_full_build_expected_cost"][
                    "hard_runtime_cap_seconds"
                ],
                4 * 3600,
            )
            before = output.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "one fresh campaign"):
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
                "CCFLAGS_EXTRA": (
                    "-DLANL_MAA_UMT_INGRESS_TRACE_TEST "
                    "-DLANL_MAA_UMT_PKI4_CONFORMANCE_TEST"
                ),
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
                "waiters=%u token=%llu pre=",
            ],
            "cells": [],
        }
        with self.assertRaisesRegex(RuntimeError, "exact v3"):
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
            legacy_source_hashes = {
                relative: source_hashes[relative]
                for relative in ingress.LEGACY_INSTRUMENTATION_SOURCES
            }
            conformance_source_hashes = {
                relative: source_hashes[relative]
                for relative in ingress.CONFORMANCE_INSTRUMENTATION_SOURCES
            }
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
            invocation_id, pid, start_ticks = "a" * 32, 77, "12345"
            build = source / "build/X86_UMT_T32_W2"
            build.mkdir(parents=True)
            gem5, object_file, config_compute_tokens, config_fp_issue_width = (
                build / "gem5.opt",
                build / "mem/LANLMAA/lanl_maa.o",
                build / "config/lanl_maa_umt_compute_tokens.hh",
                build / "config/lanl_maa_umt_fp_issue_width.hh",
            )
            object_file.parent.mkdir(parents=True)
            config_compute_tokens.parent.mkdir(parents=True)
            object_file.write_bytes(b"\0".join(ingress.COMPILED_MARKERS))
            config_compute_tokens.write_bytes(
                b"#define LANL_MAA_UMT_COMPUTE_TOKENS 32\n"
            )
            config_fp_issue_width.write_bytes(
                b"#define LANL_MAA_UMT_FP_ISSUE_WIDTH 2\n"
            )
            gem5.write_bytes(b"gem5\0" + b"\0".join(ingress.COMPILED_MARKERS))
            root_stat = build.stat()
            ownership_record = {
                "schema": ingress.GENERATED_ROOT_OWNERSHIP_SCHEMA,
                "unit": ingress.BUILD_UNIT,
                "invocation_id": invocation_id,
                "wrapper_pid": pid,
                "wrapper_proc_start_ticks": start_ticks,
                "nonce": "b" * 64,
                "source_root": str(source),
                "generated_root": str(build),
                "root_device": root_stat.st_dev,
                "root_inode": root_stat.st_ino,
            }
            sentinel = build / wrapper.SENTINEL_NAME
            sentinel.write_bytes(
                wrapper.canonical_json_bytes(ownership_record)
            )
            sentinel_stat = sentinel.stat()
            ownership = {
                **ownership_record,
                "sentinel": str(sentinel),
                "sentinel_sha256": ingress.sha256(sentinel),
                "sentinel_device": sentinel_stat.st_dev,
                "sentinel_inode": sentinel_stat.st_ino,
                "success_state": "retained_in_generated_root",
            }
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
                "config_compute_tokens": {
                    "path": str(config_compute_tokens),
                    "sha256": ingress.sha256(config_compute_tokens),
                },
                "config_fp_issue_width": {
                    "path": str(config_fp_issue_width),
                    "sha256": ingress.sha256(config_fp_issue_width),
                },
            }
            initial_absent_paths = [str(build), str(gem5), str(object_file)]
            invalidated = {}
            attestation_value = {
                "schema": "lanl-maa-umt-pki4-dual-build-attestation-v19",
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
                "initial_absent_paths": initial_absent_paths,
                "invalidated_artifacts": invalidated,
                "target_paths_absent_after_clean": True,
                "generated_root_ownership": ownership,
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
                    marker.decode("ascii")
                    for marker in ingress.COMPILED_MARKERS
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
                "conformance_gate": {
                    "command": [
                        "/usr/bin/python3",
                        str(
                            source
                            / "tests/lanl_maa/run_umt_pki4_conformance_gate.py"
                        ),
                        "--cxx",
                        "g++",
                    ],
                    "returncode": 0,
                    "report": {},
                    "transcript": {},
                    "provenance": {
                        "host_report": {
                            "path": str(ingress.CONFORMANCE_REPORT),
                            "sha256": ingress.CONFORMANCE_REPORT_SHA256,
                        },
                        "temporal_plan": {
                            "path": str(ingress.TEMPORAL_PLAN),
                            "sha256": ingress.TEMPORAL_PLAN_SHA256,
                        },
                        "independent_review": {
                            "path": str(ingress.PROMOTION_REVIEW),
                            "sha256": ingress.PROMOTION_REVIEW_SHA256,
                        },
                    },
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
                        "_SYSTEMD_USER_UNIT": b"init.scope",
                        "_SYSTEMD_CGROUP": b"/user.slice/user-1000.slice/init.scope",
                        "_COMM": b"systemd",
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
                        "schema": "lanl-maa-umt-production-ingress-trace-v3",
                        "status": "passed",
                        "source_root": str(source),
                        "input_source_sha256": legacy_source_hashes,
                        "binary": str(gem5),
                        "binary_sha256": ingress.sha256(gem5),
                        "required_define": ingress.TRACE_BUILD_DEFINE,
                        "compiled_binary_markers": [
                            "UMT_INGRESS kind=",
                            "d64_hold cycle=",
                            "waiters=%u token=%llu pre=",
                        ],
                        "cells": [
                            {
                                "tokens": t,
                                "issue_width": w,
                                "waiter_counts": [1, 7, 8],
                                "abi_boundaries": ["D32", "D64"],
                                "two_lane_serialization": "rejected_by_trace_difference",
                                "selected_token_text": "numeric_for_denominator_and_source_sentinel",
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
                    + "\ninitial_absent=build/X86_UMT_T32_W2,"
                    "build/X86_UMT_T32_W2/gem5.opt,"
                    "build/X86_UMT_T32_W2/mem/LANLMAA/lanl_maa.o\n"
                    "status=0/SUCCESS\n",
                ),
                "clean_stderr": evidence_file("clean.stderr", ""),
                "object_prebuild_stdout": evidence_file(
                    "object-prebuild.stdout",
                    "g++ -o build/X86_UMT_T32_W2/mem/LANLMAA/lanl_maa.o "
                    "-c src/mem/LANLMAA/lanl_maa.cc "
                    + ingress.TRACE_DEFINE_VALUE
                    + "\n",
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
                    json.dumps(legacy_source_hashes),
                ),
                "conformance_source_manifest": evidence_file(
                    "conformance-input-source-sha256.json",
                    json.dumps(conformance_source_hashes),
                ),
                "conformance_stdout": evidence_file(
                    "conformance.stdout",
                    ingress.CONFORMANCE_REPORT.read_text(encoding="utf-8"),
                ),
                "conformance_stderr": evidence_file("conformance.stderr", ""),
                "conformance_report": evidence_file(
                    "conformance-report.json",
                    ingress.CONFORMANCE_REPORT.read_text(encoding="utf-8"),
                ),
                "conformance_transcript": evidence_file(
                    "conformance-transcript.txt", "status=0/SUCCESS\n"
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
                            "config_compute_tokens": str(
                                config_compute_tokens
                            ),
                            "config_compute_tokens_sha256": ingress.sha256(
                                config_compute_tokens
                            ),
                            "config_fp_issue_width": str(
                                config_fp_issue_width
                            ),
                            "config_fp_issue_width_sha256": ingress.sha256(
                                config_fp_issue_width
                            ),
                            "compiled_binary_markers": [
                                marker.decode("ascii")
                                for marker in ingress.COMPILED_MARKERS
                            ],
                            "markers_verified_in": [
                                str(gem5),
                                str(object_file),
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
            attestation_value["conformance_gate"]["report"] = evidence_items[
                "conformance_report"
            ]
            attestation_value["conformance_gate"][
                "transcript"
            ] = evidence_items["conformance_transcript"]
            attestation.write_text(
                json.dumps(attestation_value), encoding="utf-8"
            )
            proof = {
                "schema": ingress.SCHEMA_BUILD_PROOF,
                "status": "passed",
                "producer": (
                    "systemd-pki4-dual-build-proof-v19-service-wrapper"
                ),
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
                "trace_defines": [
                    ingress.TRACE_BUILD_DEFINE,
                    ingress.CONFORMANCE_BUILD_DEFINE,
                ],
                "instrumentation_source_sha256": source_hashes,
                "build_system_source_sha256": build_system_hashes,
                "clean_method": ingress.BUILD_CLEAN_METHOD,
                "initial_absent_paths": initial_absent_paths,
                "invalidated_artifacts": invalidated,
                "target_paths_absent_after_clean": True,
                "generated_root_ownership": ownership,
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
                    "input_source_sha256": legacy_source_hashes,
                    "binary": str(gem5),
                    "binary_sha256": ingress.sha256(gem5),
                    "stdout": evidence_items["observer_stdout"],
                    "stderr": evidence_items["observer_stderr"],
                    "report": evidence_items["observer_report"],
                    "transcript": evidence_items["observer_transcript"],
                    "status": "passed",
                },
                "conformance_gate": {
                    "command": [
                        "/usr/bin/python3",
                        str(
                            source
                            / "tests/lanl_maa/run_umt_pki4_conformance_gate.py"
                        ),
                        "--cxx",
                        "g++",
                    ],
                    "input_source_sha256": conformance_source_hashes,
                    "stdout": evidence_items["conformance_stdout"],
                    "stderr": evidence_items["conformance_stderr"],
                    "report": evidence_items["conformance_report"],
                    "transcript": evidence_items["conformance_transcript"],
                    "provenance": attestation_value["conformance_gate"][
                        "provenance"
                    ],
                    "status": "passed_host_only",
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
                "BUILD_ROOT": build,
                "GENERATED_ROOT_SENTINEL": sentinel,
                "BUILD_OBJECT": object_file,
                "CONFIG_ARTIFACTS": {
                    "config_compute_tokens": (
                        config_compute_tokens,
                        b"#define LANL_MAA_UMT_COMPUTE_TOKENS 32\n",
                    ),
                    "config_fp_issue_width": (
                        config_fp_issue_width,
                        b"#define LANL_MAA_UMT_FP_ISSUE_WIDTH 2\n",
                    ),
                },
                "INSTRUMENTATION_SOURCES": source_hashes,
                "LEGACY_INSTRUMENTATION_SOURCES": legacy_source_hashes,
                "CONFORMANCE_INSTRUMENTATION_SOURCES": (
                    conformance_source_hashes
                ),
                "BUILD_SYSTEM_SOURCES": build_system_hashes,
            }
            with mock.patch.multiple(ingress, **patches), mock.patch.object(
                ingress,
                "validate_conformance_provenance",
                side_effect=lambda value: value,
            ):
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
                                "/data1/nier/worktrees/DX100-umt-pki4-conformance-source-v3-20260831",
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

                obsolete = json.loads(json.dumps(proof))
                obsolete_artifacts = obsolete["build_artifacts"]
                obsolete_artifacts["config_hh"] = obsolete_artifacts.pop(
                    "config_compute_tokens"
                )
                obsolete_artifacts["config_cc"] = obsolete_artifacts.pop(
                    "config_fp_issue_width"
                )
                with self.assertRaisesRegex(RuntimeError, "build artifacts"):
                    attempt(obsolete)

                predecessor = json.loads(json.dumps(proof))
                predecessor[
                    "schema"
                ] = "lanl-maa-umt-ingress-instrumented-gem5-build-proof-v13"
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
                            "schema": "lanl-maa-umt-production-ingress-trace-v3",
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

    def test_v15_combined_contract_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            campaign = pathlib.Path(temporary) / "campaign"
            campaign.mkdir()
            contract = campaign / ingress.CONTRACT_FILENAME
            contract.write_text(
                json.dumps(
                    {
                        "schema": "lanl-maa-umt-ingress-contract-v15",
                        "status": "frozen_before_dispatch",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RuntimeError, "v16 contract semantics"
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

    def test_v16_dispatch_runs_the_pinned_wrapper_not_gem5_directly(self):
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
            "unit": f"umt-ingress-micro-v16-{case}-20260830.service",
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
                    "_SYSTEMD_USER_UNIT": b"init.scope",
                    "_SYSTEMD_CGROUP": b"/user.slice/user-1000.slice/init.scope",
                    "_COMM": b"systemd",
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
                raw
                + record(
                    {
                        "USER_UNIT": b"unrelated.service",
                        "USER_INVOCATION_ID": b"f" * 32,
                        "MESSAGE": b"wrong manager",
                    }
                ),
                raw
                + record(
                    {
                        "_SYSTEMD_USER_UNIT": b"init.scope",
                        "_SYSTEMD_CGROUP": b"/user.slice/init.scope",
                        "_COMM": b"not-systemd",
                        "USER_UNIT": ingress.BUILD_UNIT.encode(),
                        "USER_INVOCATION_ID": invocation.encode(),
                        "MESSAGE": b"forged manager provenance",
                    }
                ),
                raw
                + record(
                    {
                        "USER_UNIT": ingress.BUILD_UNIT.encode(),
                        "MESSAGE": b"incomplete manager",
                    }
                ),
                raw
                + record(
                    {
                        "_SYSTEMD_USER_UNIT": b"init.scope",
                        "USER_UNIT": ingress.BUILD_UNIT.encode(),
                        "USER_INVOCATION_ID": invocation.encode(),
                        "_PID": str(pid).encode(),
                        "MESSAGE": start,
                    }
                ),
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
