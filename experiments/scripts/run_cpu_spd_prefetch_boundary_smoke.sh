#!/usr/bin/env bash
set -euo pipefail

die() {
    echo "run_cpu_spd_prefetch_boundary_smoke: $*" >&2
    exit 2
}

if [[ $# -ne 2 ]]; then
    echo "usage: $0 GEM5_BIN OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
out=$(realpath -m "$2")
allow_dirty=${ALLOW_DIRTY:-0}
timeout_seconds=${TIMEOUT_SECONDS:-1800}
guest_source=benchmarks/API/test_cpu_spd_prefetch_boundary.cpp
runner_source=experiments/scripts/run_cpu_spd_prefetch_boundary_smoke.sh
config=configs/deprecated/example/se.py
ramulator_config=ext/ramulator2/ramulator2/example_gem5_config.yaml
expected='CPU_SPD_PREFETCH_BOUNDARY guest_elements=4096 sum=142583808 last=69618 result=PASS'
negative_marker='CPU_SPD_PREFETCH_BOUNDARY_NEGATIVE scan_sum=142583808 scan_last=69618 next=architectural_element4096'

[[ -x $gem5 ]] || die "missing simulator: $gem5"
[[ ! -e $out ]] || die "output already exists: $out"
[[ $allow_dirty == 0 || $allow_dirty == 1 ]] ||
    die "ALLOW_DIRTY must be 0 or 1"
[[ $timeout_seconds =~ ^[1-9][0-9]*$ ]] ||
    die "TIMEOUT_SECONDS must be positive"

sources=(
    src/mem/MAA/CpuSpdAperture.hh
    src/mem/MAA/CpuSidePort.cc
    src/mem/MAA/MAA.cc
    src/mem/MAA/MAA.hh
    "$guest_source"
    "$runner_source"
)
for source in "${sources[@]}"; do
    [[ -f $root/$source ]] || die "missing source: $source"
    if [[ $allow_dirty == 0 ]]; then
        git -C "$root" ls-files --error-unmatch "$source" >/dev/null 2>&1 ||
            die "untracked acceptance source: $source"
        git -C "$root" diff --quiet HEAD -- "$source" ||
            die "acceptance source differs from HEAD: $source"
    fi
done

mkdir -p "$out/bin" "$out/positive/checkpoint" "$out/positive/run" \
    "$out/negative/checkpoint" "$out/negative/run"
trap 'rc=$?; trap - EXIT; if [[ $rc -ne 0 ]]; then printf "%s\n" "$rc" > "$out/gate.fail"; fi; exit "$rc"' EXIT

compile_guest() {
    local arm=$1
    local negative=$2
    "${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
        -I"$root/util/m5/src" -std=c++11 -O3 -Wall -Wextra -Werror \
        -Wno-ignored-qualifiers -DGEM5 -DNUM_CORES=4 \
        -DNUM_TILES_PER_CORE=8 -DTILE_SIZE=16384 \
        -DMAA_MEM_SIZE=0x80000000 -DCPU_SPD_NEGATIVE_ARM="$negative" \
        "$root/util/m5/src/abi/x86/m5op.S" "$root/$guest_source" \
        -o "$out/bin/$arm"
}
compile_guest positive 0
compile_guest negative 1

ramulator_lib=${RAMULATOR_LIB:-}
if [[ -z $ramulator_lib ]]; then
    ramulator_lib=$(ldd "$gem5" | awk '$1 == "libramulator.so" {print $3}')
fi
[[ -f $ramulator_lib ]] || die "could not resolve simulator libramulator.so"
ramulator_lib=$(realpath "$ramulator_lib")
export LD_LIBRARY_PATH="$(dirname "$ramulator_lib"):${LD_LIBRARY_PATH:-}"

{
    printf 'schema=dx100.cpu_spd_prefetch_boundary.v2\n'
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'allow_dirty=%s\n' "$allow_dirty"
    printf 'logical_elements=16384\nphysical_elements=4096\n'
    printf 'cache_line_bytes=64\nprefetcher=StridePrefetcher\n'
    printf 'producer=bounded_stream_load_4096_then_wait_ready\n'
    printf 'expected_guest_output=%s\n' "$expected"
    printf 'negative_contract=element4096_must_panic_without_value\n'
    printf 'gem5_sha256=%s\n' "$(sha256sum "$gem5" | awk '{print $1}')"
    printf 'ramulator_sha256=%s\n' \
        "$(sha256sum "$ramulator_lib" | awk '{print $1}')"
    printf 'positive_guest_sha256=%s\n' \
        "$(sha256sum "$out/bin/positive" | awk '{print $1}')"
    printf 'negative_guest_sha256=%s\n' \
        "$(sha256sum "$out/bin/negative" | awk '{print $1}')"
} > "$out/manifest.txt"
sha256sum "$gem5" "$ramulator_lib" "$out/bin/positive" \
    "$out/bin/negative" "$root/$config" "$root/$ramulator_config" \
    "${sources[@]/#/$root/}" > "$out/artifacts.sha256"

run_checkpoint() {
    local arm=$1
    local arm_root="$out/$arm"
    local command=(
        timeout 300 "$gem5" --listener-mode=off
        --outdir="$arm_root/checkpoint" "$root/$config"
        --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB
        --max-checkpoints=1 --cmd "$out/bin/$arm"
    )
    printf '%q ' "${command[@]}" > "$arm_root/checkpoint/command"
    printf '\n' >> "$arm_root/checkpoint/command"
    set +e
    "${command[@]}" > "$arm_root/checkpoint/restore.log" 2>&1
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$arm_root/checkpoint/exit"
    [[ $rc -eq 0 ]] || die "$arm checkpoint failed with rc=$rc"
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
              "$arm_root/checkpoint/restore.log" || true) -eq 1 ]] ||
        die "$arm checkpoint terminal marker missing"
}

run_timing() {
    local arm=$1
    local arm_root="$out/$arm"
    local command=(
        timeout "$timeout_seconds" "$gem5" --listener-mode=off
        --debug-flags=MAAVirtualTrace
        --debug-file=cpu_spd_prefetch_boundary.trace
        --outdir="$arm_root/run" "$root/$config" --cpu-type X86O3CPU
        -r 1 -n 4 --mem-size 2GB
        --checkpoint-dir="$arm_root/checkpoint"
        --sys-clock 3.2GHz --cpu-clock 3.2GHz --caches
        --l1d_size=32kB --l1d_assoc=8 --l1d-hwp-type=StridePrefetcher
        --l1d_mshrs=16 --l1d_write_buffers=8 --l1i_size=32kB
        --l1i_assoc=8 --l1i_mshrs=16 --l1i_write_buffers=8 --l2cache
        --l2_size=256kB --l2_assoc=4 --l2_mshrs=32
        --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
        --l3_mshrs=64 --l3_write_buffers=32 --l3_ports=4
        --cacheline_size=64 --mem-type Ramulator2
        --ramulator-config "$root/$ramulator_config" --mem-channels=2
        --maa --maa_num_maas=1 --maa_num_indirect_units_per_maa=1
        --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096
        --maa_l2_uncacheable --maa_l3_uncacheable --cmd "$out/bin/$arm"
    )
    printf '%q ' "${command[@]}" > "$arm_root/run/command"
    printf '\n' >> "$arm_root/run/command"
    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
        "${command[@]}" > "$arm_root/run/restore.log" 2>&1
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$arm_root/run/exit"
    return "$rc"
}

run_checkpoint positive
run_checkpoint negative
run_timing positive || die "positive timing run failed"

positive_log="$out/positive/run/restore.log"
positive_stats="$out/positive/run/stats.txt"
positive_trace="$out/positive/run/cpu_spd_prefetch_boundary.trace"
[[ $(grep -Fxc "$expected" "$positive_log" || true) -eq 1 ]] ||
    die "positive exact guest output missing"
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
          "$positive_log" || true) -eq 1 ]] || die "positive exit missing"
[[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
          "$positive_log" || true) -eq 0 ]] || die "positive fatal marker"
[[ -s $positive_stats && -s $positive_trace ]] ||
    die "positive stats or trace missing"

read -r drops rejections sim_ticks < <(awk '
    /^---------- Begin Simulation Statistics/ { section++ }
    section == 1 && $1 == "system.maa.cpu_spd_boundary_prefetch_drops" {
        drops=$2; drops_seen++
    }
    section == 1 && $1 == "system.maa.cpu_spd_out_of_range_rejections" {
        rejections=$2; rejections_seen++
    }
    section == 1 && $1 == "simTicks" { ticks=$2; ticks_seen++ }
    /^---------- End Simulation Statistics/ && section == 1 {
        if (drops_seen != 1 || rejections_seen != 1 || ticks_seen != 1)
            exit 2
        printf "%.0f %.0f %.0f\n", drops, rejections, ticks
        emitted=1
        exit
    }
    END { if (!emitted) exit 2 }
' "$positive_stats") || die "could not extract positive aperture stats"
[[ $drops =~ ^[1-9][0-9]*$ ]] || die "expected a drop, got $drops"
[[ $rejections == 0 ]] || die "positive rejection count is $rejections"
trace_drops=$(grep -c ' event=cpu_spd_boundary_prefetch_drop ' \
    "$positive_trace" || true)
[[ $trace_drops -eq $drops ]] ||
    die "positive trace/stat mismatch: trace=$trace_drops stats=$drops"
tagged_drops=$(grep -Ec \
    ' event=cpu_spd_boundary_prefetch_drop .*packet_prefetch=0 request_prefetch=0 task_prefetch=1 cmd=ReadSharedReq response=BadAddress spd_touched=0 invalidator_touched=0$' \
    "$positive_trace" || true)
[[ $tagged_drops -eq $drops ]] || die "positive provenance trace mismatch"

if run_timing negative; then
    negative_rc=0
else
    negative_rc=$?
fi
[[ $negative_rc -ne 0 && $negative_rc -ne 124 ]] ||
    die "negative arm did not fail by aperture panic: rc=$negative_rc"
negative_log="$out/negative/run/restore.log"
negative_trace="$out/negative/run/cpu_spd_prefetch_boundary.trace"
[[ $(grep -Fxc "$negative_marker" "$negative_log" || true) -eq 1 ]] ||
    die "negative precondition marker missing"
grep -Fq 'CPU SPD aperture rejected physical_out_of_range access:' \
    "$negative_log" || die "negative aperture panic missing"
[[ $(grep -c 'CPU_SPD_PREFETCH_BOUNDARY_NEGATIVE_OBSERVED' \
          "$negative_log" || true) -eq 0 ]] || die "negative observed a value"
[[ $(grep -Ec '^Exiting @ tick .*m5_exit' "$negative_log" || true) -eq 0 ]] ||
    die "negative arm exited normally"
negative_drops=$(grep -c ' event=cpu_spd_boundary_prefetch_drop ' \
    "$negative_trace" || true)
[[ $negative_drops -ge 1 ]] || die "negative prefetch opportunity absent"

{
    printf 'terminal=true\ncorrect=true\n'
    printf 'guest_output=%s\n' "$expected"
    printf 'boundary_prefetch_drops=%s\n' "$drops"
    printf 'architectural_out_of_range_rejections=%s\n' "$rejections"
    printf 'positive_simTicks=%s\n' "$sim_ticks"
    printf 'negative_boundary_prefetch_drops=%s\n' "$negative_drops"
    printf 'negative_exit_code=%s\n' "$negative_rc"
    printf 'negative_architectural_element4096=panic_before_value\n'
} > "$out/result.txt"
touch "$out/gate.pass"
cat "$out/result.txt"
echo "CPU_SPD_PREFETCH_BOUNDARY_SMOKE_PASS"
