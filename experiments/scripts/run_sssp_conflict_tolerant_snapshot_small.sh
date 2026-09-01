#!/usr/bin/env bash
set -euo pipefail

if [[ ${SSSP_SNAPSHOT_FROZEN_RUNNER:-0} != 1 ]]; then
    runner_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
    frozen_runner=$(mktemp /tmp/dx100-sssp-snapshot-small.XXXXXX.sh)
    cp -- "${BASH_SOURCE[0]}" "$frozen_runner"
    chmod 0555 "$frozen_runner"
    exec env SSSP_SNAPSHOT_FROZEN_RUNNER=1 \
        SSSP_SNAPSHOT_RUNNER_ROOT="$runner_root" \
        SSSP_SNAPSHOT_FROZEN_RUNNER_PATH="$frozen_runner" \
        "$frozen_runner" "$@"
fi
if [[ -n ${SSSP_SNAPSHOT_FROZEN_RUNNER_PATH:-} ]]; then
    trap 'rm -f -- "$SSSP_SNAPSHOT_FROZEN_RUNNER_PATH"' EXIT
fi

if [[ $# -ne 2 ]]; then
    echo "usage: $0 GEM5_BIN OUTDIR" >&2
    exit 2
fi

root=$(realpath "${SSSP_SNAPSHOT_RUNNER_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}")
gem5=$(realpath "$1")
out=$(realpath -m "$2")
variant=${SSSP_SNAPSHOT_VARIANT:-active_source}
case "$variant" in
all_safe|active_source|cross_owner) ;;
*) echo "invalid SSSP_SNAPSHOT_VARIANT: $variant" >&2; exit 2 ;;
esac

config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
source_file="$root/benchmarks/gapbs/src/sssp.cc"
helper_file="$root/benchmarks/gapbs/src/sssp_coherent_fallback.hh"
admission_file="$root/benchmarks/gapbs/src/sssp_chunk_admission.hh"
predictor_file="$root/experiments/tools/predict_sssp_chunk_admission.cc"
frozen_ramulator=/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/input/libramulator.so
frozen_ramulator_sha256=76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753

hash_value() { sha256sum "$1" | awk '{print $1}'; }
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
            if (!found) exit 2
            printf "%.0f\n", sum
            exit
        }
    ' "$stats"
}
terminal_value() {
    local line=$1 key=$2
    tr ' ' '\n' <<<"$line" | awk -F= -v key="$key" \
        '$1 == key {print substr($0, length(key) + 2)}'
}
json_total() {
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["totals"][sys.argv[2]])' \
        "$prediction" "$1"
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
guest="$out/bin/sssp_maa_2G_conflict_snapshot_fp"
host_reference="$out/bin/sssp_functional_fp"
predictor="$out/bin/predict_sssp_chunk_admission"
converter="$out/bin/converter"
wel="$out/graph/sssp_conflict_tolerant_snapshot_small.wel"
graph="$out/graph/sssp_conflict_tolerant_snapshot_small.wsg"
prediction="$out/graph/snapshot_prediction.json"

"${CXX:-g++}" -I"$root/benchmarks/gapbs/src" -std=c++11 -O3 \
    -Wall -Wextra -Werror -Wno-unused-parameter -fopenmp \
    "$root/benchmarks/gapbs/src/converter.cc" -o "$converter"
"${CXX:-g++}" -std=c++17 -O3 -Wall -Wextra -Werror \
    "$predictor_file" -o "$predictor"
"${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O3 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers -Wno-unused-parameter -fopenmp -DGEM5 -DMAA \
    -DNUM_CORES=4 -DNUM_TILES_PER_CORE=8 -DTILE_SIZE=16384 \
    -DMAA_CONSUMER_TILE_SIZE=4096 -DMAA_MEM_SIZE=0x80000000 \
    -DSSSP_FP_ENABLE=1 -DSSSP_OLD_RESULT_HYBRID=1 \
    -DSSSP_CONFLICT_TOLERANT_SNAPSHOT=1 \
    "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$guest"
"${CXX:-g++}" -I"$root/benchmarks/gapbs/src" \
    -I"$root/benchmarks/API" -I"$root/include" -std=c++11 -O3 \
    -Wall -Wextra -Werror -Wno-ignored-qualifiers \
    -Wno-unused-parameter -Wno-maybe-uninitialized -fopenmp \
    -DSSSP_FP_ENABLE=1 "$source_file" -o "$host_reference"
chmod 0555 "$guest" "$host_reference" "$predictor"

for ((u = 1; u <= 4096; ++u)); do
    printf '0 %d 1\n' "$u"
done >"$wel"
for ((u = 1; u <= 4096; ++u)); do
    base=$((4097 + (u - 1) * 16))
    for ((lane = 0; lane < 16; ++lane)); do
        destination=$((base + lane))
        if [[ $variant == active_source && $u -eq 1025 && $lane -eq 0 ]]; then
            destination=1
        elif [[ $variant == cross_owner && $lane -eq 0 &&
                ( $u -eq 1025 || $u -eq 2049 ) ]]; then
            destination=20481
        fi
        printf '%d %d 1\n' "$u" "$destination"
    done
done >>"$wel"
"$converter" -f "$wel" -w -b "$graph" >"$out/graph/converter.log" 2>&1
chmod 0444 "$graph"

case "$variant" in
all_safe)
    expected_graph_sha=3fc71246c10bb765d1f67ac15e9fb30561ca70a89a95f8104f85c91fd2954d23
    expected_tolerated=0
    expected_active_tolerated=0
    expected_cross_tolerated=0
    expected_snapshot_words=69632
    expected_fingerprint='SSSP_FINGERPRINT vertices=69633 reached=69633 unreachable=0 distance_sum=135168 max_distance=2 hash_a=a0531a7ddb9387df hash_b=39f1ea63bc8817e8 triangle_violations=0 missing_predecessors=0 nonpositive_weights=0 negative_distances=0 result=PASS'
    ;;
active_source)
    expected_graph_sha=fd9fa484aba9353155327bc42adbf635e00b543ddbb7d651f6d8be085530b009
    expected_tolerated=1
    expected_active_tolerated=1
    expected_cross_tolerated=0
    expected_snapshot_words=69631
    expected_fingerprint='SSSP_FINGERPRINT vertices=69633 reached=69632 unreachable=1 distance_sum=135166 max_distance=2 hash_a=24951adf631ff822 hash_b=1d2f7d2e3ed1aa0f triangle_violations=0 missing_predecessors=0 nonpositive_weights=0 negative_distances=0 result=PASS'
    ;;
cross_owner)
    expected_graph_sha=6db51b28b36f1da116c9b3b282d8a95539458afe9ebe840b1c89a9b4356ffa3b
    expected_tolerated=2
    expected_active_tolerated=0
    expected_cross_tolerated=2
    expected_snapshot_words=69631
    expected_fingerprint='SSSP_FINGERPRINT vertices=69633 reached=69632 unreachable=1 distance_sum=135166 max_distance=2 hash_a=4ab569558e397822 hash_b=005c7757503cab01 triangle_violations=0 missing_predecessors=0 nonpositive_weights=0 negative_distances=0 result=PASS'
    ;;
esac
require_hash "$graph" "$expected_graph_sha"

options="-f $graph -n 1 -r 0 -d 1 -v"
OMP_NUM_THREADS=4 "$host_reference" $options >"$out/graph/oracle.log" 2>&1
[[ $(grep -Fxc "$expected_fingerprint" "$out/graph/oracle.log") -eq 1 ]]
"$predictor" --input "$graph" --source 0 --delta 1 --threads 4 \
    --policy conflict-tolerant-snapshot --output "$prediction"
[[ $(json_total eligible_windows) -eq 4 ]]
[[ $(json_total routed_windows) -eq 4 ]]
[[ $(json_total unsafe_eligible_windows) -eq 0 ]]
[[ $(json_total tolerated_hazard_windows) -eq $expected_tolerated ]]
[[ $(json_total source_snapshot_words) -eq $expected_snapshot_words ]]

checkpoint_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" "$config"
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1
    --cmd "$guest" --options "$options"
)
restore_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/run" "$config"
    --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --checkpoint-dir="$out/checkpoint" --sys-clock 3.2GHz --cpu-clock 3.2GHz
    --caches --l1d_size=32kB --l1d_assoc=8
    --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8
    --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher
    --l1i_mshrs=16 --l1i_write_buffers=8 --l2cache --l2_size=256kB
    --l2_assoc=4 --l2-hwp-type=StridePrefetcher --l2_mshrs=32
    --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
    --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
    --cacheline_size=64 --mem-type Ramulator2 --ramulator-config "$ramulator"
    --mem-channels=2 --maa_ncbus_width=32 --maa --maa_num_maas=1
    --maa_num_indirect_units_per_maa=4 --maa_num_tile_elements=16384
    --maa_physical_tile_elements=4096 --maa_num_offset_table_entries=16384
    --maa_num_offset_table_epoch_entries=16384
    --maa_num_initial_row_table_slices=32 --maa_l2_uncacheable
    --maa_l3_uncacheable --maa_soa_jit_old_result_pressure_policy=densest
    --maa_soa_jit_old_result_partial_credits=4
    --maa_soa_jit_predicate_active_credits=1 --maa_soa_jit_active_contexts=8
    --maa_soa_jit_value_lookahead=1 --maa_soa_jit_value_cache_enable
    --maa_soa_jit_pre_a_value_lookahead --maa_soa_jit_value_prefetch_credits=0
    --maa_soa_jit_active_value_owners=64 --maa_soa_jit_apply_lanes=1
    --cmd "$guest" --options "$options"
)

{
    printf 'schema=dx100.sssp.conflict_tolerant_snapshot.small.v1\n'
    printf 'source_commit=%s\nsource_sha256=%s\n' \
        "$(git -C "$root" rev-parse HEAD)" "$(hash_value "$source_file")"
    printf 'helper_sha256=%s\nchunk_admission_sha256=%s\n' \
        "$(hash_value "$helper_file")" "$(hash_value "$admission_file")"
    printf 'predictor_sha256=%s\nguest_sha256=%s\n' \
        "$(hash_value "$predictor_file")" "$(hash_value "$guest")"
    printf 'gem5_path=%s\ngem5_sha256=%s\n' "$gem5" "$(hash_value "$gem5")"
    printf 'ramulator_library_path=%s\nramulator_library_sha256=%s\n' \
        "$frozen_ramulator" "$frozen_ramulator_sha256"
    printf 'variant=%s\ngraph_sha256=%s\n' "$variant" "$expected_graph_sha"
    printf 'prototype_policy=conflict-tolerant-snapshot\n'
    printf 'expected_routed_windows=4\nexpected_unsafe_windows=0\n'
    printf 'expected_tolerated_hazard_windows=%s\n' "$expected_tolerated"
    printf 'expected_source_snapshot_words=%s\n' "$expected_snapshot_words"
    printf 'oracle_fingerprint=%s\n' "$expected_fingerprint"
    printf 'native_arms=0\nwall_timeout=none\nfull_graph=false\n'
} >"$out/manifest.txt"

sha256sum "$gem5" "$frozen_ramulator" "$guest" "$host_reference" \
    "$predictor" "$graph" "$source_file" "$helper_file" "$admission_file" \
    "$predictor_file" "$config" "$ramulator" "$0" \
    >"$out/artifacts.before.sha256"

OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${checkpoint_cmd[@]}" \
    >"$out/checkpoint.log" 2>&1
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
          "$out/checkpoint.log" || true) -eq 1 ]]
OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${restore_cmd[@]}" \
    >"$out/run/restore.log" 2>&1

restore="$out/run/restore.log"
stats="$out/run/stats.txt"
[[ $(grep -Fxc "$expected_fingerprint" "$restore" || true) -eq 1 ]]
[[ $(grep -Ec '^SSSP_OLD_RESULT_HYBRID_TERMINAL ' "$restore" || true) -eq 1 ]]
terminal=$(grep '^SSSP_OLD_RESULT_HYBRID_TERMINAL ' "$restore")
for expected in \
    treatment=conflict_tolerant_snapshot eligible_windows=4 routed_windows=4 \
    unsafe_eligible_windows=0 reason_covered_unsafe_windows=0 \
    bounds_rejected_windows=0 active_source_rejected_windows=0 \
    cross_owner_rejected_windows=0 \
    tolerated_hazard_windows="$expected_tolerated" \
    active_source_observed_windows="$expected_active_tolerated" \
    cross_owner_observed_windows="$expected_cross_tolerated" \
    active_source_tolerated_windows="$expected_active_tolerated" \
    cross_owner_tolerated_windows="$expected_cross_tolerated" \
    source_snapshot_words="$expected_snapshot_words" \
    source_snapshot_bytes="$((expected_snapshot_words * 4))" \
    source_snapshot_barriers=2 \
    external_snapshot_capacity_words=69632 \
    external_snapshot_capacity_bytes=278528 \
    snapshot_backing=ordinary_coherent_external snapshot_hidden_sram_bytes=0 \
    snapshot_lifetime_closure=1 index_publish_pages=16 value_publish_pages=16 \
    old_result_words=65536 legacy_words=0 fallback_pages=0 \
    fallback_publication_issue_pages=0 fallback_publication_response_pages=0 \
    fallback_publication_words=0 fallback_publication_bytes=0 \
    fallback_consumed_words=0 predicate_restore_words=0 \
    coherent_tail_batches=0 coherent_tail_words=0 \
    logical_reorder_words=16384 physical_spd_words=4096 row_table_slices=32 \
    duplicate_order=legacy_physical_pages host_spd_reads=0 \
    max_host_spd_element=-1 illegal_host_spd_line_starts=0 \
    new_dedicated_payload_bytes=0 hidden_logical_spd_bytes=0 \
    hidden_result_payload_bytes=0 response_closure=1 counts_close=1; do
    key=${expected%%=*}
    value=${expected#*=}
    [[ $(terminal_value "$terminal" "$key") == "$value" ]]
done
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
[[ $instructions -eq 4 && $terminals -eq 4 ]]
[[ $selected -eq 65536 && $rejected -eq 0 && $captures -eq 65536 ]]
[[ $issues -gt 0 && $issues -eq $responses ]]
[[ $a_reads -gt 0 && $a_reads -eq $a_read_responses ]]
[[ $a_reads -eq $a_writes && $a_writes -eq $a_write_responses ]]

sha256sum "$gem5" "$frozen_ramulator" "$guest" "$host_reference" \
    "$predictor" "$graph" "$source_file" "$helper_file" "$admission_file" \
    "$predictor_file" "$config" "$ramulator" "$0" \
    >"$out/artifacts.after.sha256"
cmp -s "$out/artifacts.before.sha256" "$out/artifacts.after.sha256"
sim_ticks=$(awk '$1 == "simTicks" {print $2; exit}' "$stats")
[[ $sim_ticks =~ ^[1-9][0-9]*$ ]]
{
    printf 'terminal=true\ncorrect=true\nvariant=%s\n' "$variant"
    printf 'simTicks=%s\nrouted_windows=4\nunsafe_windows=0\n' "$sim_ticks"
    printf 'tolerated_hazard_windows=%s\n' "$expected_tolerated"
    printf 'source_snapshot_words=%s\nold_result_captures=%s\n' \
        "$expected_snapshot_words" "$captures"
    printf 'old_result_write_issues=%s\nold_result_write_responses=%s\n' \
        "$issues" "$responses"
} >"$out/result.txt"
touch "$out/gate.complete"
cat "$out/result.txt"
echo "SSSP_CONFLICT_TOLERANT_SNAPSHOT_SMALL_PASS"
