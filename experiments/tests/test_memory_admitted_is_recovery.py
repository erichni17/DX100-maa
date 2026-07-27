import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_memory_admitted_is_recovery as admission  # noqa: E402


class MemoryAdmittedISRecoveryTests(unittest.TestCase):
    def test_live_owned_task_overrides_concurrent_completed_artifact(self):
        task = {
            "state": "running",
            "attempts": 1,
            "unit": "memory-is-t8192-a1.service",
        }
        self.assertEqual(
            admission.classify_task_state(
                task, artifact_complete=True, unit={"active": True}
            ),
            "running",
        )

    def test_tile_parser_ignores_tile_in_prefix(self):
        self.assertEqual(
            admission.tile_from_unit(
                "dx100-full-tile-final-is-memory-v1-20260727-"
                "t32768-a1.service"
            ),
            32768,
        )

    def test_empty_host_has_no_fixed_task_count_limit(self):
        report = admission.compute_admission(
            {
                "total_bytes": 330 * admission.GIB_BYTES,
                "available_bytes": 330 * admission.GIB_BYTES,
            },
            [],
        )
        self.assertEqual(
            report["host_reserve_bytes"], 33 * admission.GIB_BYTES
        )
        self.assertEqual(report["admissible_new_tasks"], 4)

    def test_current_and_future_growth_are_both_reserved(self):
        reservations = [
            {"unconsumed_bytes": 58 * admission.GIB_BYTES},
            {"unconsumed_bytes": 29 * admission.GIB_BYTES},
            {"unconsumed_bytes": 15 * admission.GIB_BYTES},
            {"unconsumed_bytes": 4 * admission.GIB_BYTES},
        ]
        report = admission.compute_admission(
            {
                "total_bytes": 330 * admission.GIB_BYTES,
                "available_bytes": 225 * admission.GIB_BYTES,
            },
            reservations,
        )
        self.assertEqual(report["admissible_new_tasks"], 1)

    def test_released_memory_increases_slots_without_policy_change(self):
        before = admission.compute_admission(
            {
                "total_bytes": 330 * admission.GIB_BYTES,
                "available_bytes": 225 * admission.GIB_BYTES,
            },
            [{"unconsumed_bytes": 106 * admission.GIB_BYTES}],
        )
        after = admission.compute_admission(
            {
                "total_bytes": 330 * admission.GIB_BYTES,
                "available_bytes": 295 * admission.GIB_BYTES,
            },
            [{"unconsumed_bytes": 77 * admission.GIB_BYTES}],
        )
        self.assertEqual(before["admissible_new_tasks"], 1)
        self.assertEqual(after["admissible_new_tasks"], 2)

    def test_negative_headroom_returns_zero_slots(self):
        report = admission.compute_admission(
            {
                "total_bytes": 330 * admission.GIB_BYTES,
                "available_bytes": 80 * admission.GIB_BYTES,
            },
            [{"unconsumed_bytes": 60 * admission.GIB_BYTES}],
        )
        self.assertEqual(report["free_for_new_reservations_bytes"], 0)
        self.assertEqual(report["admissible_new_tasks"], 0)

    def test_each_launched_task_gets_its_own_64_gib_zero_swap_cgroup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = SimpleNamespace(
                source_root=root / "source",
                runtime_root=root / "runtime",
                run_root=root / "runs",
                checkpoint_root=root / "checkpoints",
                gem5_bin=root / "gem5",
                systemd_run="systemd-run",
            )
            completed = SimpleNamespace(
                returncode=0, stdout="running\n", stderr=""
            )
            with patch.object(
                admission.subprocess, "run", return_value=completed
            ) as runner:
                command = admission.launch_task(
                    args, 65536, "memory-is-t65536.service"
                )
            runner.assert_called_once()
            self.assertIn("--property=MemoryHigh=60G", command)
            self.assertIn("--property=MemoryMax=64G", command)
            self.assertIn("--property=MemorySwapMax=0", command)
            self.assertNotIn("RuntimeMaxSec", " ".join(command))
            self.assertEqual(
                command[-6:],
                [
                    "gem5.opt.ovl_base",
                    "65536",
                    "0",
                    "0",
                    "0",
                    "0",
                ],
            )


if __name__ == "__main__":
    unittest.main()
