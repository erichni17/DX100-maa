import tempfile
import unittest
from pathlib import Path

from experiments.scripts.validate_xrage_line_handoff_pair import (
    exact_checkpoint,
)


class ExactCheckpointTest(unittest.TestCase):
    def make_arm(self, root, timestamp, state=b"[system]\nvalue=1\n", memory=b"memory"):
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
            changed_state = self.make_arm(root, "state", state=b"[system]\nvalue=2\n")
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


if __name__ == "__main__":
    unittest.main()
