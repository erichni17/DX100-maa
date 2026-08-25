#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 GEM5_BIN OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
out=$(realpath -m "$2")
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
source_file="$root/benchmarks/gapbs/src/sssp.cc"
frozen_ramulator=/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/input/libramulator.so
frozen_ramulator_sha256=76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753

hash_value() {
    sha256sum "$1" | awk '{print $1}'
}

require_hash() {
    local path=$1 expected=$2
    [[ -f $path && $(hash_value "$path") == "$expected" ]]
}

stat_sum() {
    local suffix=$1
    awk -v suffix="$suffix" '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 &&
            ($1 == "system.maa." suffix || $1 ~ ("_" suffix "$")) {
            sum += $2
            found++
        }
        /^---------- End Simulation Statistics/ && section == 1 {
            if (!found)
                exit 2
            printf "%.0f\n", sum
            exit
        }
    ' "$stats"
}

[[ -x $gem5 ]] || { echo "missing gem5 binary: $gem5" >&2; exit 2; }
require_hash "$frozen_ramulator" "$frozen_ramulator_sha256" || {
    echo "missing or mismatched frozen Ramulator library" >&2
    exit 2
}
export LD_LIBRARY_PATH="$(dirname "$frozen_ramulator"):${LD_LIBRARY_PATH:-}"
resolved_ramulator=$(ldd "$gem5" | awk '$1 == "libramulator.so" {print $3}')
[[ -n $resolved_ramulator &&
   $(realpath "$resolved_ramulator") == $(realpath "$frozen_ramulator") ]] || {
    echo "candidate gem5 does not resolve the frozen Ramulator library" >&2
    exit 2
}
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence run from a dirty source tree" >&2
    exit 1
}

mkdir -p "$out/bin" "$out/graph" "$out/checkpoint" "$out/run"
guest="$out/bin/sssp_maa_2G_old_result_hybrid_fp"
converter="$out/bin/converter"
wel="$out/graph/sssp_old_result_hybrid_small.wel"
graph="$out/graph/sssp_old_result_hybrid_small.wsg"

"${CXX:-g++}" -I"$root/benchmarks/gapbs/src" -std=c++11 -O3 -Wall \
    -Wextra -Werror -Wno-unused-parameter -fopenmp \
    "$root/benchmarks/gapbs/src/converter.cc" \
    -o "$converter"
"${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O3 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers -Wno-unused-parameter -fopenmp -DGEM5 -DMAA \
    -DNUM_CORES=4 \
    -DNUM_TILES_PER_CORE=8 -DTILE_SIZE=16384 \
    -DMAA_CONSUMER_TILE_SIZE=4096 -DMAA_MEM_SIZE=0x80000000 \
    -DSSSP_FP_ENABLE=1 -DSSSP_OLD_RESULT_HYBRID=1 \
    "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$guest"
chmod 0555 "$guest"

# Directed two-level fanout chosen to activate exactly four safe logical
# windows: the 4,096 active middle vertices each have 16 distinct destinations
# and no destination aliases an active source.  This is input construction,
# not a native/oracle rerun.
for ((u = 1; u <= 4096; ++u)); do
    printf '0 %d 1\n' "$u"
done >"$wel"
for ((u = 1; u <= 4096; ++u)); do
    base=$((4097 + (u - 1) * 16))
    for ((lane = 0; lane < 16; ++lane)); do
        printf '%d %d 1\n' "$u" "$((base + lane))"
    done
done >>"$wel"
"$converter" -f "$wel" -w -b "$graph" >"$out/graph/converter.log" 2>&1
chmod 0444 "$graph"

options="-f $graph -n 1 -r 0 -d 1 -v"
checkpoint_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" "$config"
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1
    --cmd "$guest" --options "$options"
)
restore_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/run"
    "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --checkpoint-dir="$out/checkpoint"
    --sys-clock 3.2GHz --cpu-clock 3.2GHz
    --caches --l1d_size=32kB --l1d_assoc=8
    --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16
    --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8
    --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16 --l1i_write_buffers=8
    --l2cache --l2_size=256kB --l2_assoc=4
    --l2-hwp-type=StridePrefetcher --l2_mshrs=32
    --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
    --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
    --cacheline_size=64 --mem-type Ramulator2
    --ramulator-config "$ramulator" --mem-channels=2
    --maa_ncbus_width=32
    --maa --maa_num_maas=1 --maa_num_indirect_units_per_maa=4
    --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096
    --maa_num_offset_table_entries=16384
    --maa_num_offset_table_epoch_entries=16384
    --maa_num_initial_row_table_slices=32
    --maa_l2_uncacheable --maa_l3_uncacheable
    --maa_soa_jit_old_result_pressure_policy=densest
    --maa_soa_jit_old_result_partial_credits=4
    --maa_soa_jit_predicate_active_credits=1
    --maa_soa_jit_active_contexts=8
    --maa_soa_jit_value_lookahead=1
    --maa_soa_jit_value_cache_enable
    --maa_soa_jit_pre_a_value_lookahead
    --maa_soa_jit_value_prefetch_credits=0
    --maa_soa_jit_active_value_owners=64
    --maa_soa_jit_apply_lanes=1
    --cmd "$guest"
    --options "$options"
)

{
    printf 'schema=dx100.sssp.old_result_hybrid.small.v1\n'
    printf 'source_commit=%s\nsource_sha256=%s\n' \
        "$(git -C "$root" rev-parse HEAD)" "$(hash_value "$source_file")"
    printf 'gem5_path=%s\ngem5_sha256=%s\n' "$gem5" "$(hash_value "$gem5")"
    printf 'ramulator_library_path=%s\nramulator_library_sha256=%s\n' \
        "$frozen_ramulator" "$frozen_ramulator_sha256"
    printf 'guest_sha256=%s\ngraph_sha256=%s\n' \
        "$(hash_value "$guest")" "$(hash_value "$graph")"
    printf 'logical_elements=16384\nphysical_tile_elements=4096\n'
    printf 'mem_channels=2\nindirect_units=4\nrow_table_slices=32\n'
    printf 'expected_routed_windows=4\n'
    printf 'native_arms=0\nwall_timeout=none\nfull_graph=false\n'
    printf 'cpu_spd_boundary_prefetch_drops=reported_not_forced\n'
    printf 'cpu_spd_out_of_range_rejections=0_required\n'
} >"$out/manifest.txt"

sha256sum "$gem5" "$frozen_ramulator" "$guest" "$graph" "$source_file" "$config" \
    "$ramulator" "$0" >"$out/artifacts.before.sha256"

OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${checkpoint_cmd[@]}" \
    >"$out/checkpoint.log" 2>&1
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
          "$out/checkpoint.log" || true) -eq 1 ]]

OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${restore_cmd[@]}" \
    >"$out/run/restore.log" 2>&1

restore="$out/run/restore.log"
stats="$out/run/stats.txt"
fingerprint='SSSP_FINGERPRINT vertices=69633 reached=69633 unreachable=0 distance_sum=135168 max_distance=2 hash_a=a0531a7ddb9387df hash_b=39f1ea63bc8817e8 triangle_violations=0 missing_predecessors=0 nonpositive_weights=0 negative_distances=0 result=PASS'
terminal='SSSP_OLD_RESULT_HYBRID_TERMINAL treatment=old_result_hybrid eligible_windows=4 routed_windows=4 index_publish_pages=16 value_publish_pages=16 old_result_words=65536 legacy_words=0 logical_reorder_words=16384 physical_spd_words=4096 row_table_slices=32 predicate_span=coherent_aligned old_result_span=coherent_aligned duplicate_order=legacy_physical_pages host_spd_reads=0 hidden_result_payload_bytes=0 counts_close=1'
[[ $(grep -Fxc "$fingerprint" "$restore" || true) -eq 1 ]]
[[ $(grep -Fxc "$terminal" "$restore" || true) -eq 1 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
          "$restore" || true) -eq 1 ]]
[[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
          "$restore" || true) -eq 0 ]]
[[ -s $stats ]]

instructions=$(stat_sum IND_SoaJitInstructions)
selected=$(stat_sum IND_SoaJitSelected)
rejected=$(stat_sum IND_SoaJitPredicateRejected)
captures=$(stat_sum IND_SoaJitOldResultCaptures)
issues=$(stat_sum IND_SoaJitOldResultWriteIssues)
responses=$(stat_sum IND_SoaJitOldResultWriteResponses)
a_reads=$(stat_sum IND_SoaJitAReadIssues)
a_read_responses=$(stat_sum IND_SoaJitAReadResponses)
a_writes=$(stat_sum IND_SoaJitAWriteIssues)
a_write_responses=$(stat_sum IND_SoaJitAWriteResponses)
terminals=$(stat_sum IND_SoaJitTerminalCompletions)
boundary_drops=$(stat_sum cpu_spd_boundary_prefetch_drops)
aperture_rejections=$(stat_sum cpu_spd_out_of_range_rejections)

[[ $instructions -eq 4 && $terminals -eq 4 ]]
[[ $selected -eq 65536 && $rejected -eq 0 && $captures -eq 65536 ]]
[[ $issues -gt 0 && $issues -eq $responses ]]
[[ $a_reads -gt 0 && $a_reads -eq $a_read_responses && \
   $a_reads -eq $a_writes && $a_writes -eq $a_write_responses ]]
[[ $boundary_drops =~ ^[0-9]+$ && $aperture_rejections =~ ^[0-9]+$ ]]
[[ $aperture_rejections -eq 0 ]]

sha256sum "$gem5" "$frozen_ramulator" "$guest" "$graph" "$source_file" "$config" \
    "$ramulator" "$0" >"$out/artifacts.after.sha256"
cmp -s "$out/artifacts.before.sha256" "$out/artifacts.after.sha256"

sim_ticks=$(awk '$1 == "simTicks" { print $2; exit }' "$stats")
[[ $sim_ticks =~ ^[1-9][0-9]*$ ]]
{
    printf 'terminal=true\ncorrect=true\n'
    printf 'simTicks=%s\neligible_windows=4\nrouted_windows=4\n' "$sim_ticks"
    printf 'old_result_captures=%s\nold_result_write_issues=%s\n' \
        "$captures" "$issues"
    printf 'old_result_write_responses=%s\n' "$responses"
    printf 'cpu_spd_boundary_prefetch_drops=%s\n' "$boundary_drops"
    printf 'cpu_spd_out_of_range_rejections=%s\n' "$aperture_rejections"
} >"$out/result.txt"
touch "$out/gate.complete"
cat "$out/result.txt"
echo "SSSP_OLD_RESULT_HYBRID_SMALL_PASS"
