import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "experiments/scripts/run_gzp_combined_optimization_matrix.py"


def module():
    spec = importlib.util.spec_from_file_location(
        "gzp_composition_runner", RUNNER
    )
    loaded = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_frozen_fixture(tmp_path: Path, runner):
    root = tmp_path / "frozen"
    config = root / "inputs/configs/deprecated/example/se.py"
    current_config = tmp_path / "current/configs/deprecated/example/se.py"
    guest = root / "inputs/gradzatp_maa_16K_general_soa_jit_fp"
    ramulator = root / "inputs/ramulator.yaml"
    cpt = root / "checkpoint/cpt.1/m5.cpt"
    pmem = cpt.with_name("system.physmem.store0.pmem")
    template = root / "runs/masked_index/restore.command.json"
    separate = root / "runs/separate_predicate/frozen_treatment.txt"
    masked = root / "runs/masked_index/frozen_treatment.txt"
    for path, content in (
        (root / "manifest.json", "{}\n"),
        (config, "# frozen config\n"),
        (current_config, "# current config with current options\n"),
        (guest, "frozen guest\n"),
        (ramulator, "frozen ramulator\n"),
        (cpt, "frozen cpt\n"),
        (pmem, "frozen pmem\n"),
        (separate, "token_stream_ld volume_soa_jit\n"),
        (masked, "token_stream_ld volume_masked_index\n"),
        (
            root / "inputs/treatment.txt",
            "token_stream_ld volume_masked_index\n",
        ),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    command = [
        "/old/gem5.opt",
        "--listener-mode=off",
        "--outdir=" + str(root / "runs/masked_index/gem5"),
        "--debug-flags=MAAVirtualTrace",
        "--debug-file=virtual_trace.log",
        str(config),
        "--cpu-type",
        "X86O3CPU",
        "-r",
        "1",
        "-n",
        "4",
        "--mem-size",
        "2GB",
        "--ramulator-config",
        str(ramulator),
        "--checkpoint-dir=" + str(root / "checkpoint"),
        "--maa",
        "--maa_num_tile_elements=16384",
        "--maa_physical_tile_elements=4096",
        "--maa_soa_jit_value_cache_enable",
        "--maa_soa_jit_active_contexts=8",
        "--maa_soa_jit_active_value_owners=32",
        "--cmd",
        str(guest),
        "--options",
        "1000000 " + str(root / "inputs/treatment.txt"),
    ]
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(json.dumps(command))
    runner.FROZEN_ROOT = root
    runner.CURRENT_CONFIG = current_config
    runner.EXPECTED_FROZEN_MANIFEST_SHA256 = digest(root / "manifest.json")
    runner.EXPECTED_TEMPLATE_SHA256 = digest(template)
    runner.EXPECTED_CONFIG_SHA256 = digest(config)
    runner.EXPECTED_GUEST_SHA256 = digest(guest)
    runner.EXPECTED_RAMULATOR_CONFIG_SHA256 = digest(ramulator)
    runner.EXPECTED_CPT_SHA256 = digest(cpt)
    runner.EXPECTED_PMEM_SHA256 = digest(pmem)
    return root


def write_fake_gem5(path: Path):
    path.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
out = pathlib.Path(next(item.split('=', 1)[1] for item in args if item.startswith('--outdir=')))
owners = int(next(item.split('=', 1)[1] for item in args if item.startswith('--maa_soa_jit_active_value_owners=')))
options = args[args.index('--options') + 1]
selector = pathlib.Path(options.split(' ', 1)[1]).read_text().strip()
masked = selector.endswith('volume_masked_index')
pre_a = '--maa_soa_jit_pre_a_value_lookahead' in args
if not masked:
    ticks = 1000
elif not pre_a:
    ticks = 900
elif owners == 32:
    ticks = 950
elif owners == 64:
    ticks = 890
else:
    ticks = 800
out.mkdir(parents=True)
selected_total = 949411
rejected_total = 50013
events = []
totals = {key: 0 for key in ('selected', 'rejected', 'predicate', 'a', 'value', 'lookahead', 'preissue', 'preready', 'preuses')}
for generation in range(61):
    selected = 15564 if generation < 60 else selected_total - 15564 * 60
    rejected = 16384 - selected
    a = selected // 2
    predicate = 0 if masked else 1025
    preissue = selected if pre_a else 0
    preready = selected - 1 if pre_a else 0
    event = (
        '0: global: event=soa_jit_complete schema=2 unit=0 generation=%d logical=16384 '
        'selected=%d predicate_rejected=%d predicate_mode=%s '
        'masked_index_compare_bits=%d masked_index_mode_state_bits=%d masked_index_additional_buffer_bytes=0 '
        'predicate_lines=%d/%d predicate_uses=%d a_reads=%d/%d value_reads=%d/%d fills=%d cached=%d '
        'deliveries=%d aliases=%d lookahead=%d/%d pre_a_enable=%d pre_a=%d/%d/%d '
        'a_writes=%d/%d active_value_owners=%d max_value_owners=128 terminal=1\\n'
        % (generation + 1, selected, rejected, 'masked_index' if masked else 'separate_array',
           32 if masked else 0, 1 if masked else 0, predicate, predicate,
           0 if masked else 16384, a, a, selected, selected, selected, selected,
           selected, selected, selected, selected, 1 if pre_a else 0, preissue,
           preready, preissue, a, a, owners)
    )
    events.append(event)
    for key, value in (('selected', selected), ('rejected', rejected), ('predicate', predicate), ('a', a), ('value', selected), ('lookahead', selected), ('preissue', preissue), ('preready', preready), ('preuses', preissue)):
        totals[key] += value
(out / 'virtual_trace.log').write_text(''.join(events))
stats = {
    'simTicks': ticks,
    'IND_SoaJitSelected': totals['selected'],
    'IND_SoaJitPredicateRejected': totals['rejected'],
    'IND_SoaJitPredicateLineReads': totals['predicate'],
    'IND_SoaJitPredicateLineResponses': totals['predicate'],
    'IND_SoaJitAReadIssues': totals['a'],
    'IND_SoaJitAReadResponses': totals['a'],
    'IND_SoaJitValueReadIssues': totals['value'],
    'IND_SoaJitValueReadResponses': totals['value'],
    'IND_SoaJitValueFills': totals['value'],
    'IND_SoaJitValueCachedResponses': totals['value'],
    'IND_SoaJitValueDeliveries': totals['value'],
    'IND_SoaJitLookaheadIssues': totals['lookahead'],
    'IND_SoaJitLookaheadResponses': totals['lookahead'],
    'IND_SoaJitPreAValueIssues': totals['preissue'],
    'IND_SoaJitPreAValueReadyAtAResponse': totals['preready'],
    'IND_SoaJitPreAValueUses': totals['preuses'],
    'IND_SoaJitAliasesApplied': totals['value'],
    'IND_SoaJitAWriteIssues': totals['a'],
    'IND_SoaJitAWriteResponses': totals['a'],
    'IND_SoaJitTerminalCompletions': 61,
    'IND_SoaJitActiveValueOwners': owners * 61,
}
(out / 'stats.txt').write_text('---------- Begin Simulation Statistics ----------\\n' + ''.join('system.maa.I0_%s %d\\n' % pair for pair in stats.items()) + '---------- End Simulation Statistics   ----------\\n')
if masked:
    print('UME_GZP_TERMINAL treatment=volume_masked_index_soa_jit full_windows=0 volume_only_windows=0 masked_index_windows=61 published_predicates=0 published_gradient_values=0 predicate_hash=10865783785176355512 ledger_selected=949959 ledger_rejected=50041 ledger_full_selected=949411 ledger_full_rejected=50013 active_uint32_max=0 active_illegal_index=0 inactive_legal_index=0 inactive_non_sentinel=0 index_hash=15605778284598092602 publisher=masked_index_no_predicate_publication predicate_publications=0 predicate_publication_bytes=0 performance_promotable=1 result=PASS')
else:
    print('UME_GZP_TERMINAL treatment=volume_only_soa_jit full_windows=0 volume_only_windows=61 masked_index_windows=0 published_predicates=0 published_gradient_values=0 predicate_hash=10865783785176355512 ledger_selected=949959 ledger_rejected=50041 ledger_full_selected=949411 ledger_full_rejected=50013 active_uint32_max=0 active_illegal_index=0 inactive_legal_index=0 inactive_non_sentinel=0 index_hash=15605778284598092602 publisher=precheckpoint_uint32_predicate predicate_publications=1 predicate_publication_bytes=4000000 performance_promotable=1 result=PASS')
print('UME_OUTPUT_FP output_hash=11225737641199706160 nonfinite=0')
print('UME_REFERENCE_PASS point_volume_errors=0 point_gradient_errors=0 elements=1180000')
print('UME_GZP_MASKED_INDEX_LEDGER result=PASS selected=949959 rejected=50041 full_selected=949411 full_rejected=50013 active_uint32_max=0 active_illegal_index=0 inactive_legal_index=0 inactive_non_sentinel=0 index_hash=15605778284598092602 exact_equivalence=1')
print('Exiting @ tick %d because m5_exit instruction encountered' % ticks)
"""
    )
    path.chmod(0o755)


def test_plan_is_restore_only_with_the_fixed_six_run_shape(tmp_path: Path):
    runner = module()
    write_frozen_fixture(tmp_path, runner)
    base = runner.template()
    args = type("Args", (), {"gem5": tmp_path / "gem5.opt"})()
    plan = runner.campaign_plan(args, base)
    assert plan["parallel_restores"] == 6
    assert plan["timeout_seconds"] is None
    assert plan["simulated_metric"] == "simTicks"
    assert plan["host_time_metric_authorized"] is False
    assert plan["fixed_active_contexts"] == 32
    assert [
        (arm["owners"], arm["pre_a"], arm["replicas"]) for arm in plan["arms"]
    ] == [
        (32, False, ["replica-1", "replica-2"]),
        (32, True, ["replica-1"]),
        (64, True, ["replica-1"]),
        (128, True, ["replica-1", "replica-2"]),
    ]


def test_materialized_arms_only_change_selector_owner_pre_a_and_outdir(
    tmp_path: Path,
):
    runner = module()
    write_frozen_fixture(tmp_path, runner)
    base = runner.template()
    selectors = []
    commands = []
    for spec in runner.run_specs():
        selector = tmp_path / (spec["name"] + ".txt")
        selector.write_text(spec["selector"] + "\n")
        selectors.append(selector)
        commands.append(
            runner.materialize_command(
                base,
                tmp_path / "gem5.opt",
                tmp_path / spec["name"],
                selector,
                spec,
            )
        )
    assert len(commands) == 6
    assert (
        len(
            {tuple(runner.normalized_command(command)) for command in commands}
        )
        == 1
    )
    assert all(
        runner.command_value(command, "--checkpoint-dir")
        == str(runner.frozen_paths()["checkpoint"])
        for command in commands
    )
    assert {
        runner.command_value(command, "--maa_soa_jit_active_contexts")
        for command in commands
    } == {"32"}
    assert all(str(runner.CURRENT_CONFIG) in command for command in commands)
    assert all(
        str(runner.frozen_paths()["config"]) not in command
        for command in commands
    )
    assert (
        sum(
            "--maa_soa_jit_pre_a_value_lookahead" in command
            for command in commands
        )
        == 4
    )


def test_fake_execution_emits_manifest_matrix_and_simticks_only_decision(
    tmp_path: Path, monkeypatch
):
    runner = module()
    write_frozen_fixture(tmp_path, runner)
    fake = tmp_path / "gem5.opt"
    write_fake_gem5(fake)
    expected = digest(fake)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runner",
            "--gem5",
            str(fake),
            "--outdir",
            str(tmp_path / "out"),
            "--execute",
            "--expected-gem5-sha256",
            expected,
        ],
    )
    assert runner.main() == 0
    out = tmp_path / "out"
    matrix = json.loads((out / "matrix.json").read_text())
    manifest = json.loads((out / "manifest.json").read_text())
    decision = json.loads((out / "decision.json").read_text())
    assert len(matrix["rows"]) == 6
    assert len(manifest["runs"]) == 6
    assert decision["decision"] == "PROMOTE"
    assert decision["selected_arm"] == "masked-owner128-pre-a-on"
    assert decision["selected_simTicks"] == 800
    assert decision["sweep_endpoint"] == "masked-owner128-pre-a-on"
    assert decision["promotion_metric"] == "simTicks"
    assert decision["host_time_metric_authorized"] is False
    assert len(decision["adjacent_deltas"]) == 3
    assert decision["adjacent_deltas"][0]["improves"] is False
    assert {row["predicate_lines_issue"] for row in matrix["rows"]} == {0}
    assert {row["predicate_mode"] for row in matrix["rows"]} == {
        "masked_index"
    }
    assert all(
        row["pre_a_issue"] == 0 for row in matrix["rows"] if not row["pre_a"]
    )
    assert all(
        row["pre_a_issue"] > 0 for row in matrix["rows"] if row["pre_a"]
    )
