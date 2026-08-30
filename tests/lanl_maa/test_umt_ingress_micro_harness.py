#!/usr/bin/env python3
"""Adversarial, dry-only tests for the v3 UMT ingress harness."""
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
                "producer": "systemd-build-proof-v3",
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
                    "pid": 77,
                    "pid_start_time": "123",
                    "started_at": "2026-08-30T00:00:00Z",
                    "resource_policy": ingress.RESOURCE_POLICY,
                    "journal_command": list(ingress.BUILD_JOURNAL_COMMAND),
                    "journal": file("build.journal", "status=0/SUCCESS"),
                    "journal_success": True,
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
                forged["build_invocation"]["resource_policy"][
                    "MemoryMax"
                ] = "1"
                with self.assertRaisesRegex(
                    RuntimeError, "systemd invocation"
                ):
                    attempt(forged)
                forged = json.loads(json.dumps(proof))
                forged["build_invocation"]["unit"] = "forged.service"
                with self.assertRaisesRegex(
                    RuntimeError, "systemd invocation"
                ):
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
                "schema": "lanl-maa-umt-ingress-contract-v3",
                "self_sha256": "0" * 64,
            }
            path = root / "ingress-contract-v3.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(
                RuntimeError, "externally fixed|semantics"
            ):
                ingress.dispatch_plan(
                    path,
                    ingress.sha256(path),
                    root,
                    root / "identity/ingress-dry-dispatch-v3.json",
                )


if __name__ == "__main__":
    unittest.main()
