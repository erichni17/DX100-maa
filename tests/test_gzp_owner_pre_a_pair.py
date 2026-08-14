import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "experiments/scripts/run_gzp_owner_pre_a_pair.py"


def module():
    spec = importlib.util.spec_from_file_location("gzp_owner_pair", RUNNER)
    loaded = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def test_plan_binds_the_frozen_full_pre_a_pair():
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--gem5",
            "/tmp/gem5.opt",
            "--outdir",
            "/tmp/gzp-owner-pair",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    plan = json.loads(result.stdout)
    assert plan["elements"] == 1_000_000
    assert plan["replicas_per_arm"] == 2
    assert plan["parallel_restores"] == 4
    assert plan["timeout_seconds"] is None
    assert plan["pre_a_enabled"] is True
    assert (
        plan["treatment_delta"]
        == "maa_soa_jit_active_value_owners=32 versus 64 only"
    )
    assert plan["arms"] == [
        {"name": "owners-32", "maa_soa_jit_active_value_owners": 32},
        {"name": "owners-64", "maa_soa_jit_active_value_owners": 64},
    ]


def test_materialized_commands_only_vary_by_owner_and_outdir(tmp_path: Path):
    runner = module()
    base = runner.template()
    c32 = runner.materialize_command(
        base, tmp_path / "gem5", tmp_path / "a", 32
    )
    c64 = runner.materialize_command(
        base, tmp_path / "gem5", tmp_path / "b", 64
    )
    assert runner.normalized_command(c32) == runner.normalized_command(c64)
    assert "--maa_soa_jit_pre_a_value_lookahead" in c32
    assert runner.command_option(c32, "--checkpoint-dir") == str(
        runner.CHECKPOINT
    )
    assert (
        runner.command_option(c64, "--maa_soa_jit_active_value_owners") == "64"
    )


def write_fake_gem5(path: Path):
    path.write_text(
        """#!/usr/bin/env python3
import pathlib, sys
out = pathlib.Path(next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--outdir=')))
owners = int(next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--maa_soa_jit_active_value_owners=')))
out.mkdir(parents=True)
selected = 10
event = ('0: global: event=soa_jit_complete schema=2 unit=0 generation=1 logical=16384 selected=10 predicate_rejected=16374 predicate_mode=separate_array a_reads=7/7 value_reads=9/9 fills=9 cached=9 deliveries=10 aliases=10 lookahead=10/10 pre_a_enable=1 pre_a=8/5/8 a_writes=7/7 evictions=%d value_stalls=20 stalls=30 active_value_owners=%d max_value_owners=128 terminal=1\\n' % (10 if owners == 32 else 5, owners))
trace = event * 61
(out / 'virtual_trace.log').write_text(trace)
stats = {'simTicks': 123456 if owners == 32 else 120000, 'IND_SoaJitSelected': 610, 'IND_SoaJitPredicateRejected': 998814, 'IND_SoaJitAReadIssues': 427, 'IND_SoaJitAReadResponses': 427, 'IND_SoaJitValueReadIssues': 549, 'IND_SoaJitValueReadResponses': 549, 'IND_SoaJitValueFills': 549, 'IND_SoaJitValueCachedResponses': 549, 'IND_SoaJitValueDeliveries': 610, 'IND_SoaJitLookaheadIssues': 610, 'IND_SoaJitLookaheadResponses': 610, 'IND_SoaJitPreAValueIssues': 488, 'IND_SoaJitPreAValueReadyAtAResponse': 305, 'IND_SoaJitPreAValueUses': 488, 'IND_SoaJitAliasesApplied': 610, 'IND_SoaJitAWriteIssues': 427, 'IND_SoaJitAWriteResponses': 427, 'IND_SoaJitValueEvictions': (10 if owners == 32 else 5) * 61, 'IND_SoaJitValueStalls': 1220, 'IND_SoaJitContextStalls': 1830, 'IND_SoaJitValueCacheHighWater': owners * 61, 'IND_SoaJitTerminalCompletions': 61, 'IND_SoaJitActiveValueOwners': owners * 61}
(out / 'stats.txt').write_text('---------- Begin Simulation Statistics ----------\\n' + ''.join('system.maa.I0_%s %d\\n' % pair for pair in stats.items()) + '---------- End Simulation Statistics   ----------\\n')
print('UME_OUTPUT_FP output_hash=11225737641199706160 nonfinite=0')
print('UME_REFERENCE_PASS point_volume_errors=0 point_gradient_errors=0 elements=1180000')
print('UME_GZP_TERMINAL treatment=volume_only_soa_jit full_windows=0 volume_only_windows=61 published_predicates=0 published_gradient_values=0 result=PASS')
print('Exiting @ tick 123 because m5_exit instruction encountered')
"""
    )
    path.chmod(0o755)


def test_execute_emits_exact_matrix_manifest_and_decision(
    tmp_path: Path, monkeypatch
):
    runner = module()
    fake = tmp_path / "gem5.opt"
    write_fake_gem5(fake)
    # The execute gate also pins frozen identity.  Replace only the module
    # constants in this isolated process-level test; the production defaults
    # remain the accepted artifact hashes.
    digest = hashlib.sha256(fake.read_bytes()).hexdigest()
    original_sha256 = runner.sha256

    def isolated_identity(path: Path) -> str:
        if path == fake:
            return digest
        if path == runner.frozen_config(runner.template()):
            return runner.EXPECTED_CONFIG_SHA256
        if path == runner.FROZEN_SOURCE / "inputs/hybrid":
            return runner.EXPECTED_GUEST_SHA256
        if path == runner.RAMULATOR:
            return runner.EXPECTED_RAMULATOR_SHA256
        return original_sha256(path)

    # Do not hash the 169 MiB frozen Ramulator library in the focused fake
    # execution test; production execution still hashes every frozen input.
    monkeypatch.setattr(runner, "sha256", isolated_identity)
    argv = [
        "runner",
        "--gem5",
        str(fake),
        "--outdir",
        str(tmp_path / "out"),
        "--execute",
        "--expected-gem5-sha256",
        digest,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert runner.main() == 0
    out = tmp_path / "out"
    assert (
        json.loads((out / "decision.json").read_text())["decision"]
        == "PROMOTE"
    )
    matrix = json.loads((out / "matrix.json").read_text())["rows"]
    assert len(matrix) == 4
    assert {(row["arm"], row["owners"]) for row in matrix} == {
        ("owners-32", 32),
        ("owners-64", 64),
    }
    commands = [
        json.loads(path.read_text())
        for path in out.glob("arms/*/*/restore.command.json")
    ]
    assert len(commands) == 4
    assert (
        len(
            {tuple(runner.normalized_command(command)) for command in commands}
        )
        == 1
    )
