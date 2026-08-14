#!/usr/bin/env bash
set -euo pipefail

# Exact shared-checkpoint 32-versus-64 value-owner gate.  The 64-owner
# treatment is default-off: no source/default is changed by this experiment.
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
expected_hash=2761840269561229581
timeout_seconds=${SOA_JIT_OWNER_TIMEOUT_SECONDS:-0}

[[ -x $gem5 ]] || { echo "missing gem5: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
[[ $timeout_seconds =~ ^[0-9]+$ ]] || {
    echo "SOA_JIT_OWNER_TIMEOUT_SECONDS must be a non-negative integer" >&2
    exit 2
}
timeout_command=()
((timeout_seconds == 0)) || timeout_command=(timeout "$timeout_seconds")
[[ -z $(git -C "$root" status --porcelain) ]] || {
    echo "source worktree must be clean for provenance" >&2; exit 2;
}
mkdir -p "$out/bin" "$out/checkpoint/soa16" "$out/runs"

guest="$out/bin/test_hybrid_rmw_soa_T16384"
"$cxx" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O2 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers -DGEM5 -DTILE_SIZE=16384 \
    -DNUM_CORES=4 -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/src/abi/x86/m5op.S" \
    "$root/benchmarks/API/test_hybrid_rmw_soa.cpp" -o "$guest"

"${timeout_command[@]}" "$gem5" --listener-mode=off --outdir="$out/checkpoint/soa16" \
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
    --max-checkpoints=1 --cmd "$guest" --options=soa \
    >"$out/checkpoint/soa16/checkpoint.log" 2>&1
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
    "$out/checkpoint/soa16/checkpoint.log" || true) -eq 1 ]] || {
    echo "shared checkpoint did not close exactly" >&2; exit 1;
}

checkpoint_state=$(find "$out/checkpoint/soa16" -type f -path '*/cpt.*/m5.cpt' -print)
[[ $(wc -l <<<"$checkpoint_state") -eq 1 ]] || {
    echo "shared checkpoint state is not unique" >&2; exit 1;
}
source_commit=$(git -C "$root" rev-parse HEAD)
gem5_sha=$(sha256sum "$gem5" | awk '{print $1}')
guest_sha=$(sha256sum "$guest" | awk '{print $1}')
checkpoint_sha=$(sha256sum "$checkpoint_state" | awk '{print $1}')
{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'gem5_sha256=%s\n' "$gem5_sha"
    printf 'guest_sha256=%s\n' "$guest_sha"
    printf 'shared_checkpoint_m5_cpt_sha256=%s\n' "$checkpoint_sha"
    printf '%s\n' 'logical_tile_elements=16384' 'metadata_entries=16384' \
        'physical_tile_elements=4096' 'active_contexts=32' \
        'value_lookahead=8' 'value_cache_enable=true' \
        'predicate_active_credits=16' 'index_buffer_lines=4' \
        'apply_lanes=1' 'pre_a_value_lookahead=false' \
        'sequential_value_prefetch_credits=0' 'owners_control=32' \
        'owners_treatment=64' 'replicas=2'
} >"$out/manifest.txt"

stat_sum() {
    local stats=$1 suffix=$2
    awk -v suffix="$suffix" '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ ("_" suffix "$") { sum += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            printf "%.0f\\n", sum; exit
        }
    ' "$stats"
}

declare -A ticks hashes selected rejected value_issues value_responses fills
declare -A deliveries aliases a_reads a_responses writes write_responses
declare -A evictions value_stalls context_stalls cache_hwm terminal
printf 'arm\treplica\towners\tsimTicks\toutput_hash\tselected\trejected\tvalue_reads\tvalue_responses\tfills\tdeliveries\taliases\ta_reads\ta_responses\twrites\twrite_responses\tevictions\tvalue_stalls\tcontext_stalls\tcache_hwm\n' >"$out/matrix.tsv"

run_arm() {
    local name=$1 replica=$2 owners=$3
    local run="$out/runs/$name"
    mkdir -p "$run"
    "${timeout_command[@]}" "$gem5" --listener-mode=off --outdir="$run" "$config" \
        --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB \
        --checkpoint-dir="$out/checkpoint/soa16" --sys-clock 3.2GHz \
        --cpu-clock 3.2GHz --caches --l1d_size=32kB --l1d_assoc=8 \
        --l1d_mshrs=16 --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8 \
        --l1i_mshrs=16 --l1i_write_buffers=8 --l2cache --l2_size=256kB \
        --l2_assoc=4 --l2_mshrs=32 --l2_write_buffers=16 --l3cache \
        --l3_size=8MB --l3_assoc=16 --l3_mshrs=256 --l3_write_buffers=128 \
        --l3_ports=4 --cacheline_size=64 --mem-type=Ramulator2 \
        --ramulator-config="$ramulator" --mem-channels=1 --maa \
        --maa_num_maas=1 --maa_num_indirect_units_per_maa=1 \
        --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096 \
        --maa_num_offset_table_entries=16384 \
        --maa_num_offset_table_epoch_entries=16384 \
        --maa_num_initial_row_table_slices=16 \
        --maa_soa_jit_predicate_active_credits=16 \
        --maa_virtual_index_buffer_lines=4 --maa_soa_jit_active_contexts=32 \
        --maa_soa_jit_value_lookahead=8 --maa_soa_jit_value_cache_enable \
        --maa_soa_jit_value_prefetch_credits=0 \
        --maa_soa_jit_active_value_owners="$owners" --maa_soa_jit_apply_lanes=1 \
        --cmd="$guest" >"$run/restore.log" 2>&1
    [[ $(grep -Fxc 'ROI Ended' "$run/restore.log" || true) -eq 1 ]]
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' "$run/restore.log" || true) -eq 1 ]]
    [[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' "$run/restore.log" || true) -eq 0 ]]
    result=$(grep -E '^HYBRID_RMW_SOA_RESULT ' "$run/restore.log" || true)
    [[ $(grep -Ec '^HYBRID_RMW_SOA_RESULT ' "$run/restore.log" || true) -eq 1 && $result =~ mode=soa && $result =~ logical=16384 && $result =~ operations=2 && $result =~ errors=0 ]]
    for resolved in "soa_jit_active_value_owners=$owners" soa_jit_active_contexts=32 soa_jit_value_lookahead=8 soa_jit_value_cache_enable=true soa_jit_value_prefetch_credits=0 soa_jit_pre_a_value_lookahead=false soa_jit_apply_lanes=1; do grep -Fqx "$resolved" "$run/config.ini"; done
    selected[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitSelected); rejected[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateRejected)
    value_issues[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadIssues); value_responses[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadResponses); fills[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitValueFills)
    deliveries[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitValueDeliveries); aliases[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitAliasesApplied)
    a_reads[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitAReadIssues); a_responses[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitAReadResponses); writes[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteIssues); write_responses[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteResponses)
    evictions[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitValueEvictions); value_stalls[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitValueStalls); context_stalls[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitContextStalls); cache_hwm[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitValueCacheHighWater); terminal[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitTerminalCompletions)
    [[ ${terminal[$name]} -eq 2 && $((selected[$name] + rejected[$name])) -eq 32768 && ${value_issues[$name]} -eq ${value_responses[$name]} && ${value_responses[$name]} -eq ${fills[$name]} && ${deliveries[$name]} -eq ${selected[$name]} && ${aliases[$name]} -eq ${selected[$name]} && ${a_reads[$name]} -eq ${a_responses[$name]} && ${a_reads[$name]} -eq ${writes[$name]} && ${writes[$name]} -eq ${write_responses[$name]} && ${cache_hwm[$name]} -ge 2 && ${cache_hwm[$name]} -le $((owners * 2)) ]]
    hashes[$name]=$(sed -n 's/.* output_hash=\([0-9][0-9]*\).*/\1/p' <<<"$result"); ticks[$name]=$(awk '$1 == "simTicks" { print $2; exit }' "$run/stats.txt")
    [[ ${hashes[$name]} == "$expected_hash" && ${ticks[$name]} =~ ^[1-9][0-9]*$ ]]
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$replica" "$owners" "${ticks[$name]}" "${hashes[$name]}" "${selected[$name]}" "${rejected[$name]}" "${value_issues[$name]}" "${value_responses[$name]}" "${fills[$name]}" "${deliveries[$name]}" "${aliases[$name]}" "${a_reads[$name]}" "${a_responses[$name]}" "${writes[$name]}" "${write_responses[$name]}" "${evictions[$name]}" "${value_stalls[$name]}" "${context_stalls[$name]}" "${cache_hwm[$name]}" >>"$out/matrix.tsv"
}

run_arm control_r1 1 32; run_arm treatment_r1 1 64
run_arm control_r2 2 32; run_arm treatment_r2 2 64
decision=PROMOTE
for replica in 1 2; do
    control=control_r$replica; treatment=treatment_r$replica
    [[ ${hashes[$control]} == ${hashes[$treatment]} && ${selected[$control]} -eq ${selected[$treatment]} && ${rejected[$control]} -eq ${rejected[$treatment]} && ${a_reads[$control]} -eq ${a_reads[$treatment]} && ${writes[$control]} -eq ${writes[$treatment]} ]]
    if [[ ${ticks[$treatment]} -ge ${ticks[$control]} || ${evictions[$treatment]} -ge ${evictions[$control]} ]]; then decision=REJECT; fi
done
{
    printf 'decision=%s\n' "$decision"
    for replica in 1 2; do control=control_r$replica; treatment=treatment_r$replica; awk -v r="$replica" -v c="${ticks[$control]}" -v t="${ticks[$treatment]}" -v ce="${evictions[$control]}" -v te="${evictions[$treatment]}" 'BEGIN { printf "replica_%s_speedup=%.9f\\nreplica_%s_evictions=%s_to_%s\\n", r, c/t, r, ce, te }'; done
    printf '%s\n' 'decision_requires_lower_simTicks_and_lower_evictions_in_both_replicas'
} >"$out/summary.txt"
cat "$out/matrix.tsv"; cat "$out/summary.txt"; echo SOA_JIT_VALUE_OWNER_SCALING_MICRO_PASS
