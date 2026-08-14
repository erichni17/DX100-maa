#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5=/data1/nier/worktrees/DX100-virtualization-line-handoff-20260812/build/X86/gem5.opt
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
binary="$out/bin/test_hybrid_rmw_soa_T16384"

[[ -x $gem5 ]] || { echo "missing gem5: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
mkdir -p "$out/bin" "$out/checkpoints" "$out/runs"

g++ -I"$root/benchmarks/API" -I"$root/include" -I"$root/util/m5/src" \
    -std=c++11 -O2 -Wall -Wextra -Werror -Wno-ignored-qualifiers \
    -DGEM5 -DTILE_SIZE=16384 -DNUM_CORES=4 -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/src/abi/x86/m5op.S" \
    "$root/benchmarks/API/test_hybrid_rmw_soa.cpp" -o "$binary"

common=(
    "$config" --cpu-type X86O3CPU -n 4 --mem-size 2GB
    --sys-clock 3.2GHz --cpu-clock 3.2GHz
    --caches --l1d_size=32kB --l1d_assoc=8 --l1d_mshrs=16
    --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8
    --l1i_mshrs=16 --l1i_write_buffers=8
    --l2cache --l2_size=256kB --l2_assoc=4 --l2_mshrs=32
    --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
    --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
    --cacheline_size=64 --mem-type Ramulator2
    --ramulator-config "$ramulator" --mem-channels=1
    --maa --maa_num_maas=1 --maa_num_indirect_units_per_maa=1
    --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096
    --maa_num_offset_table_entries=16384
    --maa_num_offset_table_epoch_entries=16384
    --maa_num_initial_row_table_slices=16 --cmd "$binary"
)

run_checkpoint() {
    local arm=$1 mode=$2 checkpoint="$out/checkpoints/$1"
    mkdir -p "$checkpoint"
    timeout 300 "$gem5" --listener-mode=off --outdir="$checkpoint" \
        "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
        --max-checkpoints=1 --cmd "$binary" --options "$mode" \
        >"$checkpoint/checkpoint.log" 2>&1
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
        "$checkpoint/checkpoint.log" || true) -eq 1 ]]
}

run_arm() {
    local arm=$1 mode=$2 run="$out/runs/$1" checkpoint="$out/checkpoints/$1"
    mkdir -p "$run"
    timeout 1800 "$gem5" --listener-mode=off --outdir="$run" \
        --debug-flags=MAAVirtualTrace --debug-file=soa_jit_trace.log \
        "${common[@]}" -r 1 --checkpoint-dir="$checkpoint" \
        --options "$mode" >"$run/restore.log" 2>&1
    [[ $(grep -Ec '^HYBRID_RMW_SOA_RESULT ' "$run/restore.log" || true) -eq 1 ]]
    grep -Fqx 'ROI Ended' "$run/restore.log"
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
        "$run/restore.log" || true) -eq 1 ]]
    grep -Fq 'errors=0' "$run/restore.log"
    if [[ $mode != soa ]]; then
        [[ $(grep -Ec '^HYBRID_RMW_SOA_WARM_CHECKSUM ' \
            "$run/restore.log" || true) -eq 2 ]]
        grep -Eq ' lines=1024 .*checksum=.* expected_checksum=' \
            "$run/restore.log"
    fi
}

for arm_mode in baseline:soa dummy_control:soa-warm-control values_warm:soa-warm; do
    arm=${arm_mode%%:*}
    mode=${arm_mode#*:}
    run_checkpoint "$arm" "$mode"
    run_arm "$arm" "$mode"
done

{
    printf 'source_commit='; git -C "$root" rev-parse HEAD
    printf 'source_sha256='; sha256sum "$root/benchmarks/API/test_hybrid_rmw_soa.cpp" | awk '{print $1}'
    printf 'guest_sha256='; sha256sum "$binary" | awk '{print $1}'
    printf 'gem5_sha256='; sha256sum "$gem5" | awk '{print $1}'
    printf 'config_sha256='; sha256sum "$config" | awk '{print $1}'
    printf 'ramulator_sha256='; sha256sum "$ramulator" | awk '{print $1}'
    printf 'comparable_checkpoint=false\n'
    printf 'checkpoint_note=mode_is_parsed_before_m5_checkpoint; each arm has its own checkpoint\n'
    printf 'arms=baseline,dummy_control,values_warm\n'
    printf 'only_intended_arm_delta=mode\n'
} > "$out/manifest.txt"

for arm in baseline dummy_control values_warm; do
    printf '%s\t' "$arm"
    awk '$1 == "simTicks" { print $2; exit }' "$out/runs/$arm/stats.txt"
done > "$out/sim_ticks.tsv"

printf 'SOA_JIT_VALUE_WARM_PROBE_PASS\n'
