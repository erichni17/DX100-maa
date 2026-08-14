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
cxx=${CXX:-g++}

[[ -x $gem5 ]] || { echo "missing gem5 binary: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
git -C "$root" diff --quiet --exit-code
git -C "$root" diff --cached --quiet --exit-code
mkdir -p "$out/bin" "$out/checkpoint" "$out/runs"

build_guest() {
    local tile=$1
    local built="$out/bin/test_hybrid_rmw_soa_T${tile}"
    "$cxx" -I"$root/benchmarks/API" -I"$root/include" \
        -I"$root/util/m5/src" -std=c++11 -O2 -Wall -Wextra -Werror \
        -Wno-ignored-qualifiers -DGEM5 -DTILE_SIZE="$tile" \
        -DNUM_CORES=4 -DMAA_MEM_SIZE=0x80000000 \
        "$root/util/m5/src/abi/x86/m5op.S" \
        "$root/benchmarks/API/test_hybrid_rmw_soa.cpp" -o "$built"
    printf '%s\n' "$built"
}

binary16=$(build_guest 16384)
binary4=$(build_guest 4096)

make_checkpoint() {
    local name=$1
    local binary=$2
    local mode=$3
    local checkpoint_dir="$out/checkpoint/$name"
    mkdir -p "$checkpoint_dir"
    timeout 300 "$gem5" --listener-mode=off --outdir="$checkpoint_dir" \
        "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
        --max-checkpoints=1 --cmd "$binary" --options "$mode" \
        >"$checkpoint_dir/checkpoint.log" 2>&1
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
              "$checkpoint_dir/checkpoint.log" || true) -eq 1 ]] || {
        echo "$name did not produce one exact checkpoint" >&2
        exit 1
    }
}

make_checkpoint ordinary16 "$binary16" ordinary
make_checkpoint ordinary4 "$binary4" ordinary
make_checkpoint soa16 "$binary16" soa

source_commit=$(git -C "$root" rev-parse HEAD)
gem5_sha=$(sha256sum "$gem5" | awk '{print $1}')
guest16_sha=$(sha256sum "$binary16" | awk '{print $1}')
guest4_sha=$(sha256sum "$binary4" | awk '{print $1}')
printf 'source_commit=%s\ngem5_sha256=%s\nguest16_sha256=%s\n' \
    "$source_commit" "$gem5_sha" "$guest16_sha" >"$out/manifest.txt"
printf 'guest4_sha256=%s\n' "$guest4_sha" >>"$out/manifest.txt"
printf '%s\n' \
    'fixed_context_slots=8' \
    'fixed_lookahead_slots_per_context=8' \
    'fixed_value_owner_pool_lines=32' \
    'fixed_apply_lanes=4' \
    'default_active_apply_lanes=1' \
    'fixed_predicate_lines=16' \
    'default_active_predicate_credits=1' \
    'active_value_prefetch_credits=0' \
    'index_treatment_max_active_lines=8' \
    'index_feeder_is_pre_existing_dynamic_implementation_state=true' \
    >>"$out/manifest.txt"

stat_sum() {
    local stats=$1
    local suffix=$2
    awk -v suffix="$suffix" '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ ("_" suffix "$") { sum += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            printf "%.0f\n", sum
            exit
        }
    ' "$stats"
}

header=$'arm\tmode\tlogical\tphysical\tcontexts\tindex_lines'\
$'\tlookahead\tactive_value_owners\tvalue_cache\tapply_lanes\tsimTicks\toutput_hash'\
$'\tfills\tcached_responses\thits\tmerged\tevictions\tdeliveries'\
$'\tvalue_stalls\tlookahead_stalls\tcontext_stalls\tcontext_hwm'\
$'\tcache_hwm\tlookahead_hwm'
printf '%s\n' "$header" >"$out/matrix.tsv"
printf '%s\n' "$header" >"$out/controls.tsv"

declare -A hashes
declare -A ticks

common_command() {
    local -n target=$1
    local run=$2
    local checkpoint=$3
    local logical=$4
    local physical=$5
    local binary=$6
    target=(
        "$gem5" --listener-mode=off --outdir="$run"
        "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
        --checkpoint-dir="$checkpoint"
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
        --maa_num_tile_elements="$logical"
        --maa_physical_tile_elements="$physical"
        --maa_num_offset_table_entries="$logical"
        --maa_num_offset_table_epoch_entries="$logical"
        --maa_num_initial_row_table_slices=16
        --cmd "$binary"
    )
}

validate_process() {
    local run=$1
    [[ $(grep -Fxc 'ROI Ended' "$run/restore.log" || true) -eq 1 ]]
    [[ $(grep -Ec \
          '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
          "$run/restore.log" || true) -eq 1 ]]
    [[ $(grep -Eic \
          'panic|fatal|assert|abort|segmentation fault|error:' \
          "$run/restore.log" || true) -eq 0 ]]
}

record_result() {
    local arm=$1
    local run=$2
    local result=$3
    local guest_sha=$4
    local output_hash sim_ticks config_sha
    output_hash=$(sed -n \
        's/.* output_hash=\([0-9][0-9]*\).*/\1/p' <<<"$result")
    sim_ticks=$(awk '$1 == "simTicks" { print $2; exit }' \
        "$run/stats.txt")
    config_sha=$(sha256sum "$run/config.ini" | awk '{print $1}')
    [[ $output_hash =~ ^[0-9]+$ && $sim_ticks =~ ^[1-9][0-9]*$ ]]
    hashes[$arm]=$output_hash
    ticks[$arm]=$sim_ticks
    printf '%s\n' \
        "source_commit=$source_commit" \
        "gem5_sha256=$gem5_sha" \
        "guest_sha256=$guest_sha" \
        "config_sha256=$config_sha" \
        "simTicks=$sim_ticks" \
        "output_hash=$output_hash" >"$run/provenance.txt"
}

run_native() {
    local arm=$1
    local logical=$2
    local physical=$3
    local checkpoint_name=$4
    local binary=$5
    local guest_sha=$6
    local run="$out/runs/$arm"
    mkdir -p "$run"
    local command
    common_command command "$run" "$out/checkpoint/$checkpoint_name" \
        "$logical" "$physical" "$binary"
    command+=(--maa_virtual_index_buffer_lines=1
        --maa_soa_jit_active_contexts=1
        --maa_soa_jit_value_lookahead=1)
    printf '%q ' "${command[@]}" >"$run/command.txt"
    printf '\n' >>"$run/command.txt"
    timeout 1800 "${command[@]}" >"$run/restore.log" 2>&1
    validate_process "$run"
    local result instructions terminal
    result=$(grep -E '^HYBRID_RMW_SOA_RESULT ' "$run/restore.log" || true)
    [[ $(grep -Ec '^HYBRID_RMW_SOA_RESULT ' "$run/restore.log" || true) \
          -eq 1 &&
       $result =~ (^|[[:space:]])mode=ordinary($|[[:space:]]) &&
       $result =~ (^|[[:space:]])logical=$logical($|[[:space:]]) &&
       $result =~ (^|[[:space:]])operations=2($|[[:space:]]) &&
       $result =~ (^|[[:space:]])errors=0($|[[:space:]]) ]]
    instructions=$(stat_sum "$run/stats.txt" IND_SoaJitInstructions)
    terminal=$(stat_sum "$run/stats.txt" IND_SoaJitTerminalCompletions)
    [[ $instructions -eq 0 && $terminal -eq 0 ]]
    record_result "$arm" "$run" "$result" "$guest_sha"
    printf '%s\tordinary\t%s\t%s\t0\t0\t0\t0\t0\t0\t%s\t%s' \
        "$arm" "$logical" "$physical" "${ticks[$arm]}" "${hashes[$arm]}" \
        >>"$out/controls.tsv"
    printf '\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\n' \
        >>"$out/controls.tsv"
}

run_soa() {
    local arm=$1
    local physical=$2
    local contexts=$3
    local index_lines=$4
    local lookahead=$5
    local cache_enable=$6
    local owners=$7
    local lanes=$8
    local table=$9
    local run="$out/runs/$arm"
    mkdir -p "$run"
    local command
    common_command command "$run" "$out/checkpoint/soa16" 16384 \
        "$physical" "$binary16"
    command=("${command[@]:0:3}"
        --debug-flags=MAAVirtualTrace --debug-file=soa_jit_trace.log
        "${command[@]:3}")
    command+=(--maa_virtual_index_buffer_lines="$index_lines"
        --maa_soa_jit_active_contexts="$contexts"
        --maa_soa_jit_value_lookahead="$lookahead"
        --maa_soa_jit_active_value_owners="$owners"
        --maa_soa_jit_apply_lanes="$lanes")
    if [[ $cache_enable -eq 1 ]]; then
        command+=(--maa_soa_jit_value_cache_enable)
    fi
    printf '%q ' "${command[@]}" >"$run/command.txt"
    printf '\n' >>"$run/command.txt"
    timeout 1800 "${command[@]}" >"$run/restore.log" 2>&1
    validate_process "$run"

    local result expected_cache=false
    result=$(grep -E '^HYBRID_RMW_SOA_RESULT ' "$run/restore.log" || true)
    [[ $(grep -Ec '^HYBRID_RMW_SOA_RESULT ' "$run/restore.log" || true) \
          -eq 1 &&
       $result =~ (^|[[:space:]])mode=soa($|[[:space:]]) &&
       $result =~ (^|[[:space:]])logical=16384($|[[:space:]]) &&
       $result =~ (^|[[:space:]])operations=2($|[[:space:]]) &&
       $result =~ (^|[[:space:]])errors=0($|[[:space:]]) ]]
    [[ $cache_enable -eq 0 ]] || expected_cache=true
    for resolved in \
        'num_tile_elements=16384' \
        "physical_tile_elements=$physical" \
        'num_offset_table_entries=16384' \
        'num_offset_table_epoch_entries=16384' \
        "virtual_index_buffer_lines=$index_lines" \
        "soa_jit_active_contexts=$contexts" \
        "soa_jit_value_lookahead=$lookahead" \
        "soa_jit_active_value_owners=$owners" \
        "soa_jit_apply_lanes=$lanes" \
        "soa_jit_value_cache_enable=$expected_cache"; do
        grep -Fqx "$resolved" "$run/config.ini" || {
            echo "$arm missing resolved configuration: $resolved" >&2
            exit 1
        }
    done

    local instructions selected rejected predicate_issues predicate_responses
    local value_issues value_responses fills cached hits merged evictions
    local deliveries value_stalls lookahead_issues lookahead_responses
    local lookahead_stalls lookahead_hwm aliases a_read_issues a_read_responses
    local write_issues write_responses active active_owners active_lanes
    local apply_hwm context_hwm context_stalls
    local cache_hwm terminal
    instructions=$(stat_sum "$run/stats.txt" IND_SoaJitInstructions)
    selected=$(stat_sum "$run/stats.txt" IND_SoaJitSelected)
    rejected=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateRejected)
    predicate_issues=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateLineReads)
    predicate_responses=$(stat_sum "$run/stats.txt" \
        IND_SoaJitPredicateLineResponses)
    value_issues=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadIssues)
    value_responses=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadResponses)
    fills=$(stat_sum "$run/stats.txt" IND_SoaJitValueFills)
    cached=$(stat_sum "$run/stats.txt" IND_SoaJitValueCachedResponses)
    hits=$(stat_sum "$run/stats.txt" IND_SoaJitValueHits)
    merged=$(stat_sum "$run/stats.txt" IND_SoaJitValueMergedWaiters)
    evictions=$(stat_sum "$run/stats.txt" IND_SoaJitValueEvictions)
    deliveries=$(stat_sum "$run/stats.txt" IND_SoaJitValueDeliveries)
    value_stalls=$(stat_sum "$run/stats.txt" IND_SoaJitValueStalls)
    cache_hwm=$(stat_sum "$run/stats.txt" IND_SoaJitValueCacheHighWater)
    lookahead_issues=$(stat_sum "$run/stats.txt" IND_SoaJitLookaheadIssues)
    lookahead_responses=$(stat_sum "$run/stats.txt" \
        IND_SoaJitLookaheadResponses)
    lookahead_stalls=$(stat_sum "$run/stats.txt" IND_SoaJitLookaheadStalls)
    lookahead_hwm=$(stat_sum "$run/stats.txt" IND_SoaJitLookaheadHighWater)
    active=$(stat_sum "$run/stats.txt" IND_SoaJitActiveContexts)
    active_owners=$(stat_sum "$run/stats.txt" IND_SoaJitActiveValueOwners)
    active_lanes=$(stat_sum "$run/stats.txt" IND_SoaJitActiveApplyLanes)
    apply_hwm=$(stat_sum "$run/stats.txt" IND_SoaJitApplyLaneHighWater)
    aliases=$(stat_sum "$run/stats.txt" IND_SoaJitAliasesApplied)
    a_read_issues=$(stat_sum "$run/stats.txt" IND_SoaJitAReadIssues)
    a_read_responses=$(stat_sum "$run/stats.txt" IND_SoaJitAReadResponses)
    write_issues=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteIssues)
    write_responses=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteResponses)
    context_hwm=$(stat_sum "$run/stats.txt" IND_SoaJitContextHighWater)
    context_stalls=$(stat_sum "$run/stats.txt" IND_SoaJitContextStalls)
    terminal=$(stat_sum "$run/stats.txt" IND_SoaJitTerminalCompletions)
    [[ $instructions -eq 2 && $terminal -eq 2 &&
       $selected -gt 0 && $rejected -gt 0 &&
       $((selected + rejected)) -eq 32768 &&
       $predicate_issues -gt 0 &&
       $predicate_issues -eq $predicate_responses &&
       $value_issues -eq $value_responses &&
       $value_responses -eq $fills && $cached -le $fills &&
       $((fills + hits + merged)) -eq $lookahead_issues &&
       $lookahead_issues -eq $selected &&
       $lookahead_responses -eq $selected &&
       $deliveries -eq $selected && $aliases -eq $selected &&
       $a_read_issues -gt 0 && $a_read_issues -eq $a_read_responses &&
       $a_read_issues -eq $write_issues &&
       $write_issues -eq $write_responses &&
       $active -eq $((2 * contexts)) &&
       $active_owners -eq $((2 * owners)) &&
       $active_lanes -eq $((2 * lanes)) &&
       $apply_hwm -ge 2 && $apply_hwm -le $((2 * lanes)) &&
       $context_hwm -ge 2 && $context_hwm -le $((2 * contexts)) &&
       $cache_hwm -ge 2 && $cache_hwm -le "$owners" &&
       $lookahead_hwm -ge 2 &&
       $lookahead_hwm -le $((2 * contexts * lookahead)) ]] || {
        echo "$arm failed exact SoA/JIT overlap closure" >&2
        exit 1
    }
    if [[ $lanes -gt 1 && $apply_hwm -le 2 ]]; then
        echo "$arm did not exercise independent same-cycle apply lanes" >&2
        exit 1
    fi

    local terminal_records generations
    terminal_records=$(grep -Ec \
        "event=soa_jit_complete .*schema=2 .*apply_lanes=$lanes .*apply_hwm=[1-4] .*active_value_owners=$owners .*max_value_owners=32 .*context_slots=8 .*lookahead_slots_per_context=8 .*terminal=1" \
        "$run/soa_jit_trace.log" || true)
    generations=$(awk '
        /event=soa_jit_complete/ && /terminal=1/ {
            for (i = 1; i <= NF; ++i)
                if ($i ~ /^generation=/) seen[$i] = 1
        }
        END { for (generation in seen) count++; print count + 0 }
    ' "$run/soa_jit_trace.log")
    [[ $terminal_records -eq 2 && $generations -eq 2 ]]
    grep -Eq "event=soa_jit_complete .*active_contexts=$contexts " \
        "$run/soa_jit_trace.log"
    grep -Eq "event=soa_jit_complete .*active_lookahead=$lookahead " \
        "$run/soa_jit_trace.log"
    grep -Eq "event=soa_jit_complete .*cache_enable=$cache_enable " \
        "$run/soa_jit_trace.log"
    grep -Eq "event=soa_jit_complete .*active_value_owners=$owners " \
        "$run/soa_jit_trace.log"
    if [[ $lanes -gt 1 ]]; then
        grep -Eq "event=soa_jit_complete .*apply_hwm=[2-4] " \
            "$run/soa_jit_trace.log"
    fi
    [[ $(grep -Ec \
          "event=soa_jit_storage .*fixed_contexts=8 .*max_physical_value_owner_lines=32 .*fixed_apply_lanes=4 active_apply_lanes=$lanes .*fixed_predicate_lines=16 .*predicate_active_credits=1 .*active_value_owners=$owners " \
          "$run/soa_jit_trace.log" || true) -eq 2 ]]
    grep -m1 'event=soa_jit_storage ' "$run/soa_jit_trace.log" \
        >"$run/storage_ledger.txt"
    record_result "$arm" "$run" "$result" "$guest16_sha"
    printf '%s\tsoa\t16384\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' \
        "$arm" "$physical" "$contexts" "$index_lines" "$lookahead" \
        "$owners" "$cache_enable" "$lanes" "${ticks[$arm]}" \
        "${hashes[$arm]}" >>"$table"
    printf '\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$fills" "$cached" "$hits" "$merged" "$evictions" \
        "$deliveries" "$value_stalls" "$lookahead_stalls" \
        "$context_stalls" "$context_hwm" "$cache_hwm" "$lookahead_hwm" \
        >>"$table"
}

run_native ordinary_native16 16384 16384 ordinary16 \
    "$binary16" "$guest16_sha"
run_native ordinary_native4 4096 4096 ordinary4 "$binary4" "$guest4_sha"
run_soa soa_serial_physical16 16384 1 1 1 0 4 1 "$out/controls.tsv"
run_soa baseline_c1_i1_l1_v4 4096 1 1 1 0 4 1 "$out/matrix.tsv"
run_soa lookahead4_c1_i8_l4_v4 4096 1 8 4 1 4 1 "$out/matrix.tsv"
run_soa lookahead8_c1_i8_l8_v4 4096 1 8 8 1 4 1 "$out/matrix.tsv"
run_soa combined_c8_i8_l8_v4 4096 8 8 8 1 4 1 "$out/matrix.tsv"
run_soa combined_c8_i8_l8_v8 4096 8 8 8 1 8 1 "$out/matrix.tsv"
run_soa combined_c8_i8_l8_v16 4096 8 8 8 1 16 1 "$out/matrix.tsv"
run_soa combined_c8_i8_l8_v32 4096 8 8 8 1 32 1 "$out/matrix.tsv"
run_soa apply2_c8_i8_l8_v32 4096 8 8 8 1 32 2 "$out/matrix.tsv"
run_soa apply4_c8_i8_l8_v32 4096 8 8 8 1 32 4 "$out/matrix.tsv"

reference=${hashes[ordinary_native16]}
for arm in ordinary_native4 soa_serial_physical16 baseline_c1_i1_l1_v4 \
           lookahead4_c1_i8_l4_v4 lookahead8_c1_i8_l8_v4 \
           combined_c8_i8_l8_v4 combined_c8_i8_l8_v8 \
           combined_c8_i8_l8_v16 combined_c8_i8_l8_v32 \
           apply2_c8_i8_l8_v32 apply4_c8_i8_l8_v32; do
    [[ ${hashes[$arm]} == "$reference" ]] || {
        echo "exact output hash mismatch at $arm" >&2
        exit 1
    }
done

{
    printf 'baseline_simTicks=%s\n' "${ticks[baseline_c1_i1_l1_v4]}"
    for arm in lookahead4_c1_i8_l4_v4 lookahead8_c1_i8_l8_v4 \
               combined_c8_i8_l8_v4 combined_c8_i8_l8_v8 \
               combined_c8_i8_l8_v16 combined_c8_i8_l8_v32 \
               apply2_c8_i8_l8_v32 apply4_c8_i8_l8_v32; do
        awk -v arm="$arm" -v base="${ticks[baseline_c1_i1_l1_v4]}" \
            -v candidate="${ticks[$arm]}" 'BEGIN {
                printf "%s_simTicks=%s\n", arm, candidate
                printf "%s_speedup_vs_baseline=%.9f\n", arm, \
                       base / candidate
            }'
    done
    printf '%s\n' 'speedups_are_derived_from_measured_simTicks'
} >"$out/attribution.txt"

{
    printf '%s\n' \
        'ledger_scope=compiled_C++_object_and_modeled_index_data_tag_bytes' \
        'allocator_and_std_map_node_overhead=software_only_not_hardware_modeled' \
        'fixed_provisioned_fields_do_not_change_across_SoA_arms'
    sed -n 's/.*event=soa_jit_storage /event=soa_jit_storage /p' \
        "$out/runs/baseline_c1_i1_l1_v4/storage_ledger.txt"
    sed -n 's/.*event=soa_jit_storage /event=soa_jit_storage /p' \
        "$out/runs/combined_c8_i8_l8_v4/storage_ledger.txt"
    sed -n 's/.*event=soa_jit_storage /event=soa_jit_storage /p' \
        "$out/runs/combined_c8_i8_l8_v32/storage_ledger.txt"
    sed -n 's/.*event=soa_jit_storage /event=soa_jit_storage /p' \
        "$out/runs/apply4_c8_i8_l8_v32/storage_ledger.txt"
} >"$out/storage_ledger.txt"

cat "$out/matrix.tsv"
cat "$out/controls.tsv"
cat "$out/attribution.txt"
cat "$out/storage_ledger.txt"
echo "HYBRID_RMW_SOA_OVERLAP_MATRIX_PASS"
