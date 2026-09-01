"""Focused contract for the deterministic three-arm SSSP locality screen."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/scripts/run_sssp_locality_matched_micro.sh"


def runner_text() -> str:
    return RUNNER.read_text(encoding="utf-8")


class SsspLocalityMatchedMicroTest(unittest.TestCase):
    def test_global_leaf_permutation_is_bijective_and_locality_adversarial(
        self,
    ) -> None:
        destinations = [
            16 * (edge % 4096) + edge // 4096 for edge in range(65536)
        ]
        self.assertEqual(sorted(destinations), list(range(65536)))

        four_k_line_sets = []
        for tile in range(16):
            words = destinations[tile * 4096 : (tile + 1) * 4096]
            lines = [word // 16 for word in words]
            self.assertEqual(len(set(lines)), 4096)
            self.assertTrue(all(lines.count(line) == 1 for line in set(lines)))
            four_k_line_sets.append(set(lines))

        for logical in range(4):
            words = destinations[logical * 16384 : (logical + 1) * 16384]
            lines = [word // 16 for word in words]
            self.assertEqual(len(set(lines)), 4096)
            self.assertTrue(all(lines.count(line) == 4 for line in set(lines)))
            first = four_k_line_sets[logical * 4]
            self.assertTrue(
                all(
                    four_k_line_sets[logical * 4 + page] == first
                    for page in range(4)
                )
            )

    def test_runner_has_exactly_the_requested_arms_and_one_replica(
        self,
    ) -> None:
        runner = runner_text()
        self.assertIn("arms=(native4 native16 hybrid)", runner)
        self.assertIn("replicas=1", runner)
        self.assertIn("full_app_runs=0", runner)
        self.assertIn("external_native_baseline_reruns=0", runner)
        self.assertIn("wall_timeout=none", runner)
        self.assertNotIn("timeout ", runner)
        self.assertIn("replica-1", runner)
        self.assertNotIn("replica-2", runner)

    def test_geometry_and_current_binary_are_frozen(self) -> None:
        runner = runner_text()
        self.assertIn("compile_guest native4 4096 4096", runner)
        self.assertIn("compile_guest native16 16384 16384", runner)
        self.assertIn("compile_guest hybrid 16384 4096", runner)
        self.assertIn('--maa_num_tile_elements="$tile"', runner)
        self.assertIn('--maa_physical_tile_elements="$physical"', runner)
        self.assertIn(
            "gem5_sha256=45206b3433449e10b26bbd8ff32281c06e533c101213097a27d50c364ca3c267",
            runner,
        )
        self.assertIn(
            "frozen_ramulator_sha256=76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753",
            runner,
        )

    def test_prediction_precedes_all_checkpoint_and_restore_execution(
        self,
    ) -> None:
        runner = runner_text()
        prediction = runner.index('cat >"$out/prediction.txt"')
        arm_loop = runner.index('for arm in "${arms[@]}"; do')
        self.assertLess(prediction, arm_loop)
        self.assertIn("prediction_native16_vs_native4=", runner)
        self.assertIn("prediction_hybrid_vs_native4=", runner)
        self.assertIn("promotion_screen=", runner)

    def test_exact_fingerprint_and_work_closure_are_required(self) -> None:
        runner = runner_text()
        expected = (
            "SSSP_FINGERPRINT vertices=69633 reached=69633 unreachable=0 "
            "distance_sum=135168 max_distance=2 hash_a=a0531a7ddb9387df "
            "hash_b=39f1ea63bc8817e8 triangle_violations=0 "
            "missing_predecessors=0 nonpositive_weights=0 negative_distances=0 "
            "result=PASS"
        )
        self.assertIn(expected, runner)
        for token in (
            "eligible_windows=4",
            "routed_windows=4",
            "old_result_words=65536",
            "logical_reorder_words=16384",
            "physical_spd_words=4096",
            "response_closure=1",
            "counts_close=1",
            "$soa_instructions -eq 4",
            "$soa_selected -eq 65536",
            "$soa_captures -eq 65536",
            "$publish_issues -eq 8192",
            "$publish_responses -eq 8192",
        ):
            self.assertIn(token, runner)

    def test_identity_terminal_and_simulated_metric_are_fail_closed(
        self,
    ) -> None:
        runner = runner_text()
        for token in (
            "checkpoint.files.sha256",
            "checkpoint_identity_sha256",
            "verify_checkpoint",
            "artifacts.before.sha256",
            "artifacts.after.sha256",
            "evidence.identity.sha256",
            "return_code=",
            "proc_start_ticks=",
            "m5_exit instruction encountered",
            "simTicks",
            "full_s22_launch_supported",
        ):
            self.assertIn(token, runner)
        self.assertRegex(runner, re.compile(r"campaign_status=PASS\n"))


if __name__ == "__main__":
    unittest.main()
