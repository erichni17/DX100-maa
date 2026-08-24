#!/usr/bin/env bash
# Candidate-only full GAPBS SSSP S22 gate for the selected hybrid composition.
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
graph=/data1/nier/worktrees/DX100-full-tile-sweep-20260720/benchmarks/gapbs/serialized_graph_22.wsg
native_log=/data1/nier/dx100-runs/2026-07-20-full-tile-sweep/repair3-validation/gapbs/sssp_s22_t16384_m2GB_gem5.opt.ovl_base_sha256_1ff4a396b98d6c838f695c4cbd631ca16e7ed12407365f17707bcf6df93e1343/run.log
tile_source=/data1/nier/dx100-runs/2026-07-20-full-tile-sweep/monitor-3h/reports/20260822_092051/tile_sweep_source.tsv
graph_sha=23eb25e34343334976554071a8184f7b03358fe1892ba44cd2f5a38369f4eebc
native_log_sha=20012684fa3cd2a4d6e6d75ecdb05f82ad818a3315e69afdd18b6c4a6f6798b7
tile_source_sha=e870eba1f74cd37c2f58695ef7ac6a5778ab030e635f55e0ce1464d72f0142cd
native16_ticks=758524789379
expected_fingerprint='SSSP_FINGERPRINT vertices=4194304 reached=4194304 unreachable=0 distance_sum=569278395 max_distance=258 hash_a=aaf3a6a5d4662d36 hash_b=9ffcf4962b364007 triangle_violations=0 missing_predecessors=0 nonpositive_weights=0 negative_distances=0 result=PASS'

[[ -x $gem5 ]] || { echo "missing gem5 binary: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
[[ -f $graph && -f $native_log && -f $tile_source ]] || {
    echo "missing frozen SSSP input or native evidence" >&2
    exit 2
}
[[ $(sha256sum "$graph" | awk '{print $1}') == "$graph_sha" ]]
[[ $(sha256sum "$native_log" | awk '{print $1}') == "$native_log_sha" ]]
[[ $(sha256sum "$tile_source" | awk '{print $1}') == "$tile_source_sha" ]]
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence launch from dirty worktree" >&2
    git -C "$root" status --short >&2
    exit 1
}

mkdir -p "$out/bin" "$out/input" "$out/checkpoint" "$out/run"
guest="$out/bin/sssp_maa_2G_old_result_hybrid_fp"
"${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -I"$root/benchmarks/gapbs/src" \
    -std=c++11 -O3 -Wall -Wextra -Werror -Wno-ignored-qualifiers \
    -Wno-unused-parameter -fopenmp -DGEM5 -DMAA -DNUM_CORES=4 \
    -DNUM_TILES_PER_CORE=8 -DTILE_SIZE=16384 \
    -DMAA_CONSUMER_TILE_SIZE=4096 -DMAA_MEM_SIZE=0x80000000 \
    -DSSSP_FP_ENABLE=1 -DSSSP_OLD_RESULT_HYBRID=1 \
    "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$guest"

options="-f $graph -n 1 -v"
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
    --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16
    --l1i_write_buffers=8 --l2cache --l2_size=256kB
    --l2_assoc=4 --l2-hwp-type=StridePrefetcher --l2_mshrs=32
    --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
    --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
    --cacheline_size=64 --mem-type Ramulator2
    --ramulator-config "$ramulator" --mem-channels=2
    --maa --maa_num_maas=1 --maa_num_indirect_units_per_maa=4
    --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096
    --maa_num_offset_table_entries=16384
    --maa_num_offset_table_epoch_entries=16384
    --maa_num_initial_row_table_slices=32
    --maa_soa_jit_old_result_pressure_policy=densest
    --maa_soa_jit_old_result_partial_credits=4
    --maa_soa_jit_active_contexts=8
    --maa_soa_jit_active_value_owners=64
    --maa_soa_jit_pre_a_value_lookahead
    --maa_soa_jit_value_cache_enable
    --cmd "$guest" --options "$options"
)

source_commit=$(git -C "$root" rev-parse HEAD)
{
    printf 'schema=dx100.sssp.old_result_hybrid.full_s22.v1\n'
    printf 'source_commit=%s\n' "$source_commit"
    printf 'gem5_sha256=%s\n' "$(sha256sum "$gem5" | awk '{print $1}')"
    printf 'guest_sha256=%s\n' "$(sha256sum "$guest" | awk '{print $1}')"
    printf 'graph_path=%s\ngraph_sha256=%s\n' "$graph" "$graph_sha"
    printf 'native_log_path=%s\nnative_log_sha256=%s\n' \
        "$native_log" "$native_log_sha"
    printf 'tile_source_sha256=%s\nnative16_simTicks=%s\n' \
        "$tile_source_sha" "$native16_ticks"
    printf 'candidate_checkpoint=true\nnative_checkpoint_reused=false\n'
    printf 'candidate_only=1\nnative_arms=0\nwall_timeout=none\n'
    printf 'trace_mode=disabled_full\nfull_graph=true\n'
    printf 'logical_elements=16384\nphysical_tile_elements=4096\n'
    printf 'memory_channels=2\nindirect_units=4\nrow_table_slices=32\n'
    printf 'pressure_policy=densest\npartial_credits=4\n'
    printf 'pre_a=true\nvalue_cache=true\nactive_value_owners=64\n'
    printf 'active_contexts=8\nold_result_payload_bytes=512\n'
    printf 'old_result_object_bytes=1128\npressure_control_bits_per_unit=3\n'
    printf 'expected_fingerprint=%s\n' "$expected_fingerprint"
    printf 'checkpoint_command='; printf '%q ' "${checkpoint_cmd[@]}"; printf '\n'
    printf 'restore_command='; printf '%q ' "${restore_cmd[@]}"; printf '\n'
} > "$out/manifest.txt"
sha256sum "$gem5" "$guest" "$graph" "$native_log" "$tile_source" \
    "$source_file" "$config" "$ramulator" "$0" \
    > "$out/input/artifacts.before.sha256"

set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
    "${checkpoint_cmd[@]}" > "$out/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/checkpoint.rc"
[[ $checkpoint_rc -eq 0 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
          "$out/checkpoint.log" || true) -eq 1 ]]
[[ $(grep -Fic 'user interrupt' "$out/checkpoint.log" || true) -eq 0 ]]
(
    cd "$out/checkpoint"
    find . -type f -print0 | sort -z | xargs -0 sha256sum
) > "$out/input/checkpoint.files.before.sha256"
checkpoint_tree_sha=$(sha256sum "$out/input/checkpoint.files.before.sha256" |
    awk '{print $1}')

set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
    "${restore_cmd[@]}" > "$out/run/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" > "$out/run/restore.rc"
[[ $restore_rc -eq 0 ]]

restore="$out/run/restore.log"
stats="$out/run/stats.txt"
config_ini="$out/run/config.ini"
[[ $(grep -Fxc "$expected_fingerprint" "$restore" || true) -eq 1 ]]
[[ $(grep -Fxc 'ROI End!!!' "$restore" || true) -eq 1 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
          "$restore" || true) -eq 1 ]]
[[ $(grep -Fic 'user interrupt' "$restore" || true) -eq 0 ]]
[[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
          "$restore" || true) -eq 0 ]]
[[ -s $stats && -s $config_ini ]]

terminal_line=$(grep '^SSSP_OLD_RESULT_HYBRID_TERMINAL ' "$restore")
[[ $(grep -c '^SSSP_OLD_RESULT_HYBRID_TERMINAL ' "$restore") -eq 1 ]]
field() {
    local key=$1
    sed -n "s/.* $key=\([^ ]*\).*/\1/p" <<<"$terminal_line"
}
eligible=$(field eligible_windows)
routed=$(field routed_windows)
index_pages=$(field index_publish_pages)
value_pages=$(field value_publish_pages)
old_words=$(field old_result_words)
legacy_words=$(field legacy_words)
[[ $eligible =~ ^[1-9][0-9]*$ && $routed -eq $eligible ]]
[[ $index_pages -eq $((routed * 4)) && $value_pages -eq $index_pages ]]
[[ $old_words -eq $((routed * 16384)) ]]
[[ $legacy_words =~ ^[0-9]+$ ]]
for token in 'logical_reorder_words=16384' 'physical_spd_words=4096' \
    'row_table_slices=32' 'host_spd_reads=0' \
    'hidden_result_payload_bytes=0' 'counts_close=1'; do
    [[ $terminal_line == *"$token"* ]]
done

stat_sum() {
    local suffix=$1
    awk -v suffix="$suffix" '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ ("_" suffix "$") { sum += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            printf "%.0f\n", sum
            exit
        }
    ' "$stats"
}
instructions=$(stat_sum IND_SoaJitInstructions)
terminals=$(stat_sum IND_SoaJitTerminalCompletions)
selected=$(stat_sum IND_SoaJitSelected)
rejected=$(stat_sum IND_SoaJitPredicateRejected)
captures=$(stat_sum IND_SoaJitOldResultCaptures)
writes=$(stat_sum IND_SoaJitOldResultWriteIssues)
responses=$(stat_sum IND_SoaJitOldResultWriteResponses)
a_reads=$(stat_sum IND_SoaJitAReadIssues)
a_read_responses=$(stat_sum IND_SoaJitAReadResponses)
a_writes=$(stat_sum IND_SoaJitAWriteIssues)
a_write_responses=$(stat_sum IND_SoaJitAWriteResponses)
[[ $instructions -eq $routed && $terminals -eq $instructions ]]
[[ $selected -eq $old_words && $rejected -eq 0 && $captures -eq $selected ]]
[[ $writes -gt 0 && $writes -eq $responses ]]
[[ $a_reads -gt 0 && $a_reads -eq $a_read_responses ]]
[[ $a_reads -eq $a_writes && $a_writes -eq $a_write_responses ]]
for resolved in num_indirect_units_per_maa=4 num_tile_elements=16384 \
    physical_tile_elements=4096 num_offset_table_entries=16384 \
    num_offset_table_epoch_entries=16384 num_initial_row_table_slices=32 \
    soa_jit_old_result_partial_credits=4 \
    soa_jit_old_result_pressure_policy=densest \
    soa_jit_active_contexts=8 soa_jit_active_value_owners=64 \
    soa_jit_pre_a_value_lookahead=true soa_jit_value_cache_enable=true; do
    grep -Fqx "$resolved" "$config_ini"
done
[[ $(find "$out/run" -maxdepth 1 -type f -name '*trace*' | wc -l) -eq 0 ]]

first_ticks=$(awk '$1 == "simTicks" { print $2; exit }' "$stats")
final_ticks=$(awk '$1 == "simTicks" { value=$2 } END { print value }' "$stats")
speedup=$(awk -v base="$native16_ticks" -v candidate="$first_ticks" \
    'BEGIN { printf "%.9f", base / candidate }')
{
    printf 'terminal=true\ncorrect=true\n'
    printf 'first_simTicks=%s\nfinal_simTicks=%s\n' "$first_ticks" "$final_ticks"
    printf 'native16_simTicks=%s\nend_to_end_speedup_vs_native16=%s\n' \
        "$native16_ticks" "$speedup"
    printf 'eligible_windows=%s\nrouted_windows=%s\n' "$eligible" "$routed"
    printf 'legacy_words=%s\nselected=%s\nold_result_captures=%s\n' \
        "$legacy_words" "$selected" "$captures"
    printf 'old_result_write_issues=%s\nold_result_write_responses=%s\n' \
        "$writes" "$responses"
    printf 'pre_a_issues=%s\npre_a_ready=%s\npre_a_uses=%s\n' \
        "$(stat_sum IND_SoaJitPreAValueIssues)" \
        "$(stat_sum IND_SoaJitPreAValueReadyAtAResponse)" \
        "$(stat_sum IND_SoaJitPreAValueUses)"
    printf 'value_hits=%s\n' "$(stat_sum IND_SoaJitValueHits)"
    printf 'comparison_scope=end_to_end_context_not_causal_virtualization\n'
} > "$out/result.txt"

(
    cd "$out/checkpoint"
    find . -type f -print0 | sort -z | xargs -0 sha256sum
) > "$out/input/checkpoint.files.after.sha256"
cmp -s "$out/input/checkpoint.files.before.sha256" \
    "$out/input/checkpoint.files.after.sha256"
sha256sum "$gem5" "$guest" "$graph" "$native_log" "$tile_source" \
    "$source_file" "$config" "$ramulator" "$0" \
    > "$out/input/artifacts.after.sha256"
cmp -s "$out/input/artifacts.before.sha256" "$out/input/artifacts.after.sha256"
{
    printf 'checkpoint_tree_sha256=%s\n' "$checkpoint_tree_sha"
} >> "$out/result.txt"
sha256sum "$out/manifest.txt" "$out/result.txt" "$out/checkpoint.log" \
    "$restore" "$stats" "$config_ini" \
    "$out/input/checkpoint.files.after.sha256" \
    > "$out/result_sha256.txt"
touch "$out/gate.complete"
cat "$out/result.txt"
