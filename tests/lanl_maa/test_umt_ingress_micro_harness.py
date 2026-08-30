#!/usr/bin/env python3
"""Adversarial fail-closed coverage for the pinned ingress v2 harness."""
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "ingress", HERE / "umt_ingress_micro_harness.py"
)
ingress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingress)


def callback(kind, callback_id, lane, waiters, pre, post, abi=4):
    return (
        f"UMT_INGRESS kind={kind} cycle={100 + callback_id} callback={callback_id} "
        f"lane={lane} packet=0x10 line=0x10 abi={abi} stage=0 group=0 corner=0 "
        f"order={lane} waiters={waiters} token={1000 + callback_id * 10 + lane} "
        f"pre=0x{pre:x} post=0x{post:x} next_engine_tick={200 + callback_id}"
    )


def line(abi_label, kind, cycle, waiters, abi=4):
    return (
        f"UMT_INGRESS kind={abi_label}_{kind} cycle={cycle} line=0x10 abi={abi} "
        f"stage=0 group=0 corner=0 waiters={waiters} pre=0x1 post=0x1"
    )


def submission(abi):
    d32 = abi == "D32"
    values = {key: 0 for key in ingress.SUBMISSION_FIELDS}
    values.update(
        {
            "schema": ingress.SCHEMA_SUBMISSION,
            "opcode": 11,
            "mode": "ordered_wave",
            "wave_calls": 2,
            "wave_corners": 16,
            "direct_arena_submissions": 2,
            "direct_sink_result_words": 16,
            "direct_sink_phi_words": 16,
            "wave_descriptor_sum_area_words": 16,
            "wave_soa_arena_descriptors": 2,
            "descriptor_submissions": 2,
            "submitted_groups": 32,
            "capability_probes": 1,
            "adaptive_wave_selector_threshold_groups": 32,
            "last_error": 0,
            "all_completions_valid": True,
            "ordered_corner_scalar_solves_replaced": True,
        }
    )
    selected = "d32" if d32 else "d64"
    values[f"wave_{selected}_descriptors"] = 2
    values[f"wave_{selected}_groups"] = 32
    values[f"wave_{selected}_decisions"] = 2
    return values


class IngressHarnessTest(unittest.TestCase):
    def valid_rows(self, case):
        abi = 5 if case == "d64-g32" else 4
        rows, callback_id, digest = [], 1, 1

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
            for count in range(1, 8):
                rows.append(line("d64", "hold", 110 + count, count, abi))
            rows.append(line("d64", "release", 118, 8, abi))
        else:
            rows.append(line("d32", "release", 110, 8, abi))
        return rows

    def valid_events(self, case):
        return ingress.parse_debug_file_text("\n".join(self.valid_rows(case)))

    def test_four_cases_accept_complete_synthetic_witnesses(self):
        for case in ingress.CASES:
            with self.subTest(case=case):
                self.assertGreater(
                    ingress.validate_trace(self.valid_events(case), case)[
                        "callbacks"
                    ],
                    0,
                )

    def test_stale_native_identity_fails_closed(self):
        old_binary = ingress.ADAPTIVE_NATIVE
        try:
            ingress.ADAPTIVE_NATIVE = pathlib.Path(
                "/tmp/stale-native-that-does-not-exist"
            )
            with self.assertRaisesRegex(
                RuntimeError, "pinned adaptive native"
            ):
                ingress.verify_native_identity()
        finally:
            ingress.ADAPTIVE_NATIVE = old_binary

    def test_three_field_build_proof_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            gem5 = root / "gem5.opt"
            gem5.write_bytes(b"gem5")
            proof = root / "proof.json"
            proof.write_text(
                json.dumps(
                    {
                        "define": ingress.TRACE_BUILD_DEFINE,
                        "gem5": str(gem5),
                        "gem5_sha256": ingress.sha256(gem5),
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

    def test_no_denominators_fails_closed(self):
        events = self.valid_events("d32-g16")
        for event in events:
            if event["class"] == "callback":
                event["kind"] = "source"
        with self.assertRaisesRegex(RuntimeError, "source and denominator"):
            ingress.validate_trace(events, "d32-g16")

    def test_zero_waiter_hold_fails_closed(self):
        events = self.valid_events("d64-g32")
        next(
            event
            for event in events
            if event["class"] == "line" and event["kind"] == "hold"
        )["waiters"] = 0
        with self.assertRaisesRegex(RuntimeError, "partial 1..7"):
            ingress.validate_trace(events, "d64-g32")

    def test_chronology_digest_and_callback_reappearance_fail_closed(self):
        events = self.valid_events("d32-g16")
        events[-1]["cycle"] = 1
        with self.assertRaisesRegex(RuntimeError, "chronology"):
            ingress.validate_trace(events, "d32-g16")
        events = self.valid_events("d32-g16")
        events[1]["pre"] = "0xdead"
        with self.assertRaisesRegex(RuntimeError, "digest chain"):
            ingress.validate_trace(events, "d32-g16")
        events = self.valid_events("d32-g16")
        events[8]["callback"] = 1
        with self.assertRaisesRegex(RuntimeError, "reappears|contiguous"):
            ingress.validate_trace(events, "d32-g16")

    def test_submission_tamper_fails_closed(self):
        report = submission("D32")
        self.assertEqual(
            ingress.validate_submission(report, "d32-g32")["abi"], "D32-v4"
        )
        report["scalar_ordered_fallback_groups"] = 1
        with self.assertRaisesRegex(RuntimeError, "scalar fallback"):
            ingress.validate_submission(report, "d32-g32")
        report = submission("D64")
        report["wave_d32_descriptors"] = 1
        with self.assertRaisesRegex(RuntimeError, "D64-v5"):
            ingress.validate_submission(report, "d64-g32")

    def test_contract_command_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            command = ["gem5", "--debug-flags=LANLMAA"]
            contract = {
                "schema": "lanl-maa-umt-ingress-contract-v2",
                "status": "frozen_before_dispatch",
                "arms": {
                    name: {
                        "command": command,
                        "command_sha256": "0" * 64,
                        "binary_sha256": ingress.ADAPTIVE_NATIVE_SHA256,
                        "unit": "u",
                    }
                    for name in ingress.CASES
                },
                "resource_policy": ingress.RESOURCE_POLICY,
                "predecessor_v1": {"reuse": "forbidden"},
            }
            path = root / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "command/binary"):
                ingress.dispatch_plan(
                    path, ingress.sha256(path), root / "plan.json"
                )

    def test_strict_proof_accepts_all_bound_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "source"
            source.mkdir()
            for relative in ingress.INSTRUMENTATION_SOURCES:
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("instrumented source", encoding="utf-8")
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
                    "test",
                ],
                cwd=source,
                check=True,
            )
            gem5 = root / "gem5.opt"
            gem5.write_bytes(b"gem5")
            build_out = root / "build.out"
            build_out.write_text("ok", encoding="utf-8")
            build_err = root / "build.err"
            build_err.write_text("", encoding="utf-8")
            gate_out = root / "gate.out"
            gate_out.write_text("ok", encoding="utf-8")
            gate_err = root / "gate.err"
            gate_err.write_text("", encoding="utf-8")
            report = root / "gate.json"
            report.write_text(
                json.dumps(
                    {
                        "schema": "lanl-maa-umt-production-ingress-trace-v2",
                        "status": "passed",
                    }
                ),
                encoding="utf-8",
            )
            artifact = lambda path: {
                "path": str(path),
                "sha256": ingress.sha256(path),
            }
            proof = {
                "schema": ingress.SCHEMA_BUILD_PROOF,
                "status": "passed",
                "gem5": str(gem5.resolve()),
                "gem5_sha256": ingress.sha256(gem5),
                "source_commit": ingress.git_output(
                    source, "rev-parse", "HEAD"
                ),
                "source_tree": ingress.git_output(
                    source, "rev-parse", "HEAD^{tree}"
                ),
                "source_worktree": str(source),
                "build_argv": [
                    "scons",
                    "CPPDEFINES=" + ingress.TRACE_BUILD_DEFINE,
                ],
                "build_environment": {
                    "CPPDEFINES": ingress.TRACE_BUILD_DEFINE
                },
                "trace_define": ingress.TRACE_BUILD_DEFINE,
                "instrumentation_source_sha256": {
                    relative: ingress.sha256(source / relative)
                    for relative in ingress.INSTRUMENTATION_SOURCES
                },
                "build_stdout": artifact(build_out),
                "build_stderr": artifact(build_err),
                "observer_gate": {
                    "command": [
                        "python3",
                        str(
                            source
                            / "tests/lanl_maa/run_umt_production_ingress_trace_gate.py"
                        ),
                    ],
                    "stdout": artifact(gate_out),
                    "stderr": artifact(gate_err),
                    "report": artifact(report),
                    "status": "passed",
                },
            }
            proof_path = root / "proof.json"
            proof_path.write_text(json.dumps(proof), encoding="utf-8")
            self.assertEqual(
                ingress.read_build_proof(
                    proof_path,
                    ingress.sha256(proof_path),
                    gem5.resolve(),
                    ingress.sha256(gem5),
                ),
                proof_path.resolve(),
            )


if __name__ == "__main__":
    unittest.main()
