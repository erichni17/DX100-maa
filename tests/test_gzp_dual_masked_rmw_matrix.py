import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "experiments/scripts/run_gzp_dual_masked_rmw_matrix.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("gzp_dual_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_is_repeated_full_exact_and_provenance_gated(
    tmp_path: Path,
) -> None:
    placeholders = []
    for name in ("gem5", "ramulator", "native16", "native4"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        placeholders.append(path)
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--out",
            str(tmp_path / "out"),
            "--gem5",
            str(placeholders[0]),
            "--ramulator-library",
            str(placeholders[1]),
            "--native16",
            str(placeholders[2]),
            "--native4",
            str(placeholders[3]),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    plan = json.loads(result.stdout)
    assert plan["n"] == 1_000_000
    assert plan["replicas"] == 3
    assert plan["timeout_seconds"] == 0
    assert plan["shared_hybrid_checkpoint"] is True
    assert plan["simulated_metric"] == "simTicks"
    assert plan["host_time_metric_authorized"] is False
    assert [arm["name"] for arm in plan["arms"]] == [
        "native16",
        "native4",
        "volume_masked_index_owner64_pre_a_context64",
        "dual_masked_index_owner64_pre_a_context64",
    ]
    assert plan["fixed_hybrid_controls"] == {
        "logical_elements": 16384,
        "physical_payload_elements": 4096,
        "active_value_owners": 64,
        "pre_a_value_lookahead": True,
        "active_contexts": 64,
    }


def test_runner_binds_exact_hardware_and_completion_ledgers() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for contract in (
        '"publisher_payload_bytes_per_instance": "512"',
        '"publisher_control_bytes_per_instance": "408"',
        '"publisher_total_bytes_per_instance": "920"',
        '"persistent_payload_bytes": "2048"',
        '"persistent_control_bytes": "1632"',
        '"persistent_total_bytes": "3680"',
        'stat_sum(stats, "STR_PublishIssues") != PUBLISH_LINES',
        'stat_sum(stats, "STR_PublishWriteResponses") != PUBLISH_LINES',
        'stat_sum(stats, "STR_PublishTerminals") != GRADIENT_PAGES',
        '"IND_SoaJitTerminalCompletions"',
        '"IND_SoaJitPredicateLineReads"',
        '"IND_SoaJitPreAValueUses"',
        '"provenance_permits_native_reference_comparison": True',
    ):
        assert contract in source


def publisher_trace(module, responses: int = 256) -> str:
    lines = []
    for window in range(module.FULL_WINDOWS):
        for page in range(4):
            lines.append(
                "0: event=spd_publish_terminal terminal=1 "
                f"logical_page={page} logical_offset={page * 4096} "
                f"generation={window * 8 + page * 2 + 2} "
                f"issues=256 responses={responses} credit_hwm=8"
            )
    return "\n".join(lines) + "\n"


def test_publisher_trace_requires_every_exact_writeresp(
    tmp_path: Path,
) -> None:
    module = load_runner()
    trace = tmp_path / "trace.log"
    trace.write_text(publisher_trace(module), encoding="utf-8")
    assert module.analyze_publisher_trace(trace) == {
        "publisher_terminals": 244,
        "publisher_lines": 62464,
    }

    trace.write_text(publisher_trace(module, responses=255), encoding="utf-8")
    with pytest.raises(RuntimeError, match="page/order/response"):
        module.analyze_publisher_trace(trace)


def test_soa_trace_requires_masked_owner_pre_a_context64(
    tmp_path: Path,
) -> None:
    module = load_runner()
    trace = tmp_path / "trace.log"
    event = (
        "0: event=soa_jit_complete terminal=1 predicate_mode=masked_index "
        "pre_a_enable=1 active_value_owners=64 active_contexts=64 "
        "masked_index_compare_bits=32 masked_index_additional_buffer_bytes=0 "
        "selected=7 predicate_rejected=16377"
    )
    trace.write_text("\n".join([event] * 2) + "\n", encoding="utf-8")
    assert module.analyze_soa_trace(trace, 2) == {
        "selected": 14,
        "rejected": 32754,
    }

    trace.write_text(
        (event.replace("active_contexts=64", "active_contexts=32") + "\n"),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="accepted-control"):
        module.analyze_soa_trace(trace, 1)
