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
[[ -z $(git -C "$root" status --porcelain) ]] || {
    echo "source worktree must be clean for provenance" >&2
    exit 2
}

mkdir -p "$out/bin" "$out/checkpoint/soa16" "$out/runs"
guest="$out/bin/test_hybrid_rmw_soa_T16384"
"$cxx" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O2 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers -DGEM5 -DTILE_SIZE=16384 \
    -DNUM_CORES=4 -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/src/abi/x86/m5op.S" \
    "$root/benchmarks/API/test_hybrid_rmw_soa.cpp" -o "$guest"

make_checkpoint() {
    timeout 300 "$gem5" --listener-mode=off \
        --outdir="$out/checkpoint/soa16" "$config" \
        --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
        --max-checkpoints=1 --cmd "$guest" --options=soa \
        >"$out/checkpoint/soa16/checkpoint.log" 2>&1
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
              "$out/checkpoint/soa16/checkpoint.log" || true) -eq 1 ]] || {
        echo "shared SoA checkpoint did not close exactly" >&2
        exit 1
    }
}

make_checkpoint

source_commit=$(git -C "$root" rev-parse HEAD)
gem5_sha=$(sha256sum "$gem5" | awk '{print $1}')
guest_sha=$(sha256sum "$guest" | awk '{print $1}')
checkpoint_state=$(find "$out/checkpoint/soa16" -mindepth 2 -maxdepth 2 \
    -type f -path '*/cpt.*/m5.cpt' -print)
[[ $(wc -l <<<"$checkpoint_state") -eq 1 ]] || {
    echo "shared SoA checkpoint state is not unique" >&2
    exit 1
}
checkpoint_sha=$(sha256sum "$checkpoint_state" | \
    awk '{print $1}')
{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'gem5_sha256=%s\n' "$gem5_sha"
    printf 'guest_sha256=%s\n' "$guest_sha"
    printf 'shared_checkpoint_m5_cpt_sha256=%s\n' "$checkpoint_sha"
    printf '%s\n' \
        'logical_tile_elements=16384' \
        'metadata_entries=16384' \
        'physical_tile_elements=4096' \
        'predicate_active_credits=16' \
        'index_buffer_lines=4' \
        'active_contexts=32' \
        'value_lookahead=8' \
        'value_cache_enable=true' \
        'active_value_owners=32' \
        'apply_lanes=1' \
        'sequential_value_prefetch_credits=0' \
        'replicas=2'
} >"$out/manifest.txt"

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

declare -A ticks hashes selected rejected value_issues
declare -A a_read_issues a_read_responses write_issues write_responses

header=$'arm\treplica\tpre_a\tsimTicks\toutput_hash\tselected\trejected'\
$'\tvalue_read_issues\tvalue_read_responses\ta_read_issues'\
$'\ta_read_responses\ta_write_issues\ta_write_responses'\
$'\tpre_a_issues\tpre_a_ready_at_a_response\tpre_a_uses'\
$'\tcontext_stalls\tlookahead_stalls'
printf '%s\n' "$header" >"$out/matrix.tsv"

run_arm() {
    local name=$1
    local arm=$2
    local replica=$3
    local run="$out/runs/$name"
    mkdir -p "$run"
    local command=(
        "$gem5" --listener-mode=off --outdir="$run"
        "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
        --checkpoint-dir="$out/checkpoint/soa16"
        --sys-clock 3.2GHz --cpu-clock 3.2GHz
        --caches --l1d_size=32kB --l1d_assoc=8 --l1d_mshrs=16
        --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8
        --l1i_mshrs=16 --l1i_write_buffers=8
        --l2cache --l2_size=256kB --l2_assoc=4 --l2_mshrs=32
        --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
        --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
        --cacheline_size=64 --mem-type=Ramulator2
        --ramulator-config="$ramulator" --mem-channels=1
        --maa --maa_num_maas=1 --maa_num_indirect_units_per_maa=1
        --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096
        --maa_num_offset_table_entries=16384
        --maa_num_offset_table_epoch_entries=16384
        --maa_num_initial_row_table_slices=16
        --maa_soa_jit_predicate_active_credits=16
        --maa_virtual_index_buffer_lines=4
        --maa_soa_jit_active_contexts=32
        --maa_soa_jit_value_lookahead=8
        --maa_soa_jit_value_cache_enable
        --maa_soa_jit_value_prefetch_credits=0
        --maa_soa_jit_active_value_owners=32
        --maa_soa_jit_apply_lanes=1
        --cmd="$guest"
    )
    if [[ $arm == treatment ]]; then
        command+=(--maa_soa_jit_pre_a_value_lookahead)
    fi
    printf '%q ' "${command[@]}" >"$run/command.txt"
    printf '\n' >>"$run/command.txt"
    timeout 1800 "${command[@]}" >"$run/restore.log" 2>&1

    [[ $(grep -Fxc 'ROI Ended' "$run/restore.log" || true) -eq 1 ]]
    [[ $(grep -Ec \
          '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
          "$run/restore.log" || true) -eq 1 ]]
    [[ $(grep -Eic \
          'panic|fatal|assert|abort|segmentation fault|error:' \
          "$run/restore.log" || true) -eq 0 ]]

    local result
    result=$(grep -E '^HYBRID_RMW_SOA_RESULT ' "$run/restore.log" || true)
    [[ $(grep -Ec '^HYBRID_RMW_SOA_RESULT ' "$run/restore.log" || true) \
          -eq 1 &&
       $result =~ (^|[[:space:]])mode=soa($|[[:space:]]) &&
       $result =~ (^|[[:space:]])logical=16384($|[[:space:]]) &&
       $result =~ (^|[[:space:]])operations=2($|[[:space:]]) &&
       $result =~ (^|[[:space:]])errors=0($|[[:space:]]) ]]

    local expected_pre_a=false
    [[ $arm == control ]] || expected_pre_a=true
    for resolved in \
        'num_tile_elements=16384' \
        'physical_tile_elements=4096' \
        'num_offset_table_entries=16384' \
        'num_offset_table_epoch_entries=16384' \
        'soa_jit_predicate_active_credits=16' \
        'virtual_index_buffer_lines=4' \
        'soa_jit_active_contexts=32' \
        'soa_jit_value_lookahead=8' \
        'soa_jit_value_cache_enable=true' \
        'soa_jit_value_prefetch_credits=0' \
        'soa_jit_active_value_owners=32' \
        'soa_jit_apply_lanes=1' \
        "soa_jit_pre_a_value_lookahead=$expected_pre_a"; do
        grep -Fqx "$resolved" "$run/config.ini" || {
            echo "$name missing resolved configuration: $resolved" >&2
            exit 1
        }
    done

    local instructions terminal predicate_issues predicate_responses
    local value_responses fills hits merged deliveries
    local lookahead_issues lookahead_responses aliases
    local pre_a_issues pre_a_ready pre_a_uses
    local prefetch_issues prefetch_responses prefetch_promotions
    local prefetch_discards context_stalls lookahead_stalls
    instructions=$(stat_sum "$run/stats.txt" IND_SoaJitInstructions)
    terminal=$(stat_sum "$run/stats.txt" IND_SoaJitTerminalCompletions)
    selected[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitSelected)
    rejected[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateRejected)
    predicate_issues=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateLineReads)
    predicate_responses=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateLineResponses)
    value_issues[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadIssues)
    value_responses=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadResponses)
    fills=$(stat_sum "$run/stats.txt" IND_SoaJitValueFills)
    hits=$(stat_sum "$run/stats.txt" IND_SoaJitValueHits)
    merged=$(stat_sum "$run/stats.txt" IND_SoaJitValueMergedWaiters)
    deliveries=$(stat_sum "$run/stats.txt" IND_SoaJitValueDeliveries)
    lookahead_issues=$(stat_sum "$run/stats.txt" IND_SoaJitLookaheadIssues)
    lookahead_responses=$(stat_sum "$run/stats.txt" IND_SoaJitLookaheadResponses)
    aliases=$(stat_sum "$run/stats.txt" IND_SoaJitAliasesApplied)
    a_read_issues[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitAReadIssues)
    a_read_responses[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitAReadResponses)
    write_issues[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteIssues)
    write_responses[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteResponses)
    pre_a_issues=$(stat_sum "$run/stats.txt" IND_SoaJitPreAValueIssues)
    pre_a_ready=$(stat_sum "$run/stats.txt" IND_SoaJitPreAValueReadyAtAResponse)
    pre_a_uses=$(stat_sum "$run/stats.txt" IND_SoaJitPreAValueUses)
    prefetch_issues=$(stat_sum "$run/stats.txt" IND_SoaJitValuePrefetchIssues)
    prefetch_responses=$(stat_sum "$run/stats.txt" IND_SoaJitValuePrefetchResponses)
    prefetch_promotions=$(stat_sum "$run/stats.txt" IND_SoaJitValuePrefetchPromotions)
    prefetch_discards=$(stat_sum "$run/stats.txt" IND_SoaJitValuePrefetchDiscards)
    context_stalls=$(stat_sum "$run/stats.txt" IND_SoaJitContextStalls)
    lookahead_stalls=$(stat_sum "$run/stats.txt" IND_SoaJitLookaheadStalls)

    [[ $instructions -eq 2 && $terminal -eq 2 &&
       ${selected[$name]} -gt 0 && ${rejected[$name]} -gt 0 &&
       $((selected[$name] + rejected[$name])) -eq 32768 &&
       $predicate_issues -eq $predicate_responses &&
       ${value_issues[$name]} -eq $value_responses &&
       $value_responses -eq $fills &&
       $((fills + hits + merged)) -eq $lookahead_issues &&
       $lookahead_issues -eq ${selected[$name]} &&
       $lookahead_responses -eq ${selected[$name]} &&
       $deliveries -eq ${selected[$name]} && $aliases -eq ${selected[$name]} &&
       ${a_read_issues[$name]} -eq ${a_read_responses[$name]} &&
       ${a_read_issues[$name]} -eq ${write_issues[$name]} &&
       ${write_issues[$name]} -eq ${write_responses[$name]} &&
       $prefetch_issues -eq 0 && $prefetch_responses -eq 0 &&
       $prefetch_promotions -eq 0 && $prefetch_discards -eq 0 ]]
    if [[ $arm == control ]]; then
        [[ $pre_a_issues -eq 0 && $pre_a_ready -eq 0 && $pre_a_uses -eq 0 ]]
    else
        [[ $pre_a_issues -gt 0 && $pre_a_issues -eq $pre_a_uses &&
           $pre_a_ready -gt 0 && $pre_a_ready -le $pre_a_issues ]]
    fi

    hashes[$name]=$(sed -n \
        's/.* output_hash=\([0-9][0-9]*\).*/\1/p' <<<"$result")
    ticks[$name]=$(awk '$1 == "simTicks" { print $2; exit }' "$run/stats.txt")
    [[ ${hashes[$name]} == 2761840269561229581 ]]
    [[ ${ticks[$name]} =~ ^[1-9][0-9]*$ ]]
    {
        printf 'source_commit=%s\n' "$source_commit"
        printf 'gem5_sha256=%s\n' "$gem5_sha"
        printf 'guest_sha256=%s\n' "$guest_sha"
        printf 'shared_checkpoint_m5_cpt_sha256=%s\n' "$checkpoint_sha"
        printf 'config_sha256=%s\n' "$(sha256sum "$run/config.ini" | awk '{print $1}')"
        printf 'simTicks=%s\n' "${ticks[$name]}"
        printf 'output_hash=%s\n' "${hashes[$name]}"
    } >"$run/provenance.txt"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$name" "$replica" "$expected_pre_a" "${ticks[$name]}" \
        "${hashes[$name]}" "${selected[$name]}" "${rejected[$name]}" \
        "${value_issues[$name]}" "$value_responses" \
        "${a_read_issues[$name]}" "${a_read_responses[$name]}" \
        "${write_issues[$name]}" "${write_responses[$name]}" \
        "$pre_a_issues" "$pre_a_ready" "$pre_a_uses" \
        "$context_stalls" "$lookahead_stalls" >>"$out/matrix.tsv"
}

run_arm control_r1 control 1
run_arm treatment_r1 treatment 1
run_arm control_r2 control 2
run_arm treatment_r2 treatment 2

decision=PROMOTE
for replica in 1 2; do
    control="control_r$replica"
    treatment="treatment_r$replica"
    [[ ${hashes[$control]} == "${hashes[$treatment]}" ]]
    [[ ${selected[$control]} -eq ${selected[$treatment]} ]]
    [[ ${rejected[$control]} -eq ${rejected[$treatment]} ]]
    [[ ${a_read_issues[$control]} -eq ${a_read_issues[$treatment]} ]]
    [[ ${a_read_responses[$control]} -eq ${a_read_responses[$treatment]} ]]
    [[ ${write_issues[$control]} -eq ${write_issues[$treatment]} ]]
    [[ ${write_responses[$control]} -eq ${write_responses[$treatment]} ]]
    if [[ ${value_issues[$treatment]} -gt ${value_issues[$control]} ||
          ${ticks[$treatment]} -ge ${ticks[$control]} ]]; then
        decision=REJECT
    fi
done

{
    printf 'decision=%s\n' "$decision"
    for replica in 1 2; do
        control="control_r$replica"
        treatment="treatment_r$replica"
        awk -v replica="$replica" -v control="${ticks[$control]}" \
            -v treatment="${ticks[$treatment]}" \
            -v control_reads="${value_issues[$control]}" \
            -v treatment_reads="${value_issues[$treatment]}" 'BEGIN {
                printf "replica_%s_control_ticks=%s\n", replica, control
                printf "replica_%s_treatment_ticks=%s\n", replica, treatment
                printf "replica_%s_speedup=%.9f\n", replica, control / treatment
                printf "replica_%s_control_value_reads=%s\n", replica, control_reads
                printf "replica_%s_treatment_value_reads=%s\n", replica, treatment_reads
            }'
    done
    printf '%s\n' 'speedups_are_derived_from_measured_simTicks'
} >"$out/decision.txt"

cat "$out/matrix.tsv"
cat "$out/decision.txt"
echo "SOA_JIT_PRE_A_LOOKAHEAD_MATRIX_PASS"
