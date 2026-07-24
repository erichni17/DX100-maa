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
    assert summary["warnings"] == []
    assert summary["vmstat"]["minimum_free_kib"] == 1000


def test_memory_safety_rejects_swap_but_reports_soft_high_pressure(tmp_path):
    records = safe_telemetry(tmp_path)
    vmstat = tmp_path / "recovery2-vmstat.log"
    vmstat.write_text(
        "1 0 4 1000 0 0 1 2 0 0 0 0 0 0 100 0 0 " "2026-07-21 00:00:00\n"
    )
    normal = tmp_path / "recovery2-normal-cgroup.tsv"
    write_cgroup(normal, high=1)
    summary = finalizer.memory_safety_summary(records)
    assert not summary["safe"]
    assert any(
        "maximum_swap_in_kib_per_second=1" in item
        for item in summary["issues"]
    )
    assert any(
        "maximum_swap_out_kib_per_second=2" in item
        for item in summary["issues"]
    )
    assert any(
        "maximum_swap_used_kib=4" in item for item in summary["warnings"]
    )
    assert not any(
        "maximum_high_events=1" in item for item in summary["issues"]
    )
    assert any("maximum_high_events=1" in item for item in summary["warnings"])


def test_memory_safety_warns_on_stable_host_swap_occupancy(tmp_path):
    records = safe_telemetry(tmp_path)
    vmstat = tmp_path / "recovery2-vmstat.log"
    vmstat.write_text(
        "1 0 4 1000 0 0 0 0 0 0 0 0 0 0 100 0 0 "
        "2026-07-21 00:00:00\n"
    )
    summary = finalizer.memory_safety_summary(records)
    assert summary["safe"]
    assert summary["issues"] == []
    assert any(
        "maximum_swap_used_kib=4" in item for item in summary["warnings"]
    )


def test_memory_safety_can_select_post_containment_epoch(tmp_path):
    records = safe_telemetry(tmp_path)
    recovery5 = tmp_path / "recovery5-vmstat.log"
    recovery5.write_text(
        "1 0 0 1000 0 0 0 0 0 0 0 0 0 0 100 0 0 "
        "2026-07-23 00:00:00\n"
    )
    app_slice = tmp_path / "recovery5-app-slice-cgroup.tsv"
    write_cgroup(app_slice)
    records.extend(
        [{"snapshot": str(recovery5)}, {"snapshot": str(app_slice)}]
    )
    summary = finalizer.memory_safety_summary(
        records,
        vmstat_name="recovery5-vmstat.log",
        base_required_cgroups={"recovery5-app-slice-cgroup.tsv"},
        baseline_cgroups={"recovery5-app-slice-cgroup.tsv"},
    )
    assert summary["safe"]
    assert summary["vmstat_source"] == "recovery5-vmstat.log"
    assert summary["required_cgroup_telemetry"] == [
        "recovery5-app-slice-cgroup.tsv"
    ]


def test_post_containment_vmstat_skips_since_boot_first_sample(tmp_path):
    records = safe_telemetry(tmp_path)
    recovery5 = tmp_path / "recovery5-vmstat.log"
    recovery5.write_text(
        "1 0 4 1000 0 0 188 9 0 0 0 0 0 0 100 0 0 "
        "2026-07-23 00:00:00\n"
        "1 0 4 900 0 0 0 0 0 0 0 0 0 0 100 0 0 "
        "2026-07-23 00:00:01\n"
    )
    app_slice = tmp_path / "recovery5-app-slice-cgroup.tsv"
    write_cgroup(app_slice, swap=4096, oom=2, oom_kill=1)
    records.extend(
        [{"snapshot": str(recovery5)}, {"snapshot": str(app_slice)}]
    )
    summary = finalizer.memory_safety_summary(
        records,
        vmstat_name="recovery5-vmstat.log",
        vmstat_skip_first_sample=True,
        base_required_cgroups={"recovery5-app-slice-cgroup.tsv"},
        baseline_cgroups={"recovery5-app-slice-cgroup.tsv"},
    )
    assert summary["safe"]
    assert summary["vmstat"]["sample_count"] == 1
    assert summary["vmstat"]["maximum_swap_in_kib_per_second"] == 0
    assert summary["vmstat"]["maximum_swap_out_kib_per_second"] == 0


def test_post_containment_vmstat_requires_quiet_tail(tmp_path):
    records = safe_telemetry(tmp_path)
    recovery5 = tmp_path / "recovery5-vmstat.log"
    recovery5.write_text(
        "1 0 4 1000 0 0 12 0 0 0 0 0 0 0 100 0 0\n"
        "1 0 4 900 0 0 0 0 0 0 0 0 0 0 100 0 0\n"
        "1 0 4 800 0 0 0 0 0 0 0 0 0 0 100 0 0\n"
        "1 0 4 700 0 0 0 0 0 0 0 0 0 0 100 0 0\n"
    )
    app_slice = tmp_path / "recovery5-app-slice-cgroup.tsv"
    write_cgroup(app_slice)
    records.extend(
        [{"snapshot": str(recovery5)}, {"snapshot": str(app_slice)}]
    )
    summary = finalizer.memory_safety_summary(
        records,
        vmstat_name="recovery5-vmstat.log",
        vmstat_minimum_quiet_samples=3,
        base_required_cgroups={"recovery5-app-slice-cgroup.tsv"},
        baseline_cgroups={"recovery5-app-slice-cgroup.tsv"},
    )
    assert summary["safe"]
    assert summary["vmstat"]["swap_activity_sample_count"] == 1
    assert summary["vmstat"][
        "latest_consecutive_zero_swap_samples"
    ] == 3
    assert any(
        "1 historical swap-activity samples" in warning
        for warning in summary["warnings"]
    )

    recovery5.write_text(
        recovery5.read_text()
        + "1 0 4 600 0 0 0 1 0 0 0 0 0 0 100 0 0\n"
    )
    summary = finalizer.memory_safety_summary(
        records,
        vmstat_name="recovery5-vmstat.log",
        vmstat_minimum_quiet_samples=3,
        base_required_cgroups={"recovery5-app-slice-cgroup.tsv"},
        baseline_cgroups={"recovery5-app-slice-cgroup.tsv"},
    )
    assert not summary["safe"]
    assert any(
        "only 0 consecutive zero-swap samples" in issue
        for issue in summary["issues"]
    )


def test_post_containment_cgroup_gates_growth_not_baseline(tmp_path):
    records = safe_telemetry(tmp_path)
    app_slice = tmp_path / "recovery5-app-slice-cgroup.tsv"
    app_slice.write_text(
        "timestamp\tcurrent_bytes\tpeak_bytes\tswap_current_bytes\t"
        "high_events\tmax_events\toom_events\toom_kill_events\n"
        "t0\t1\t2\t4096\t10\t3\t2\t1\n"
        "t1\t1\t2\t4096\t11\t3\t2\t1\n"
    )
    records.append({"snapshot": str(app_slice)})
    summary = finalizer.memory_safety_summary(
        records,
        base_required_cgroups={"recovery5-app-slice-cgroup.tsv"},
        baseline_cgroups={"recovery5-app-slice-cgroup.tsv"},
    )
    assert summary["safe"]
    assert summary["cgroups"][app_slice.name][
        "maximum_delta_oom_kill_events"
    ] == 0
    assert any(
        "first_swap_current_bytes=4096" in item
        for item in summary["warnings"]
    )

    app_slice.write_text(app_slice.read_text() + "t2\t1\t2\t8192\t11\t3\t2\t2\n")
    summary = finalizer.memory_safety_summary(
        records,
        base_required_cgroups={"recovery5-app-slice-cgroup.tsv"},
        baseline_cgroups={"recovery5-app-slice-cgroup.tsv"},
    )
    assert not summary["safe"]
    assert any(
        "maximum_swap_growth_bytes=4096" in item
        for item in summary["issues"]
    )
    assert any(
        "maximum_delta_oom_kill_events=1" in item
        for item in summary["issues"]
    )


def test_memory_safety_rejects_hard_limit_or_oom_events(tmp_path):
    records = safe_telemetry(tmp_path)
    normal = tmp_path / "recovery2-normal-cgroup.tsv"
    write_cgroup(normal, maximum=2, oom=1, oom_kill=1)
    summary = finalizer.memory_safety_summary(records)
    assert not summary["safe"]
    assert any("maximum_max_events=2" in item for item in summary["issues"])
    assert any("maximum_oom_events=1" in item for item in summary["issues"])
    assert any(
        "maximum_oom_kill_events=1" in item for item in summary["issues"]
    )


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


def test_bfs_oracle_uses_exact_output_depth_not_schedule_frontiers(tmp_path):
    expected = finalizer.BFS_DEPTH_ORACLE
    oracle_ids = []
    for index, frontier in enumerate(("reached=4194304", "reached=4194244")):
        outdir = tmp_path / f"bfs-{index}"
        outdir.mkdir()
        (outdir / "stats.txt").write_text("simTicks 123\n")
        (outdir / "run.log").write_text(
            f"BFS_FP levels=7 {frontier} frontier_hash={index} {expected}\n"
            "Exiting @ tick 456 because m5_exit instruction encountered\n"
        )
        row = {"rc": "0", "simTicks": "123", "outdir": str(outdir)}
        valid, _, oracle, notes = finalizer.validate_row(row, "bfs")
        assert valid, notes
        oracle_ids.append(oracle)
    assert oracle_ids == [expected, expected]


def test_xrage_oracle_ignores_randomized_output_hash(tmp_path):
    oracle_ids = []
    for run, hash_offset in enumerate((100, 900)):
        outdir = tmp_path / f"xrage-{run}"
        outdir.mkdir()
        (outdir / "stats.txt").write_text("simTicks 123\n")
        markers = [
            "SPATTER_FP "
            f"config={config} kernel=scatter elements=8 checked=8 "
            f"ambiguous=0 mismatches=0 hash={hash_offset + config}"
            for config in range(9)
        ]
        (outdir / "run.log").write_text(
            "\n".join(markers)
            + "\nExiting @ tick 456 because m5_exit instruction encountered\n"
        )
        row = {"rc": "0", "simTicks": "123", "outdir": str(outdir)}
        valid, _, oracle, notes = finalizer.validate_row(row, "xrage")
        assert valid, notes
        oracle_ids.append(oracle)
    assert oracle_ids[0] == oracle_ids[1]


def test_is_tiles_use_numa_safe_recovery4_workflows(tmp_path):
    workload_specs = finalizer.specs(
        tmp_path,
        tmp_path / "gapbs.tsv",
        [tmp_path / "hashjoin.tsv"],
    )
    is_spec = next(
        item for item in workload_specs if item["id"] == "nas-is-full"
    )
    assert is_spec["workflow_by_tile"] == {
        1024: "recovery_is_node1_low",
        2048: "recovery_is_node1_mid",
        4096: "recovery_is_node1_mid",
        8192: "recovery_is_node1_low",
        16384: "recovery_is_gate",
        32768: "recovery_is_node1_high",
        65536: "recovery_is_node1_high",
    }


def test_scan_log_cache_is_stat_bound_and_invalidated(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(
        "IS_ROI_EXIT_POLICY dump_stats_verify_m5_exit\n"
        "IS_VERIFY keys=1 result=PASS\n"
        "Exiting @ tick 1 because m5_exit instruction encountered\n"
    )
    first = finalizer.scan_log(log, "is")
    assert first["m5_exit"]
    assert first["is_exit_policy"]
    cache = tmp_path / ".run.log.scan-v1.json"
    assert cache.is_file()

    log.write_text(log.read_text() + "fatal: synthetic failure\n")
    second = finalizer.scan_log(log, "is")
    assert second["panic_or_fatal"]


def test_task_workflow_uses_overlay_only_when_task_is_present():
    spec = {
        "workflow": "normal",
        "workflow_by_tile": {16384: "gate"},
        "workflow_overlays": ["repair"],
    }
    states = {
        "repair": {
            "tasks": {
                "gapbs-bfs-t1024": {"state": "running"},
            }
        }
    }
    assert (
        finalizer.task_workflow(
            states, spec, 1024, "gapbs-bfs-t1024"
        )
        == "repair"
    )
    assert (
        finalizer.task_workflow(
            states, spec, 2048, "gapbs-bfs-t2048"
        )
        == "normal"
    )
    assert (
        finalizer.task_workflow(
            states, spec, 16384, "gapbs-bfs-t16384"
        )
        == "gate"
    )


def test_t32_supersession_requires_exact_workflow_and_owners(tmp_path):
    workflow = tmp_path / "t32.json"
    workflow.write_text(
        __import__("json").dumps(
            {
                "tasks": [
                    {"id": task_id}
                    for task_id in finalizer.T32_SUPERSESSION_OWNERS
                ]
            }
        )
    )
    record = {
        "schema_version": 1,
        "decision": "superseded-with-exact-owners",
        "task_owners": finalizer.T32_SUPERSESSION_OWNERS,
        "superseded_workflow": str(workflow),
        "superseded_workflow_sha256": finalizer.sha256(workflow),
    }
    assert finalizer.valid_t32_supersession(record, workflow)
    record["task_owners"] = {}
    assert not finalizer.valid_t32_supersession(record, workflow)
