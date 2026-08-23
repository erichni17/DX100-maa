"""Contract checks for the exact matched GZP dual-capacity matrix."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "experiments/scripts/run_gzp_dual_capacity_matrix.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("gzp_dual_capacity", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_is_exact_two_arm_three_replica_capacity_attribution(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--out", str(tmp_path / "out")],
        check=True,
        capture_output=True,
        text=True,
    )
    plan = json.loads(result.stdout)
    assert plan["arms"] == ["logical16_physical16", "logical16_physical4"]
    assert plan["replicas"] == 3
    assert plan["max_parallel_restores"] == 6
    assert plan["timeout_seconds"] == 0
    assert plan["shared_checkpoint"] is True
    assert plan["shared_guest"] is True
    assert plan["shared_selector"] == "token_stream_ld dual_masked_index"
    assert plan["debug_flags"] == "MAAVirtualTrace,MAATrace"
    assert plan["performance_metric"] == "first ROI simTicks"
    assert plan["physical_tile_payload_bytes"] == {
        "logical16_physical16": 2_097_152,
        "logical16_physical4": 524_288,
        "delta": 1_572_864,
    }
    assert plan["publisher_bytes_separate"] == 920
    assert plan["coherent_backing_bytes_separate"] == 262_144


def test_commands_differ_only_in_outdir_and_physical_capacity(
    tmp_path: Path,
) -> None:
    module = load_runner()
    common = {
        "gem5": tmp_path / "gem5",
        "config": tmp_path / "se.py",
        "checkpoint": tmp_path / "checkpoint",
        "guest": tmp_path / "guest",
        "selector": tmp_path / "selector",
        "ramulator": tmp_path / "ramulator.yaml",
    }
    command16 = module.make_restore_command(
        common["gem5"],
        common["config"],
        tmp_path / "physical16",
        common["checkpoint"],
        common["guest"],
        common["selector"],
        common["ramulator"],
        "native16",
    )
    command4 = module.make_restore_command(
        common["gem5"],
        common["config"],
        tmp_path / "physical4",
        common["checkpoint"],
        common["guest"],
        common["selector"],
        common["ramulator"],
        "hybrid",
    )
    assert module.normalized_command(command16) == module.normalized_command(
        command4
    )
    delta = module.command_delta(command16, command4)
    assert len(delta) == 2
    assert {
        "left": "--maa_physical_tile_elements=16384",
        "right": "--maa_physical_tile_elements=4096",
    } in delta
    assert command16.count("--debug-flags=MAAVirtualTrace,MAATrace") == 1
    assert command4.count("--debug-flags=MAAVirtualTrace,MAATrace") == 1


def test_analyzer_requires_exact_work_output_and_hardware_boundaries() -> None:
    module = load_runner()
    assert module.GEM5_SHA256.startswith("44b6e86e")
    assert module.GUEST_SHA256.startswith("79f80081")
    assert module.CHECKPOINT_SHA256.startswith("35fd8fb2")
    source = RUNNER.read_text(encoding="utf-8")
    for contract in (
        "EXPECTED_SOA_TERMINALS = FULL_WINDOWS * 2",
        'stat_sum(stats, "STR_PublishIssues") != PUBLISH_LINES',
        'stat_sum(stats, "STR_PublishAccepts") != PUBLISH_LINES',
        'stat_sum(stats, "STR_PublishWriteResponses") != PUBLISH_LINES',
        '"publisher_source_order_sha256"',
        '"soa_source_order_sha256"',
        '"physical_spd_payload_delta_bytes"',
        '"publisher_bytes_separate": PUBLISHER_BYTES',
        '"coherent_backing_bytes_separate": COHERENT_BACKING_BYTES',
    ):
        assert contract in source
