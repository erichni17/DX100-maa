import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import create_failed_gapbs_retry as retry  # noqa: E402


def task(task_id):
    return {
        "id": task_id,
        "command": ["old-runner", "gem5.opt", "kernel", "10000000"],
        "cwd": "/old",
        "env": {"KEEP": "yes", "CAMPAIGN_ROOT": "/old/campaign"},
    }


def test_selects_only_failed_gapbs_tasks():
    workflow = {
        "tasks": [
            task("gapbs-bfs-t1024"),
            task("gapbs-bc-t2048"),
            task("nas-cg-t1024"),
        ]
    }
    state = {
        "tasks": {
            "gapbs-bfs-t1024": {"state": "failed", "pid": None},
            "gapbs-bc-t2048": {"state": "running", "pid": 12},
            "nas-cg-t1024": {"state": "failed", "pid": None},
        }
    }
    selected = retry.select_failed_gapbs_tasks(workflow, state)
    assert [item["id"] for item in selected] == ["gapbs-bfs-t1024"]


def test_repair_pins_binary_campaign_and_disables_progress(tmp_path):
    original = task("gapbs-bfs-t1024")
    source = tmp_path / "source"
    runner = source / "benchmarks/gapbs/run_gapbs_tile_smoke.sh"
    binary = tmp_path / "immutable-gem5"
    campaign = tmp_path / "campaign"
    repaired = retry.repair_task(
        original, source, runner, binary, campaign
    )
    assert repaired["command"][0] == str(runner)
    assert repaired["command"][-1] == "0"
    assert repaired["env"]["DX100_GEM5_BIN"] == str(binary)
    assert repaired["env"]["CAMPAIGN_ROOT"] == str(campaign)
    assert repaired["env"]["KEEP"] == "yes"
    assert original["command"][0] == "old-runner"
