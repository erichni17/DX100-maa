import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "experiments/scripts/run_fused_p16_forward_progress_probe.py"
SPEC = importlib.util.spec_from_file_location("forward_probe", PATH)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def lines(events):
    return (
        "\n".join(
            f"{tick}: global: event={event} schema=1" for tick, event in events
        )
        + "\n"
    )


class FusedP16ForwardProgressProbeTest(unittest.TestCase):
    def test_restore_is_one_operation_four_indirect_unit_bounded_trace(self):
        command = probe.restore_command(
            Path("guest"), Path("cpt"), Path("out"), 99
        )
        self.assertIn("--debug-flags=MAAVirtualTrace", command)
        self.assertIn("--debug-file=forward_progress.trace", command)
        self.assertEqual(command[command.index("--rel-max-tick") + 1], "99")
        self.assertEqual(
            command[command.index("--maa_num_indirect_units_per_maa=4")],
            "--maa_num_indirect_units_per_maa=4",
        )
        self.assertIn("--maa_soa_jit_predicate_active_credits=16", command)
        self.assertEqual(probe.WORDS, 16384)
        self.assertEqual(probe.PAGES, 4)

    def test_same_tick_execute_churn_is_event_explosion(self):
        records = probe.parse_trace_file_text(
            lines([(7, "indirect_execute")] * 64)
        )
        self.assertEqual(
            probe.classify_timeout(records), "EVENT_EXPLOSION_SAME_TICK"
        )

    def test_advancing_stalls_are_queue_polling_collapse(self):
        records = probe.parse_trace_file_text(
            lines([(tick, "indirect_stall") for tick in range(16)])
        )
        self.assertEqual(
            probe.classify_timeout(records),
            "QUEUE_POLLING_FORWARD_PROGRESS_COLLAPSE",
        )

    def test_real_progress_and_absent_trace_are_not_misclassified(self):
        self.assertEqual(probe.classify_timeout([]), "NO_TRACE_PROGRESS")
        records = probe.parse_trace_file_text(
            lines(
                [
                    (1, "fused_p16_mul_complete"),
                    (2, "fused_p16_product_complete"),
                ]
            )
        )
        self.assertEqual(
            probe.classify_timeout(records),
            "PRODUCER_COMPLETED_BEFORE_WATCHDOG",
        )

    def test_snapshot_accepts_partial_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace"
            trace.write_text(
                lines(
                    [(9, "indirect_execute"), (10, "fused_p16_mul_complete")]
                )
                + "partial"
            )
            snapshot = probe.trace_snapshot(trace, 1.25)
        self.assertEqual(snapshot["events"], 2)
        self.assertEqual(snapshot["last_tick"], 10)
        self.assertEqual(snapshot["mul_completions"], 1)


if __name__ == "__main__":
    unittest.main()
