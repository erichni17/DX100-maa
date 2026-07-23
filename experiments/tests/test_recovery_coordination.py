import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import create_auxiliary_tile_workflow as auxiliary  # noqa: E402
import run_full_tile_recovery as full_recovery  # noqa: E402
import run_normal_tile_recovery as normal_recovery  # noqa: E402
import watch_full_tile_completion as completion_watcher  # noqa: E402

TILE_RUNNERS = (
    Path("benchmarks/UME/run_ume_tile_smoke.sh"),
    Path("benchmarks/NAS/is/run_is_smoke.sh"),
    Path("benchmarks/NAS/cg/run_cg_tile_smoke.sh"),
    Path("benchmarks/gapbs/run_gapbs_tile_smoke.sh"),
    Path("benchmarks/spatter/run_xrage_tile_smoke.sh"),
)


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


def test_processes_in_multiple_owned_cgroups_are_allowed(
    monkeypatch, tmp_path
):
    normal = tmp_path / "normal"
    gate = tmp_path / "gate"
    outside = tmp_path / "other"
    for path in (normal, gate, outside):
        path.mkdir()
    mapping = {1: normal / "child", 2: gate, 3: outside}
    monkeypatch.setattr(
        normal_recovery,
        "process_cgroup_directory",
        lambda pid: mapping[pid],
    )
    conflicts = [{"pid": 1}, {"pid": 2}, {"pid": 3}]
    assert normal_recovery.outside_allowed_cgroups(
        conflicts, [normal, gate]
    ) == [{"pid": 3}]


def test_aggregate_memory_limit_is_enforced(tmp_path):
    normal = tmp_path / "normal"
    gate = tmp_path / "gate"
    auxiliary = tmp_path / "auxiliary"
    for path in (normal, gate, auxiliary):
        path.mkdir()
    (normal / "memory.max").write_text(str(112 * 1024**3))
    (gate / "memory.max").write_text(str(96 * 1024**3))
    (auxiliary / "memory.max").write_text(str(32 * 1024**3))
    summary = normal_recovery.verify_aggregate_memory_max(
        32 * 1024**3, [normal, gate, auxiliary], 272
    )
    assert summary["total"] == 272 * 1024**3
    try:
        normal_recovery.verify_aggregate_memory_max(
            33 * 1024**3, [normal, gate, auxiliary], 272
        )
    except SystemExit as error:
        assert "unsafe aggregate memory.max" in str(error)
    else:
        raise AssertionError("unsafe aggregate was accepted")


def test_rebalanced_aggregate_memory_limit_is_enforced(tmp_path):
    maxima = {
        "normal": 112,
        "gate": 72,
        "auxiliary": 32,
        "xrage-surge": 32,
    }
    cgroups = []
    for name, maximum in maxima.items():
        path = tmp_path / name
        path.mkdir()
        (path / "memory.max").write_text(str(maximum * 1024**3))
        cgroups.append(path)
    summary = normal_recovery.verify_aggregate_memory_max(
        24 * 1024**3, cgroups, 272
    )
    assert summary["total"] == 272 * 1024**3


def test_auxiliary_workflow_preserves_requested_task_order():
    document = {
        "tasks": [
            {"id": "first", "command": ["one"]},
            {"id": "second", "command": ["two"]},
        ]
    }
    assert auxiliary.select_tasks(document, ["second", "first"]) == [
        {"id": "second", "command": ["two"]},
        {"id": "first", "command": ["one"]},
    ]


def test_auxiliary_tasks_must_not_be_live_in_primary_state(tmp_path):
    workflow = tmp_path / "auxiliary.json"
    primary = tmp_path / "primary.json"
    workflow.write_text(
        json.dumps({"tasks": [{"id": "safe"}, {"id": "live"}]})
    )
    primary.write_text(
        json.dumps(
            {
                "tasks": {
                    "safe": {"state": "pending"},
                    "live": {"state": "running"},
                }
            }
        )
    )
    try:
        normal_recovery.verify_primary_task_states(workflow, primary)
    except SystemExit as error:
        assert "live" in str(error)
    else:
        raise AssertionError("live primary task was accepted")


def test_completion_watcher_requires_auxiliary_retry_record():
    completed = {"tasks": {"task": {"state": "completed"}}}
    documents = [completed, completed, completed, completed]
    assert completion_watcher.campaign_terminal(documents, False, None)
    assert not completion_watcher.campaign_terminal(documents, True, None)
    assert not completion_watcher.campaign_terminal(
        documents, True, {"terminal": False}
    )
    assert completion_watcher.campaign_terminal(
        documents, True, {"terminal": True}
    )


def test_completion_watcher_rejects_live_auxiliary_state():
    completed = {"tasks": {"task": {"state": "completed"}}}
    running = {"tasks": {"task": {"state": "running"}}}
    assert not completion_watcher.campaign_terminal(
        [completed, completed, completed, running],
        True,
        {"terminal": True},
    )


def test_tile_runners_do_not_depend_on_codex_rg_path():
    root = Path(__file__).resolve().parents[2]
    for relative in TILE_RUNNERS:
        assert "rg " not in (root / relative).read_text()


def test_xrage_runner_serializes_same_output_directory():
    root = Path(__file__).resolve().parents[2]
    runner = (root / "benchmarks/spatter/run_xrage_tile_smoke.sh").read_text()
    assert "RUN_LOCK=" in runner
    assert "flock -x 9" in runner
    assert runner.index("flock -x 9") < runner.index(
        "if reuse_completed_run; then"
    )
    assert "65536) echo 64K" in runner


def test_xrage_64k_build_target_is_complete():
    root = Path(__file__).resolve().parents[2]
    executable_cmake = (
        root / "benchmarks/spatter/src/CMakeLists.txt"
    ).read_text()
    library_cmake = (
        root / "benchmarks/spatter/src/Spatter/CMakeLists.txt"
    ).read_text()
    assert "add_executable(spatter_maa_64K" in executable_cmake
    assert "TILE_SIZE=65536" in executable_cmake
    assert "add_library(Spatter_MAA_64K" in library_cmake
    assert "target_link_libraries(Spatter_MAA_64K" in library_cmake
    assert "TILE_SIZE=65536" in library_cmake


def test_xrage_64k_lane_accepts_primary_retry_cgroup():
    root = Path(__file__).resolve().parents[2]
    launcher = (
        root / "experiments/scripts/launch_xrage64_tile_lane.sh"
    ).read_text()
    assert "dx100-full-tile-normal-recovery2-20260721.service" in launcher
    assert (
        "dx100-full-tile-normal-retry-recovery2-20260721.service" in launcher
    )
    assert "--aggregate-memory-max-gib 272" in launcher


def test_cross_page_prefetch_drops_uncacheable_translation():
    root = Path(__file__).resolve().parents[2]
    queued = (root / "src/mem/cache/prefetch/queued.cc").read_text()
    translation = queued[queued.index("Queued::translationComplete") :]
    uncacheable = translation.index("it->translationRequest->isUncacheable()")
    create_packet = translation.index("it->createPkt(")
    assert uncacheable < create_packet


def test_normal_retry_uses_prefetch_fixed_gem5_successor():
    root = Path(__file__).resolve().parents[2]
    launcher = (
        root / "experiments/scripts/launch_normal_tile_retry.sh"
    ).read_text()
    assert "recovery2-normal-retry-workflow-v2.json" in launcher
    assert "build/X86/gem5.opt" in launcher
    assert "recovery2-prefetch-fix-manifest.json" in launcher
    assert "dx100-full-tile-auxiliary-recovery2-20260721.service" in launcher
    assert "dx100-full-tile-t8-surge-recovery2-20260722.service" in launcher
    assert "--aggregate-memory-max-gib 272" in launcher


def test_successor_launchers_accept_normal_retry_cgroup():
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "experiments/scripts/launch_auxiliary_tile_retry.sh",
        "experiments/scripts/launch_t32_surge_tile_lane.sh",
        "experiments/scripts/launch_xrage64_tile_lane.sh",
    ):
        launcher = (root / relative).read_text()
        assert (
            "dx100-full-tile-normal-retry-recovery2-20260721.service"
            in launcher
        )


def test_final_is_recovery_uses_safe_three_way_parallelism():
    root = Path(__file__).resolve().parents[2]
    launcher = (
        root / "experiments/scripts/launch_full_tile_recovery.sh"
    ).read_text()
    assert "--is-parallel 3" in launcher
    assert "--property=MemoryHigh=220G" in launcher
    assert "--property=MemoryMax=240G" in launcher
    assert "--property=MemorySwapMax=0" in launcher


def test_retry_state_repoints_to_successor_workflow(tmp_path):
    state = tmp_path / "state.json"
    workflow = tmp_path / "successor.json"
    state.write_text(
        json.dumps(
            {
                "name": "campaign",
                "file": "/old/workflow.json",
                "tasks": {
                    "done": {"state": "completed"},
                    "dead": {"state": "running"},
                    "failed": {"state": "failed"},
                    "new": {"state": "pending"},
                },
            }
        )
    )
    workflow.write_text(
        json.dumps(
            {
                "name": "campaign",
                "tasks": [
                    {"id": "done"},
                    {"id": "dead"},
                    {"id": "failed"},
                    {"id": "new"},
                ],
            }
        )
    )
    result = normal_recovery.prepare_retry_state(state, workflow.resolve())
    document = json.loads(state.read_text())
    assert document["file"] == str(workflow.resolve())
    assert document["retry_workflow_repointed_from"] == "/old/workflow.json"
    assert result["states"] == {
        "completed": 1,
        "running": 1,
        "failed": 1,
        "pending": 1,
    }


def test_ume_runner_serializes_same_output_directory():
    root = Path(__file__).resolve().parents[2]
    runner = (root / "benchmarks/UME/run_ume_tile_smoke.sh").read_text()
    assert "RUN_LOCK=" in runner
    assert "flock -x 9" in runner
    assert runner.index("flock -x 9") < runner.index(
        "if reuse_completed_run; then"
    )


def test_gapbs_and_cg_runners_serialize_same_output_directory():
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "benchmarks/gapbs/run_gapbs_tile_smoke.sh",
        "benchmarks/NAS/cg/run_cg_tile_smoke.sh",
    ):
        runner = (root / relative).read_text()
        assert "RUN_LOCK=" in runner
        assert "flock -x 9" in runner
        assert runner.index("flock -x 9") < runner.index(
            "if reuse_completed_run; then"
        )
