#!/usr/bin/env python3
"""Synthetic fail-closed coverage for the live ingress harness parser."""
import importlib.util
import pathlib
import tempfile
import unittest
from types import SimpleNamespace

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "ingress", HERE / "umt_ingress_micro_harness.py"
)
ingress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingress)


def callback(kind, callback, lane, waiters, token=1, abi=4):
    return (
        f"UMT_INGRESS kind={kind} cycle={100+callback} callback={callback} lane={lane} "
        f"packet=0x10 line=0x10 abi={abi} stage=0 group=0 corner=0 order={lane} "
        f"waiters={waiters} token={token+lane} pre=0x1 post=0x2 next_engine_tick={200+callback}"
    )


def release(kind="d32", cycle=90, waiters=8, abi=4):
    return (
        f"UMT_INGRESS kind={kind}_release cycle={cycle} line=0x10 abi={abi} stage=0 "
        f"group=0 corner=0 waiters={waiters} pre=0x1 post=0x1"
    )


class IngressHarnessTest(unittest.TestCase):
    def valid(self, case):
        abi = 5 if case == "d64-g32" else 4
        rows = [callback("source", 1, lane, 8, abi=abi) for lane in range(8)]
        rows += [
            callback("denominator", 2, lane, 8, 11, abi) for lane in range(8)
        ]
        if case == "d32-g31":
            rows += [
                callback("source", 3, lane, 7, 20, abi) for lane in range(7)
            ]
        if case == "d64-g32":
            rows += [
                "UMT_INGRESS kind=d64_hold cycle=81 line=0x10 abi=5 stage=0 group=0 corner=0 waiters=7 pre=0x1 post=0x1",
                release("d64", 82, 8, abi),
            ]
        else:
            rows += [release("d32", abi=abi)]
        return ingress.parse_debug_file_text("\n".join(rows))

    def test_four_cases_accept_synthetic_witnesses(self):
        for case in ingress.CASES:
            with self.subTest(case=case):
                result = ingress.validate_trace(self.valid(case), case)
                self.assertGreater(result["callbacks"], 0)

    def test_tampered_lane_fails_closed(self):
        events = self.valid("d32-g16")
        events[1]["lane"] = 4
        with self.assertRaisesRegex(RuntimeError, "lanes"):
            ingress.validate_trace(events, "d32-g16")

    def test_missing_witness_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "missing UMT_INGRESS"):
            ingress.parse_debug_file_text("unrelated debug output")

    def test_unparseable_witness_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "unparseable"):
            ingress.parse_debug_file_text("UMT_INGRESS kind=forged")

    def test_d64_hold_must_precede_later_release(self):
        events = self.valid("d64-g32")
        for event in events:
            if event["class"] == "line" and event["kind"] == "hold":
                event["cycle"] = 99
        with self.assertRaisesRegex(RuntimeError, "hold"):
            ingress.validate_trace(events, "d64-g32")

    def test_contract_and_dry_plan_reject_tamper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            data = root / "contract.json"
            data.write_text('{"status":"not-frozen"}', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                ingress.dispatch_plan(data, "0" * 64, root / "plan.json")

    def test_synthetic_contract_and_dry_plan_are_frozen(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            gem5 = root / "gem5.opt"
            native = root / "test_driver"
            gem5.write_bytes(b"instrumented synthetic gem5")
            native.write_bytes(b"synthetic opcode11 driver")
            cwd = root / "cwd"
            cwd.mkdir()
            proof = root / "proof.json"
            proof.write_text(
                '{"define":"LANL_MAA_UMT_INGRESS_TRACE_TEST","gem5":"%s","gem5_sha256":"%s"}'
                % (gem5.resolve(), ingress.sha256(gem5)),
                encoding="utf-8",
            )
            campaign = root / "campaign"
            contract_path = campaign / "ingress-contract-v1.json"
            args = SimpleNamespace(
                campaign_root=campaign,
                output=contract_path,
                gem5=gem5,
                gem5_sha256=ingress.sha256(gem5),
                instrumented_build_proof=proof,
                instrumented_build_proof_sha256=ingress.sha256(proof),
                native=native,
                native_sha256=ingress.sha256(native),
                native_cwd=cwd,
            )
            contract = ingress.freeze_contract(args)
            self.assertEqual(contract["status"], "frozen_before_dispatch")
            plan = ingress.dispatch_plan(
                contract_path, ingress.sha256(contract_path), root / "dry.json"
            )
            self.assertEqual(plan["status"], "dry_only_not_dispatched")
            self.assertEqual(set(plan["arms"]), set(ingress.CASES))
            self.assertTrue(
                all(
                    "systemd-run" in command
                    for command in plan["arms"].values()
                )
            )


if __name__ == "__main__":
    unittest.main()
