#!/usr/bin/env python3
"""Offline and adversarial tests for the dry Gate-B v22 live plan."""

import ast
import copy
import importlib.util
import json
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
PATH = HERE / "umt_pki4_gate_b_live_plan.py"
SPEC = importlib.util.spec_from_file_location("gate_b_live_plan", PATH)
PLAN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLAN)


def leaves(value, prefix=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaves(child, prefix + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from leaves(child, prefix + (index,))
    else:
        yield prefix


def mutate(value, path):
    result = copy.deepcopy(value)
    target = result
    for key in path[:-1]:
        target = target[key]
    key = path[-1]
    old = target[key]
    if isinstance(old, bool):
        target[key] = not old
    elif isinstance(old, int):
        target[key] = old + 1
    elif old is None:
        target[key] = "forged"
    else:
        target[key] = str(old) + "-forged"
    return result


class GateBLivePlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = PLAN.expected_plan(require_clean=False)

    def test_exact_two_abi_arms_and_bounds(self):
        arms = self.plan["dispatch"]["arms"]
        self.assertEqual(set(arms), {"d32-g32", "d64-g31"})
        self.assertEqual(
            {value["abi"] for value in arms.values()}, {"D32", "D64"}
        )
        self.assertEqual(self.plan["dispatch"]["maximum_concurrent_arms"], 2)
        self.assertEqual(
            self.plan["dispatch"]["resource_properties"],
            {
                "CPUQuota": "400%",
                "CPUWeight": "1000",
                "MemoryHigh": str(14 * 1024**3),
                "MemoryMax": str(16 * 1024**3),
                "MemorySwapMax": "0",
                "RuntimeMaxSec": "4h",
            },
        )

    def test_build_proof_is_a_hard_future_dependency(self):
        dependency = self.plan["build_dependency"]
        self.assertTrue(dependency["proof_must_exist_before_contract_freeze"])
        self.assertEqual(dependency["proof_path"], str(PLAN.BUILD_PROOF))
        self.assertEqual(
            dependency["launch_before_all_fields_are_frozen"], "forbidden"
        )
        self.assertFalse(self.plan["authorization"]["systemd"])
        self.assertFalse(self.plan["authorization"]["gem5"])

    def test_lifecycle_and_queue_gates_are_explicit(self):
        successor = self.plan["analysis"][
            "full_successor_required_observations_per_arm"
        ]
        self.assertEqual(
            set(successor["phase_counts_strictly_positive"]),
            {
                "token_admission",
                "token_issue",
                "token_completion",
                "token_release",
                "token_reuse",
            },
        )
        self.assertTrue(successor["replay_authorized"])
        self.assertEqual(successor["terminal_live_token_count"], 0)
        queue = self.plan["analysis"]["queue_timing"]
        self.assertTrue(
            any("C+2" in item for item in queue["required_live_evidence"])
        )
        self.assertIn("not observed RTL", queue["claim_scope"])

    def test_rtl_blocker_is_fail_closed(self):
        replay = self.plan["rtl_full_successor_replay"]
        self.assertEqual(
            replay["status"], "blocked_no_reviewed_canonical_v4_rtl_transactor"
        )
        self.assertFalse(replay["rtl_launch_authorized"])
        self.assertEqual(
            replay["known_callback_only_predecessor"][
                "reuse_for_canonical_v4"
            ],
            "forbidden",
        )
        self.assertGreaterEqual(
            len(replay["required_successor_before_rtl_launch"]), 6
        )

    def test_gate_b_postprocessor_requires_separate_review(self):
        gate = self.plan["pre_dispatch_implementation_gate"]
        self.assertEqual(
            gate["status"], "required_not_implemented_by_dry_plan"
        )
        self.assertFalse(gate["launch_authorized_without_gate"])
        predecessor = self.plan["analysis"]["queue_timing"][
            "reviewed_snapshot_design_predecessor"
        ]
        self.assertEqual(
            predecessor["direct_reuse"],
            "forbidden_v16_v19_contract_does_not_accept_v21_gate_b_proof",
        )

    def test_module_has_no_launch_capability(self):
        tree = ast.parse(PATH.read_text(encoding="utf-8"))
        forbidden_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(
                node.func, ast.Attribute
            ):
                if isinstance(
                    node.func.value, ast.Name
                ) and node.func.value.id in {"subprocess", "os"}:
                    if node.func.attr in {
                        "run",
                        "call",
                        "Popen",
                        "system",
                        "execv",
                        "execve",
                        "spawnv",
                    }:
                        forbidden_calls.append(
                            (node.func.value.id, node.func.attr)
                        )
        self.assertEqual(forbidden_calls, [])

    def test_all_pinned_inputs_match_and_are_regular(self):
        PLAN.verify_pins()

    def test_every_leaf_mutation_is_rejected(self):
        sample_paths = list(leaves(self.plan))[::4]
        self.assertGreater(len(sample_paths), 70)
        for leaf in sample_paths:
            with self.assertRaisesRegex(
                RuntimeError, "not byte-semantically exact"
            ):
                PLAN.validate_value(mutate(self.plan, leaf), self.plan)

    def test_exact_roundtrip_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "plan.json"
            path.write_text(json.dumps(self.plan), encoding="utf-8")
            self.assertEqual(
                PLAN.validate(path, require_clean=False), self.plan
            )

    def test_no_clobber_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "plan.json"
            PLAN.atomic_no_clobber(path, self.plan)
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                PLAN.atomic_no_clobber(path, self.plan)
            self.assertEqual(path.read_bytes(), before)

    def test_symlink_plan_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            real = root / "real.json"
            link = root / "link.json"
            real.write_text(json.dumps(self.plan), encoding="utf-8")
            link.symlink_to(real)
            with self.assertRaisesRegex(RuntimeError, "not a regular file"):
                PLAN.validate(link, require_clean=False)


if __name__ == "__main__":
    unittest.main()
