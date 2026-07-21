import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_full_tile_recovery as full_recovery  # noqa: E402
import run_normal_tile_recovery as normal_recovery  # noqa: E402


def write_state(path, states):
    path.write_text(
        json.dumps(
            {
                "tasks": {
                    f"task-{index}": {"state": state}
                    for index, state in enumerate(states)
                }
            }
        )
    )


def test_terminal_normal_state_can_be_reused(tmp_path):
    state = tmp_path / "state.json"
    write_state(state, ["completed", "failed", "skipped"])
    assert full_recovery.workflow_terminal(state)
    assert not full_recovery.workflow_completed(state)


def test_running_normal_state_cannot_be_reused(tmp_path):
    state = tmp_path / "state.json"
    write_state(state, ["completed", "running"])
    assert not full_recovery.workflow_terminal(state)


def test_only_processes_in_gate_cgroup_are_allowed(monkeypatch, tmp_path):
    gate = tmp_path / "gate"
    inside = gate / "child"
    outside = tmp_path / "other"
    inside.mkdir(parents=True)
    outside.mkdir()
    mapping = {1: inside, 2: outside}
    monkeypatch.setattr(
        normal_recovery,
        "process_cgroup_directory",
        lambda pid: mapping[pid],
    )
    conflicts = [{"pid": 1}, {"pid": 2}]
    assert normal_recovery.outside_allowed_cgroup(conflicts, gate) == [
        {"pid": 2}
    ]
