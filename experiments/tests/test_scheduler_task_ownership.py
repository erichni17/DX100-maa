import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_full_tile_recovery as full_recovery  # noqa: E402
import run_normal_tile_recovery as normal_recovery  # noqa: E402


class SchedulerTaskOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write_workflow(self, task_ids):
        path = self.root / "lane.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "name": "lane",
                    "tasks": [
                        {"id": task_id, "command": ["true"]}
                        for task_id in task_ids
                    ],
                }
            )
        )
        return path

    def write_primary_state(self, states, ownership_scope=None):
        path = self.root / "primary.json"
        document = {
            "version": 1,
            "name": "primary",
            "tasks": {
                task_id: {"state": state}
                for task_id, state in states.items()
            },
        }
        if ownership_scope is not None:
            document["ownership_scope"] = ownership_scope
        path.write_text(json.dumps(document))
        return path

    def test_shared_scope_allows_scheduler_to_defer_live_primary_task(self):
        workflow = self.write_workflow(["live", "pending"])
        primary = self.write_primary_state(
            {"live": "running", "pending": "pending"},
            ownership_scope="full-tile-sweep",
        )

        self.assertEqual(
            normal_recovery.verify_primary_task_states(
                workflow, primary, "full-tile-sweep"
            ),
            {"live": "running", "pending": "pending"},
        )

    def test_live_primary_task_fails_closed_without_shared_scope(self):
        workflow = self.write_workflow(["live"])
        primary = self.write_primary_state({"live": "running"})

        with self.assertRaisesRegex(SystemExit, "live in primary workflow"):
            normal_recovery.verify_primary_task_states(workflow, primary)

    def test_auxiliary_rejects_missing_or_different_primary_scope(self):
        workflow = self.write_workflow(["task"])
        primary = self.write_primary_state({"task": "pending"})

        with self.assertRaisesRegex(
            SystemExit, "does not share task ownership scope"
        ):
            normal_recovery.verify_primary_task_states(
                workflow, primary, "full-tile-sweep"
            )

        primary = self.write_primary_state(
            {"task": "pending"}, ownership_scope="other-sweep"
        )
        with self.assertRaisesRegex(
            SystemExit, "does not share task ownership scope"
        ):
            normal_recovery.verify_primary_task_states(
                workflow, primary, "full-tile-sweep"
            )

    def test_scope_is_inherited_and_conflicts_fail_closed(self):
        self.assertEqual(
            normal_recovery.select_ownership_scope(
                None, "full-tile-sweep", "full-tile-sweep"
            ),
            "full-tile-sweep",
        )
        with self.assertRaisesRegex(SystemExit, "scopes disagree"):
            normal_recovery.select_ownership_scope("first", "second")

    def test_workflow_launch_passes_nonblocking_ownership_scope(self):
        captured = {}

        class Completed:
            returncode = 0

        def fake_run(command, stdout, stderr):
            captured["command"] = command
            return Completed()

        log = self.root / "manager.log"
        workflow = self.root / "workflow.json"
        workflow.write_text("{}")
        with mock.patch.object(
            full_recovery.subprocess, "run", side_effect=fake_run
        ):
            rc = full_recovery.run_workflow(
                "/runtime",
                self.root / "state",
                workflow,
                4,
                log,
                "full-tile-sweep",
            )

        self.assertEqual(rc, 0)
        command = captured["command"]
        position = command.index("--ownership-scope")
        self.assertEqual(
            command[position : position + 2],
            ["--ownership-scope", "full-tile-sweep"],
        )
        self.assertEqual(command[-1], str(workflow))


if __name__ == "__main__":
    unittest.main()
