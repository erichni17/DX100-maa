import tempfile
import unittest
from pathlib import Path

from experiments.scripts.validate_xrage_line_handoff_pair import (
    command_has_line_handoff,
    exact_checkpoint,
    stats_blocks,
    unique_config_value,
    validate_four_descriptor_line_mechanism,
    validate_required_multicontext,
    verifier_record,
)


class ExactCheckpointTest(unittest.TestCase):
    def make_arm(
        self, root, timestamp, state=b"[system]\nvalue=1\n", memory=b"memory"
    ):
        arm = root / timestamp
        checkpoint = arm / "checkpoint" / "cpt.123"
        checkpoint.mkdir(parents=True)
        (arm / "checkpoint" / "cpt.%d").mkdir()
        (checkpoint / "m5.cpt").write_bytes(
            f"## checkpoint generated: {timestamp}\n".encode("ascii") + state
        )
        (checkpoint / "system.physmem.store0.pmem").write_bytes(memory)
        return arm

    def test_ignores_only_timestamp_header(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.make_arm(root, "first")
            second = self.make_arm(root, "second")
            self.assertEqual(exact_checkpoint(first), exact_checkpoint(second))

    def test_detects_serialized_state_or_memory_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = self.make_arm(root, "reference")
            changed_state = self.make_arm(
                root, "state", state=b"[system]\nvalue=2\n"
            )
            changed_memory = self.make_arm(root, "memory", memory=b"changed")
            self.assertNotEqual(
                exact_checkpoint(reference), exact_checkpoint(changed_state)
            )
            self.assertNotEqual(
                exact_checkpoint(reference), exact_checkpoint(changed_memory)
            )

    def test_rejects_checkpoint_without_timestamp_header(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arm = self.make_arm(root, "invalid")
            checkpoint = arm / "checkpoint" / "cpt.123" / "m5.cpt"
            checkpoint.write_bytes(b"[system]\nvalue=1\n")
            with self.assertRaisesRegex(ValueError, "timestamp header"):
                exact_checkpoint(arm)


class RawEvidenceTest(unittest.TestCase):
    def test_stats_parser_preserves_first_and_final_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stats.txt"
            path.write_text(
                "---------- Begin Simulation Statistics ----------\n"
                "simTicks 123 # ROI\n"
                "system.maa.direct_retirement_descriptors 4\n"
                "---------- End Simulation Statistics   ----------\n"
                "---------- Begin Simulation Statistics ----------\n"
                "simTicks 456 # final\n"
                "---------- End Simulation Statistics   ----------\n",
                encoding="ascii",
            )
            blocks = stats_blocks(path)
            self.assertEqual(blocks[0]["simTicks"], "123")
            self.assertEqual(
                blocks[0]["system.maa.direct_retirement_descriptors"], "4"
            )
            self.assertEqual(blocks[1]["simTicks"], "456")

    def test_config_and_command_require_unique_raw_treatment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.ini"
            config.write_text(
                "direct_retirement_line_handoff=true\n", encoding="ascii"
            )
            command = root / "restore.command"
            command.write_text(
                "gem5 --maa_direct_retirement_line_handoff --outdir='a b'\n",
                encoding="ascii",
            )
            self.assertEqual(
                unique_config_value(config, "direct_retirement_line_handoff"),
                "true",
            )
            self.assertTrue(command_has_line_handoff(command))
            config.write_text(
                "direct_retirement_line_handoff=true\n"
                "direct_retirement_line_handoff=false\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(ValueError, "expected one"):
                unique_config_value(config, "direct_retirement_line_handoff")

    def test_verifier_record_is_exact_and_unique(self):
        record = "MAA_GATHER_VERIFY_PASS length=65536 hash=12345\n"
        self.assertEqual(verifier_record(record, Path("log")), (65536, 12345))
        with self.assertRaisesRegex(ValueError, "expected one"):
            verifier_record(record + record, Path("log"))

    def test_four_descriptor_line_gate_requires_exact_bounded_closure(self):
        result = {
            "direct_descriptors": "4",
            "direct_line_acks": "8192",
            "direct_page_fallback_lines": "0",
            "direct_read_responses": "8192",
            "direct_alu_completions": "8192",
            "direct_write_responses": "8192",
            "direct_context_high_water": "4",
            "direct_request_record_high_water": "64",
        }
        validate_four_descriptor_line_mechanism(result)

        for field, invalid in (
            ("direct_line_acks", "8191"),
            ("direct_page_fallback_lines", "1"),
            ("direct_read_responses", "8191"),
            ("direct_alu_completions", "8191"),
            ("direct_write_responses", "8191"),
            ("direct_context_high_water", "1"),
            ("direct_request_record_high_water", "65"),
        ):
            broken = dict(result)
            broken[field] = invalid
            with self.assertRaises(ValueError, msg=field):
                validate_four_descriptor_line_mechanism(broken)

    def test_required_multicontext_gate_binds_population_and_concurrency(self):
        page = {
            "direct_descriptors": "4",
            "direct_context_high_water": "4",
        }
        line = dict(page)
        validate_required_multicontext(page, line, 4, 4)

        for field, value, message in (
            ("direct_descriptors", "3", "descriptor count"),
            ("direct_context_high_water", "3", "context high-water"),
        ):
            broken = dict(line)
            broken[field] = value
            with self.assertRaisesRegex(ValueError, message):
                validate_required_multicontext(page, broken, 4, 4)


if __name__ == "__main__":
    unittest.main()
