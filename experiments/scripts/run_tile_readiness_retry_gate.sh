#!/usr/bin/env bash
set -euo pipefail

die() {
    echo "run_tile_readiness_retry_gate: $*" >&2
    exit 2
}

if [[ $# -ne 2 ]]; then
    echo "usage: $0 GEM5_BIN OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
out=$(realpath -m "$2")
timeout_seconds=${TIMEOUT_SECONDS:-7200}
allow_dirty=${ALLOW_DIRTY:-0}
tile_elements=${TILE_ELEMENTS:-1024}
debug_flags=${DEBUG_FLAGS:-}
test_source=benchmarks/API/test_tile_readiness_retry.cpp
runner_source=experiments/scripts/run_tile_readiness_retry_gate.sh
config=configs/deprecated/example/se.py
ramulator=ext/ramulator2/ramulator2/example_gem5_config.yaml
case $tile_elements in
    1024)
        expected_marker='TILE_READINESS_RETRY_RESULT elements=1024 sum=8907264 hash=805893729001882087 errors=0'
        ;;
    16384)
        expected_marker='TILE_READINESS_RETRY_RESULT elements=16384 sum=2281611264 hash=15326592238407866328 errors=0'
        ;;
    *)
        die "TILE_ELEMENTS must be 1024 or 16384"
        ;;
esac

[[ -x $gem5 ]] || die "missing simulator: $gem5"
[[ $timeout_seconds =~ ^[0-9]+$ ]] || die "TIMEOUT_SECONDS must be an integer"
[[ $allow_dirty == 0 || $allow_dirty == 1 ]] ||
    die "ALLOW_DIRTY must be 0 or 1"
[[ ! -e $out ]] || die "output already exists: $out"

for source in src/mem/MAA/CpuSidePort.cc src/mem/MAA/MAA.cc \
              src/mem/MAA/MAA.hh "$test_source" "$runner_source"; do
    [[ -f $root/$source ]] || die "missing source: $source"
    if [[ $allow_dirty == 0 ]]; then
        git -C "$root" ls-files --error-unmatch "$source" >/dev/null 2>&1 ||
            die "untracked acceptance source: $source"
        git -C "$root" diff --quiet HEAD -- "$source" ||
            die "acceptance source differs from HEAD: $source"
    fi
done

mkdir -p "$out/bin" "$out/checkpoint" "$out/restore"
trap 'rc=$?; trap - EXIT; if [[ $rc -ne 0 ]]; then rm -f "$out/gate.pass"; printf "%s\n" "$rc" > "$out/gate.fail"; fi; exit "$rc"' EXIT

cxx=${CXX:-g++}
"$cxx" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O3 -Wall -Wextra -g3 -fopenmp \
    -DGEM5 -DTILE_SIZE="$tile_elements" -DNUM_CORES=4 \
    -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/build/x86/abi/x86/m5op.S" \
    "$root/$test_source" -o "$out/bin/test_tile_readiness_retry"

{
    printf 'simulator_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'simulator_sha256=%s\n' "$(sha256sum "$gem5" | awk '{print $1}')"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'allow_dirty=%s\n' "$allow_dirty"
    printf 'timeout_seconds=%s\n' "$timeout_seconds"
    printf 'tile_elements=%s\n' "$tile_elements"
    printf 'debug_flags=%s\n' "$debug_flags"
    printf 'expected_marker=%s\n' "$expected_marker"
} > "$out/source.txt"
sha256sum "$gem5" "$root/$test_source" "$root/$runner_source" \
    "$root/src/mem/MAA/CpuSidePort.cc" "$root/src/mem/MAA/MAA.cc" \
    "$root/src/mem/MAA/MAA.hh" "$root/$config" "$root/$ramulator" \
    "$out/bin/test_tile_readiness_retry" > "$out/artifact_sha256.txt"
git -C "$root" diff -- src/mem/MAA/CpuSidePort.cc src/mem/MAA/MAA.cc \
    src/mem/MAA/MAA.hh "$test_source" "$runner_source" > "$out/source.diff"

export LD_LIBRARY_PATH="$root/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"
checkpoint_cmd=(
    timeout 300 "$gem5" --listener-mode=off
    --outdir="$out/checkpoint" "$root/$config"
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1
    --cmd "$out/bin/test_tile_readiness_retry"
)
printf '%q ' "${checkpoint_cmd[@]}" > "$out/checkpoint/command"
printf '\n' >> "$out/checkpoint/command"
set +e
/usr/bin/time -f 'checkpoint_wall=%e checkpoint_rss_kb=%M' \
    "${checkpoint_cmd[@]}" > "$out/checkpoint/run.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/checkpoint/exit"
[[ $checkpoint_rc -eq 0 ]] || die "checkpoint failed with rc=$checkpoint_rc"
mapfile -t checkpoints < <(
    find "$out/checkpoint" -mindepth 2 -maxdepth 2 -type f \
        -name m5.cpt -printf '%h\n' | sort -u
)
[[ ${#checkpoints[@]} -eq 1 ]] ||
    die "expected exactly one checkpoint, found ${#checkpoints[@]}"
cp -a --reflink=auto "${checkpoints[0]}" "$out/restore/"
sha256sum "$out/restore/$(basename "${checkpoints[0]}")/m5.cpt" \
    "$out/restore/$(basename "${checkpoints[0]}")/system.physmem.store0.pmem" \
    > "$out/checkpoint_sha256.txt"

debug_args=()
if [[ -n $debug_flags ]]; then
    debug_args=(
        --debug-flags="$debug_flags"
        --debug-file=tile_readiness_retry.trace.gz
    )
fi
restore_cmd=(
    timeout "$timeout_seconds" "$gem5" --listener-mode=off
    "${debug_args[@]}"
    --outdir="$out/restore" "$root/$config"
    --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --sys-clock 3.2GHz --cpu-clock 3.2GHz --caches
    --l1d_size=32kB --l1d_assoc=8 --l1d-hwp-type=StridePrefetcher
    --l1d_mshrs=16 --l1d_write_buffers=8
    --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher
    --l1i_mshrs=16 --l1i_write_buffers=8
    --l2cache --l2_size=256kB --l2_assoc=4
    --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16
    --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256
    --l3_write_buffers=128 --l3_ports=4 --cacheline_size=64
    --mem-type Ramulator2 --ramulator-config "$root/$ramulator"
    --mem-channels=2 --maa --maa_num_maas=1
    --maa_num_tile_elements="$tile_elements" --maa_l2_uncacheable
    --maa_l3_uncacheable --maa_num_initial_row_table_slices=32
    --cmd "$out/bin/test_tile_readiness_retry"
)
printf '%q ' "${restore_cmd[@]}" > "$out/restore/command"
printf '\n' >> "$out/restore/command"
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
    /usr/bin/time -f 'restore_wall=%e restore_rss_kb=%M' \
    "${restore_cmd[@]}" > "$out/restore/run.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" > "$out/restore/exit"

stats_blob=$(awk '
    /^---------- Begin Simulation Statistics/ { section++ }
    section == 1 && $1 == "simTicks" { ticks=$2; ticks_seen++ }
    section == 1 && $1 == "system.maa.cpu_spd_data_read_deferrals" {
        deferrals=$2; deferrals_seen++
    }
    section == 1 && $1 == "system.maa.cpu_spd_data_read_retry_signals" {
        signals=$2; signals_seen++
    }
    section == 1 && $1 == "system.maa.cpu_spd_data_read_retry_attempts" {
        attempts=$2; attempts_seen++
    }
    section == 1 && $1 == "system.maa.cpu_spd_data_read_retry_acceptances" {
        acceptances=$2; acceptances_seen++
    }
    /^---------- End Simulation Statistics/ && section == 1 {
        if (ticks_seen != 1 || deferrals_seen != 1 || signals_seen != 1 ||
            attempts_seen != 1 || acceptances_seen != 1)
            exit 2
        printf "%s\n%s\n%s\n%s\n%s\n", ticks, deferrals, signals,
            attempts, acceptances
        emitted=1
        exit 0
    }
    END { if (!emitted) exit 2 }
' "$out/restore/stats.txt") || stats_blob=
mapfile -t stats <<< "$stats_blob"
ticks=${stats[0]:-NA}
deferrals=${stats[1]:-NA}
signals=${stats[2]:-NA}
attempts=${stats[3]:-NA}
acceptances=${stats[4]:-NA}
marker=$(grep -Fxc "$expected_marker" "$out/restore/run.log" || true)
roi=$(grep -Fxc 'ROI Ended' "$out/restore/run.log" || true)
fatal=$(grep -Eic \
    'panic|fatal|assert|abort|segmentation fault|error:' \
    "$out/restore/run.log" || true)

valid=1
[[ $checkpoint_rc -eq 0 && $restore_rc -eq 0 && $marker -eq 1 &&
   $roi -eq 1 && $fatal -eq 0 && $ticks =~ ^[1-9][0-9]*$ &&
   $deferrals =~ ^[1-9][0-9]*$ && $signals =~ ^[1-9][0-9]*$ &&
   $attempts =~ ^[1-9][0-9]*$ && $acceptances =~ ^[1-9][0-9]*$ ]] ||
    valid=0
if [[ $valid -eq 1 ]] &&
   ((deferrals != signals || signals != attempts ||
     acceptances > attempts)); then
    valid=0
fi

printf 'restore_rc\tsim_ticks\tdeferrals\tretry_signals\tretry_attempts\tretry_acceptances\tfatal_count\tvalid\n' \
    > "$out/result.tsv"
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$restore_rc" "$ticks" "$deferrals" "$signals" "$attempts" \
    "$acceptances" "$fatal" "$valid" >> "$out/result.tsv"
cat "$out/result.tsv"
[[ $valid -eq 1 ]]
: > "$out/gate.pass"
