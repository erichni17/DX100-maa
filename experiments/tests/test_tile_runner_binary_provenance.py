import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRESH_TILE_RUNNERS = (
    Path("benchmarks/gapbs/run_gapbs_tile_smoke.sh"),
    Path("benchmarks/UME/run_ume_tile_smoke.sh"),
    Path("benchmarks/NAS/cg/run_cg_tile_smoke.sh"),
    Path("benchmarks/NAS/is/run_is_smoke.sh"),
    Path("benchmarks/spatter/run_xrage_tile_smoke.sh"),
)
BACKFILL = ROOT / "experiments/scripts/backfill_legacy_tile_provenance.py"
VERIFIER = ROOT / "experiments/scripts/verify_tile_gem5_provenance.py"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_executable(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    path.chmod(0o755)


def fake_gem5(path, label):
    write_executable(
        path,
        f"""#!/usr/bin/env bash
set -euo pipefail
printf 'command line:'
printf ' %q' "$0" "$@"
printf '\\n'
outdir=
restore=0
previous=
for argument in "$@"; do
  case "$argument" in
    --outdir=*) outdir=${{argument#--outdir=}} ;;
  esac
  if [[ "$previous" == -r && "$argument" == 1 ]]; then
    restore=1
  fi
  previous=$argument
done
[[ -n "$outdir" ]]
if [[ "$restore" == 1 ]]; then
  phase=restore
else
  phase=checkpoint
fi
printf '%s\\t%s\\n' {label!r} "$phase" >> "$FAKE_GEM5_CALLS"
mkdir -p "$outdir"
if [[ "$restore" == 1 ]]; then
  printf 'simTicks 123\\nsystem.maa.cycles_TOTAL 456\\n' > "$outdir/stats.txt"
  printf '%s\\n' \
    'BFS_FP sample depth_reached=4194304 depth_sum=19771483 depth_sq_sum=94148523 max_depth=6 invalid_chains=0 depth_hash=10642142323936141248' \
    'Exiting @ tick 123 because m5_exit instruction encountered'
else
  if [[ -n "${{FAKE_CKPT_DELAY:-}}" ]]; then
    sleep "$FAKE_CKPT_DELAY"
  fi
  mkdir -p "$outdir/cpt.1"
fi
""",
    )


def run_gapbs(runner, environment):
    return subprocess.run(
        [
            str(runner),
            "gem5.opt.ovl_base",
            "bfs",
            "1024",
            "22",
            "1",
            "2GB",
            "0",
            "0",
            "0",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_fresh_tile_runners_use_immutable_sha_identity_and_publication():
    for relative in FRESH_TILE_RUNNERS:
        runner = (ROOT / relative).read_text()
        assert "DEFAULT_GEM5_BIN=" in runner
        assert 'TAG="${LEGACY_TAG}_sha256_${GEM5_SHA256}"' in runner
        assert "materialize_gem5_snapshot" in runner
        assert "GEM5_SNAPSHOT_ROOT=" in runner
        assert "verify_tile_gem5_provenance.py" in runner
        assert "RESULTS=$CAMPAIGN_ROOT/results_provenance_v2.tsv" in runner
        assert "execution_snapshot" in runner
        assert "gem5_provenance_matches" in runner
        assert "printf 'resolved_path\\t%s\\n'" in runner
        assert "printf 'sha256\\t%s\\n'" in runner
        assert "printf 'output_tag\\t%s\\n'" in runner
        assert "write_gem5_provenance" in runner
        assert "CKPT_LOCK=" in runner
        assert "flock -x 8" in runner
        assert "CKPT_TMP=$(mktemp -d" in runner
        assert "[ckpt] atomically published" in runner
        assert "gem5_resolved_path" in runner
        assert "gem5_sha256" in runner
        assert "gem5_output_tag" in runner
    ume_runner = (ROOT / "benchmarks/UME/run_ume_tile_smoke.sh").read_text()
    assert 'BENCHMARK_SHA256=$(sha256sum -- "$BIN")' in ume_runner
    assert 'CKPT="${CKPT_BASE}_binsha_${BENCHMARK_SHA256}"' in ume_runner
    assert '"$OUT/benchmark_provenance.tsv"' in ume_runner


def install_fake_gapbs_make(fake_bin):
    make = fake_bin / "make"
    write_executable(
        make,
        """#!/usr/bin/env bash
set -euo pipefail
directory=
while (($#)); do
  if [[ "$1" == -C ]]; then
    directory=$2
    shift 2
  else
    shift
  fi
done
[[ -n "$directory" ]]
mkdir -p "$directory"
: > "$directory/bfs_maa_1K"
chmod +x "$directory/bfs_maa_1K"
if [[ -n "${FAKE_MUTATE_GEM5_SOURCE:-}" ]]; then
  cp -- "$FAKE_REPLACEMENT_GEM5" "$FAKE_MUTATE_GEM5_SOURCE"
  chmod +x "$FAKE_MUTATE_GEM5_SOURCE"
fi
""",
    )
    return make


def gapbs_fixture(tmp_path):
    source = tmp_path / "source"
    runtime = tmp_path / "runtime"
    campaign = tmp_path / "campaign"
    checkpoints = tmp_path / "checkpoints"
    snapshots = tmp_path / "snapshots"
    fake_bin = tmp_path / "bin"
    calls = tmp_path / "gem5.calls"
    gapbs = source / "benchmarks/gapbs"
    gapbs.mkdir(parents=True)
    (gapbs / "serialized_graph_22.sg").write_text("graph\n")
    install_fake_gapbs_make(fake_bin)
    baseline = runtime / "build/X86/gem5.opt.ovl_base"
    patched = source / "build/X86/gem5.opt"
    fake_gem5(baseline, "baseline")
    fake_gem5(patched, "patched")
    environment = dict(
        os.environ,
        PATH=f"{fake_bin}:{os.environ['PATH']}",
        DX100_SOURCE_ROOT=str(source),
        DX100_RUNTIME_ROOT=str(runtime),
        CAMPAIGN_ROOT=str(campaign),
        CHECKPOINT_ROOT=str(checkpoints),
        GEM5_SNAPSHOT_ROOT=str(snapshots),
        FAKE_GEM5_CALLS=str(calls),
        DX100_PROVENANCE_VERIFIER=str(VERIFIER),
    )
    return {
        "source": source,
        "runtime": runtime,
        "campaign": campaign,
        "checkpoints": checkpoints,
        "snapshots": snapshots,
        "fake_bin": fake_bin,
        "calls": calls,
        "baseline": baseline,
        "patched": patched,
        "environment": environment,
    }


def test_gapbs_override_has_distinct_output_and_exact_sha_reuse(tmp_path):
    fixture = gapbs_fixture(tmp_path)
    source = fixture["source"]
    campaign = fixture["campaign"]
    calls = fixture["calls"]
    baseline = fixture["baseline"]
    patched = fixture["patched"]
    patched_sha = sha256(patched)
    runner = ROOT / "benchmarks/gapbs/run_gapbs_tile_smoke.sh"

    environment = dict(
        fixture["environment"],
        DX100_GEM5_BIN=str(patched),
    )
    first = run_gapbs(runner, environment)
    assert first.returncode == 0, first.stdout

    baseline_sha = sha256(baseline)
    baseline_out = campaign / (
        "bfs_s22_t1024_m2GB_gem5.opt.ovl_base_sha256_" + baseline_sha
    )
    patched_out = campaign / (
        "bfs_s22_t1024_m2GB_gem5.opt.ovl_base_sha256_" + patched_sha
    )
    assert not baseline_out.exists()
    assert patched_out.is_dir()
    provenance = (patched_out / "gem5_provenance.tsv").read_text()
    assert f"resolved_path\t{patched.resolve()}\n" in provenance
    assert f"sha256\t{patched_sha}\n" in provenance
    assert (
        f"output_tag\tgem5.opt.ovl_base_sha256_{patched_sha}\n" in provenance
    )
    snapshot = Path(
        next(
            line.split("\t", 1)[1]
            for line in provenance.splitlines()
            if line.startswith("execution_snapshot\t")
        )
    )
    assert snapshot.is_file()
    assert not snapshot.is_symlink()
    assert sha256(snapshot) == patched_sha
    assert calls.read_text().splitlines() == [
        "patched\tcheckpoint",
        "patched\trestore",
    ]

    reused = run_gapbs(runner, environment)
    assert reused.returncode == 0, reused.stdout
    assert "[reuse] accepted" in reused.stdout
    assert calls.read_text().splitlines() == [
        "patched\tcheckpoint",
        "patched\trestore",
    ]

    (patched_out / "gem5_provenance.tsv").write_text(
        "schema_version\t1\n"
        f"resolved_path\t{patched.resolve()}\n"
        f"sha256\t{patched_sha}\n"
    )
    rerun = run_gapbs(runner, environment)
    assert rerun.returncode == 0, rerun.stdout
    assert "[reuse] accepted" not in rerun.stdout
    assert calls.read_text().splitlines() == [
        "patched\tcheckpoint",
        "patched\trestore",
        "patched\trestore",
    ]
    assert (
        f"sha256\t{patched_sha}\n"
        in (patched_out / "gem5_provenance.tsv").read_text()
    )

    (patched_out / "gem5_provenance.tsv").write_text(
        "schema_version\t1\n"
        f"resolved_path\t{patched.resolve()}\n"
        f"sha256\t{'0' * 64}\n"
    )
    rerun = run_gapbs(runner, environment)
    assert rerun.returncode == 0, rerun.stdout
    assert "[reuse] accepted" not in rerun.stdout
    assert calls.read_text().splitlines() == [
        "patched\tcheckpoint",
        "patched\trestore",
        "patched\trestore",
        "patched\trestore",
    ]

    baseline_environment = dict(environment, DX100_GEM5_BIN=str(baseline))
    baseline_run = run_gapbs(runner, baseline_environment)
    assert baseline_run.returncode == 0, baseline_run.stdout
    assert baseline_out.is_dir()
    assert patched_out.is_dir()
    assert calls.read_text().splitlines()[-1] == "baseline\trestore"
    baseline_provenance = (baseline_out / "gem5_provenance.tsv").read_text()
    assert f"resolved_path\t{baseline.resolve()}\n" in baseline_provenance
    assert f"sha256\t{sha256(baseline)}\n" in baseline_provenance


def test_default_path_content_change_gets_new_identity_and_old_snapshot(
    tmp_path,
):
    fixture = gapbs_fixture(tmp_path)
    runner = ROOT / "benchmarks/gapbs/run_gapbs_tile_smoke.sh"
    baseline = fixture["baseline"]
    original_sha = sha256(baseline)
    replacement = tmp_path / "replacement-gem5"
    fake_gem5(replacement, "replacement")
    environment = dict(
        fixture["environment"],
        FAKE_MUTATE_GEM5_SOURCE=str(baseline),
        FAKE_REPLACEMENT_GEM5=str(replacement),
    )

    first = run_gapbs(runner, environment)
    assert first.returncode == 0, first.stdout
    assert fixture["calls"].read_text().splitlines() == [
        "baseline\tcheckpoint",
        "baseline\trestore",
    ]
    original_out = fixture["campaign"] / (
        "bfs_s22_t1024_m2GB_gem5.opt.ovl_base_sha256_" + original_sha
    )
    assert original_out.is_dir()

    replacement_sha = sha256(baseline)
    assert replacement_sha != original_sha
    second_environment = dict(fixture["environment"])
    second = run_gapbs(runner, second_environment)
    assert second.returncode == 0, second.stdout
    replacement_out = fixture["campaign"] / (
        "bfs_s22_t1024_m2GB_gem5.opt.ovl_base_sha256_" + replacement_sha
    )
    assert replacement_out.is_dir()
    assert original_out.is_dir()
    assert fixture["calls"].read_text().splitlines()[-1] == (
        "replacement\trestore"
    )


def test_concurrent_variants_share_one_atomic_checkpoint(tmp_path):
    fixture = gapbs_fixture(tmp_path)
    runner = ROOT / "benchmarks/gapbs/run_gapbs_tile_smoke.sh"
    baseline_environment = dict(
        fixture["environment"],
        DX100_GEM5_BIN=str(fixture["baseline"]),
        FAKE_CKPT_DELAY="0.3",
    )
    patched_environment = dict(
        fixture["environment"],
        DX100_GEM5_BIN=str(fixture["patched"]),
        FAKE_CKPT_DELAY="0.3",
    )
    arguments = [
        str(runner),
        "gem5.opt.ovl_base",
        "bfs",
        "1024",
        "22",
        "1",
        "2GB",
        "0",
        "0",
        "0",
    ]
    first = subprocess.Popen(
        arguments,
        cwd=ROOT,
        env=baseline_environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    second = subprocess.Popen(
        arguments,
        cwd=ROOT,
        env=patched_environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    first_stdout, _ = first.communicate(timeout=20)
    second_stdout, _ = second.communicate(timeout=20)
    assert first.returncode == 0, first_stdout
    assert second.returncode == 0, second_stdout

    calls = fixture["calls"].read_text().splitlines()
    assert sum(line.endswith("\tcheckpoint") for line in calls) == 1
    assert sum(line.endswith("\trestore") for line in calls) == 2
    checkpoint = fixture["checkpoints"] / "gapbs_bfs_s22_t1024_m2GB"
    assert (checkpoint / "cpt.1").is_dir()
    assert not list(fixture["checkpoints"].glob("*.tmp.*"))
    results = (
        (fixture["campaign"] / "results_provenance_v2.tsv")
        .read_text()
        .splitlines()
    )
    assert len(results) == 3
    assert results[0].endswith(
        "\tgem5_resolved_path\tgem5_sha256\tgem5_output_tag"
    )


def test_concurrent_identical_claims_launch_one_restore(tmp_path):
    fixture = gapbs_fixture(tmp_path)
    runner = ROOT / "benchmarks/gapbs/run_gapbs_tile_smoke.sh"
    environment = dict(
        fixture["environment"],
        DX100_GEM5_BIN=str(fixture["patched"]),
        FAKE_CKPT_DELAY="0.2",
    )
    arguments = [
        str(runner),
        "gem5.opt.ovl_base",
        "bfs",
        "1024",
        "22",
        "1",
        "2GB",
        "0",
        "0",
        "0",
    ]
    processes = [
        subprocess.Popen(
            arguments,
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for _ in range(2)
    ]
    outputs = [process.communicate(timeout=20)[0] for process in processes]
    for process, output in zip(processes, outputs):
        assert process.returncode == 0, output
    calls = fixture["calls"].read_text().splitlines()
    assert sum(line.endswith("\tcheckpoint") for line in calls) == 1
    assert sum(line.endswith("\trestore") for line in calls) == 1
    assert any("[reuse] accepted" in output for output in outputs)


def test_old_live_results_file_is_not_mixed_with_provenance_rows(tmp_path):
    fixture = gapbs_fixture(tmp_path)
    runner = ROOT / "benchmarks/gapbs/run_gapbs_tile_smoke.sh"
    old_results = fixture["campaign"] / "results.tsv"
    fixture["campaign"].mkdir(parents=True)
    old_results.write_text("timestamp\tgem5_bin\toutdir\nold\told\told\n")
    original = old_results.read_text()

    result = run_gapbs(runner, fixture["environment"])
    assert result.returncode == 0, result.stdout
    assert old_results.read_text() == original
    provenance_results = (
        (fixture["campaign"] / "results_provenance_v2.tsv")
        .read_text()
        .splitlines()
    )
    assert len(provenance_results) == 2
    assert provenance_results[0].endswith(
        "\tgem5_resolved_path\tgem5_sha256\tgem5_output_tag"
    )


def test_legacy_backfill_requires_manifest_and_command_binding(tmp_path):
    binary = tmp_path / "gem5.opt.ovl_base"
    write_executable(binary, "#!/usr/bin/env bash\nexit 0\n")
    binary_sha = sha256(binary)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "gem5_binary": str(binary),
                "gem5_sha256": binary_sha,
            }
        )
    )
    manifest_sha = sha256(manifest)
    campaign = tmp_path / "campaign"
    accepted = campaign / "accepted_gem5.opt.ovl_base"
    rejected = campaign / "rejected_gem5.opt.ovl_base"
    accepted.mkdir(parents=True)
    rejected.mkdir()
    (accepted / "run.log").write_text(
        f"command line: {binary} --outdir={accepted} config.py\n"
    )
    (rejected / "run.log").write_text(
        f"command line: {binary} --outdir={accepted} config.py\n"
    )
    command = [
        str(BACKFILL),
        "--manifest",
        str(manifest),
        "--expected-manifest-sha256",
        manifest_sha,
        "--campaign-root",
        str(campaign),
    ]

    dry_run = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert dry_run.returncode == 0, dry_run.stdout
    assert f"would-write\t{accepted}" in dry_run.stdout
    assert f"command-mismatch\t{rejected}" in dry_run.stdout
    assert not (accepted / "gem5_provenance.tsv").exists()
    assert not (campaign / f".{accepted.name}.run.lock").exists()

    applied = subprocess.run(
        [*command, "--apply"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert applied.returncode == 0, applied.stdout
    sidecar = (accepted / "gem5_provenance.tsv").read_text()
    assert f"resolved_path\t{binary}\n" in sidecar
    assert f"sha256\t{binary_sha}\n" in sidecar
    assert f"attestation_manifest_sha256\t{manifest_sha}\n" in sidecar
    assert not (rejected / "gem5_provenance.tsv").exists()

    verified = subprocess.run(
        [
            str(VERIFIER),
            "--outdir",
            str(accepted),
            "--resolved-path",
            str(binary),
            "--sha256",
            binary_sha,
            "--output-tag",
            f"{binary.name}_sha256_{binary_sha}",
            "--requested-gbin",
            binary.name,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert verified.returncode == 0, verified.stdout


def test_is_runner_uses_frozen_snapshot_and_output_lock():
    runner = (ROOT / "benchmarks/NAS/is/run_is_smoke.sh").read_text()
    assert "IS_FROZEN_RUNNER" in runner
    assert "--allow-resolved-path-alias" in runner
    assert "reused_resolved_path" in runner
    assert 'exec 9>"$RUN_LOCK"' in runner
    assert runner.index("flock -x 9") < runner.index(
        "if reuse_completed_run; then"
    )


def test_schema_v2_alias_requires_opt_in_and_exact_snapshot_hash(tmp_path):
    current_source = tmp_path / "current/gem5.opt.ovl_base"
    historical_source = tmp_path / "historical/gem5"
    snapshot = tmp_path / "snapshots/gem5"
    outdir = tmp_path / "output"
    write_executable(current_source, "#!/usr/bin/env bash\nexit 0\n")
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(current_source.read_bytes())
    snapshot.chmod(0o555)
    outdir.mkdir()
    digest = sha256(snapshot)
    tag = f"gem5.opt.ovl_base_sha256_{digest}"
    (outdir / "run.log").write_text(
        f"command line: {snapshot} --outdir={outdir} config.py\n"
    )
    (outdir / "gem5_provenance.tsv").write_text(
        "schema_version\t2\n"
        "requested_gbin\tgem5.opt.ovl_base\n"
        f"resolved_path\t{historical_source}\n"
        f"execution_snapshot\t{snapshot}\n"
        f"sha256\t{digest}\n"
        f"output_tag\t{tag}\n"
    )
    command = [
        str(VERIFIER),
        "--outdir",
        str(outdir),
        "--resolved-path",
        str(current_source),
        "--sha256",
        digest,
        "--output-tag",
        tag,
        "--requested-gbin",
        "gem5.opt.ovl_base",
    ]

    strict = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert strict.returncode == 2
    assert "resolved gem5 path differs" in strict.stdout

    aliased = subprocess.run(
        [*command, "--allow-resolved-path-alias"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert aliased.returncode == 0, aliased.stdout
