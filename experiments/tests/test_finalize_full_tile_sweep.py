import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import finalize_full_tile_sweep as finalizer  # noqa: E402


def write_cgroup(path, *, swap=0, high=0, maximum=0, oom=0, oom_kill=0):
    path.write_text(
        "timestamp\tcurrent_bytes\tpeak_bytes\tswap_current_bytes\t"
        "high_events\tmax_events\toom_events\toom_kill_events\n"
        f"t\t1\t2\t{swap}\t{high}\t{maximum}\t{oom}\t{oom_kill}\n"
    )


def safe_telemetry(tmp_path):
    vmstat = tmp_path / "recovery2-vmstat.log"
    vmstat.write_text(
        "1 0 0 1000 0 0 0 0 0 0 0 0 0 0 100 0 0 " "2026-07-21 00:00:00\n"
    )
    records = [{"snapshot": str(vmstat)}]
    for name in (
        "recovery2-normal-cgroup.tsv",
        "recovery2-is-gate-cgroup.tsv",
        "recovery2-full-cgroup.tsv",
    ):
        path = tmp_path / name
        write_cgroup(path)
        records.append({"snapshot": str(path)})
    return records


def test_memory_safety_accepts_required_zero_pressure_telemetry(tmp_path):
    summary = finalizer.memory_safety_summary(safe_telemetry(tmp_path))
    assert summary["safe"]
    assert summary["issues"] == []
    assert summary["vmstat"]["minimum_free_kib"] == 1000


def test_memory_safety_rejects_swap_or_pressure(tmp_path):
    records = safe_telemetry(tmp_path)
    vmstat = tmp_path / "recovery2-vmstat.log"
    vmstat.write_text(
        "1 0 4 1000 0 0 1 2 0 0 0 0 0 0 100 0 0 " "2026-07-21 00:00:00\n"
    )
    normal = tmp_path / "recovery2-normal-cgroup.tsv"
    write_cgroup(normal, high=1)
    summary = finalizer.memory_safety_summary(records)
    assert not summary["safe"]
    assert any("maximum_swap_used_kib=4" in item for item in summary["issues"])
    assert any("maximum_high_events=1" in item for item in summary["issues"])


def test_auxiliary_telemetry_can_be_required(tmp_path):
    records = safe_telemetry(tmp_path)
    summary = finalizer.memory_safety_summary(
        records, {"recovery2-auxiliary-cgroup.tsv"}
    )
    assert not summary["safe"]
    assert any(
        "recovery2-auxiliary-cgroup.tsv" in item for item in summary["issues"]
    )
    auxiliary = tmp_path / "recovery2-auxiliary-cgroup.tsv"
    write_cgroup(auxiliary)
    records.append({"snapshot": str(auxiliary)})
    summary = finalizer.memory_safety_summary(
        records, {"recovery2-auxiliary-cgroup.tsv"}
    )
    assert summary["safe"]


def test_prior_handoff_and_fresh_exact_oracle_are_distinct(tmp_path):
    outdir = tmp_path / "run"
    outdir.mkdir()
    (outdir / "stats.txt").write_text("simTicks 123\n")
    (outdir / "run.log").write_text(
        "UME_OUTPUT_FP output_hash=7 nonfinite=0\n"
        "UME_REFERENCE_PASS volume_errors=0 gradient_errors=0 elements=1\n"
        "Exiting @ tick 456 because m5_exit instruction encountered\n"
    )
    row = {"rc": "0", "simTicks": "123", "outdir": str(outdir)}

    valid, ticks, oracle, notes = finalizer.validate_row(row, None, prior=True)
    assert valid
    assert ticks == 123
    assert oracle == "accepted prior handoff; wrapper rc=0"
    assert notes == []

    valid, _, _, notes = finalizer.validate_row(row, "ume", expected_hash=8)
    assert not valid
    assert "exact UME output fingerprint mismatch (expected 8)" in notes
