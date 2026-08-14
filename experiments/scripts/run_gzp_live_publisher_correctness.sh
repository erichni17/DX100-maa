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
source_file="$root/benchmarks/UME/gradzatp.cpp"
m5op="$root/util/m5/src/abi/x86/m5op.S"

[[ -x $gem5 ]] || { echo "missing executable gem5: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
[[ -z $(git -C "$root" status --short --untracked-files=all) ]] || {
    echo "refusing correctness evidence from a dirty source worktree" >&2
    exit 1
}

mkdir -p "$out/artifacts"
binary="$out/artifacts/gradzatp_gzp_live_publisher"
selector="$out/artifacts/selector.txt"
printf '%s\n' 'token_stream_ld soa_jit' > "$selector"
n=65536
expected_publications=32
expected_lines=8192

"${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O3 -Wall -g3 -fopenmp \
    -DGEM5 -DMAA -DMAA_VIRTUAL_GATHER \
    -DMAA_GENERAL_VIRTUAL_CONSUMER -DMAA_CONSUMER_TILE_SIZE=4096 \
    -DUME_GZP_SOA_JIT_RMW -DUME_FIXED_INPUT -DUME_OUTPUT_FINGERPRINT \
    -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 \
    "$m5op" "$source_file" -o "$binary"

options="$n $selector"
checkpoint_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint"
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB
    --max-checkpoints=1 --cmd "$binary" --options "$options"
)
restore_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/run"
    --debug-flags=MAATrace,MAAVirtualTrace
    --debug-file=virtual_trace.log "$config"
    --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --checkpoint-dir="$out/checkpoint"
    --sys-clock 3.2GHz --cpu-clock 3.2GHz
    --caches --l1d_size=32kB --l1d_assoc=8
    --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16
    --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8
    --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16
    --l1i_write_buffers=8 --l2cache --l2_size=256kB --l2_assoc=4
    --l2-hwp-type=StridePrefetcher --l2_mshrs=32
    --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
    --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
    --cacheline_size=64 --mem-type Ramulator2
    --ramulator-config "$ramulator" --mem-channels=1
    --maa --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096
    --maa_num_initial_row_table_slices=16
    --maa_num_row_table_rows_per_slice=64
    --maa_num_row_table_entries_per_subslice_row=8
    --maa_num_offset_table_entries=16384
    --maa_num_offset_table_epoch_entries=16384
    --maa_virtual_grow_order --maa_virtual_index_force_cache
    --maa_virtual_index_buffer_lines=128 --maa_virtual_combine_slots=384
    --maa_virtual_combine_words=4096 --maa_virtual_combine_ways=4
    --maa_virtual_response_slots=96 --maa_virtual_response_word_pool=480
    --maa_virtual_words_per_cycle=4
    --maa_virtual_max_outstanding_writes=64 --maa_virtual_masked_writes
    --maa_direct_retirement_line_handoff
    --cmd "$binary" --options "$options"
)

{
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'scope=GZP_FP32_logical16_physical4K_live_publisher_single_owner_correctness\n'
    printf 'speedup_claim=0\n'
    printf 'publisher_credits=8\nexpected_publications=%s\n' \
        "$expected_publications"
    printf 'checkpoint_command='; printf '%q ' "${checkpoint_cmd[@]}"; printf '\n'
    printf 'restore_command='; printf '%q ' "${restore_cmd[@]}"; printf '\n'
} > "$out/manifest.txt"
sha256sum "$source_file" "$binary" "$gem5" "$config" "$ramulator" \
    "$selector" > "$out/artifact_sha256.txt"

set +e
"${checkpoint_cmd[@]}" > "$out/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]]
[[ $(rg -c '^Exiting @ tick [0-9]+ because checkpoint$' \
          "$out/checkpoint.log" || true) -eq 1 ]]

set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=1 \
    "${restore_cmd[@]}" > "$out/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" > "$out/restore.exit"
[[ $restore_rc -eq 0 ]]
[[ $(rg -c '^UME_REFERENCE_PASS point_volume_errors=0 point_gradient_errors=0 ' \
          "$out/restore.log" || true) -eq 1 ]]
terminal="UME_GZP_TERMINAL treatment=soa_jit_correctness full_windows=4 volume_only_windows=0 published_predicates=$n published_gradient_values=$n"
[[ $(rg -c "^$terminal .*publisher=response_bearing_spd_to_coherent performance_promotable=0 result=PASS$" \
          "$out/restore.log" || true) -eq 1 ]]
[[ $(rg -c '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
          "$out/restore.log" || true) -eq 1 ]]
[[ $(rg -ic 'panic|fatal|assert|abort|segmentation fault|error:' \
          "$out/restore.log" || true) -eq 0 ]]

stats="$out/run/stats.txt"
trace="$out/run/virtual_trace.log"
[[ -s $stats && -s $trace ]]
sum_stat() {
    local suffix=$1
    awk -v suffix="$suffix" '$1 ~ suffix "$" { value += $2 } END { print value+0 }' "$stats"
}
max_stat() {
    local suffix=$1
    awk -v suffix="$suffix" '$1 ~ suffix "$" && $2 > value { value=$2 } END { print value+0 }' "$stats"
}
[[ $(sum_stat 'STR_PublishIssues') -eq $expected_lines ]]
[[ $(sum_stat 'STR_PublishAccepts') -eq $expected_lines ]]
[[ $(sum_stat 'STR_PublishWriteResponses') -eq $expected_lines ]]
[[ $(sum_stat 'STR_PublishTerminals') -eq $expected_publications ]]
[[ $(max_stat 'STR_PublishCreditHWM') -eq 8 ]]
[[ $(sum_stat 'STR_PublishCreditStalls') -gt 0 ]]
[[ $(rg -c 'event=spd_publish_issue ' "$trace" || true) -eq $expected_lines ]]
[[ $(rg -c 'event=spd_publish_accept ' "$trace" || true) -eq $expected_lines ]]
[[ $(rg -c 'event=spd_publish_response ' "$trace" || true) -eq $expected_lines ]]
[[ $(rg -c 'event=spd_publish_terminal ' "$trace" || true) -eq $expected_publications ]]
for resolved in num_tile_elements=16384 physical_tile_elements=4096 \
    num_offset_table_entries=16384 num_offset_table_epoch_entries=16384; do
    rg -Fx "$resolved" "$out/run/config.ini"
done

{
    printf 'terminal=true\ncorrect=true\nspeedup_claim=0\n'
    printf 'simTicks=%s\n' "$(awk '$1 == "simTicks" { value=$2 } END { print value+0 }' "$stats")"
    printf 'issues=%s\naccepts=%s\nresponses=%s\nterminals=%s\n' \
        "$expected_lines" "$expected_lines" "$expected_lines" \
        "$expected_publications"
    printf 'retries=%s\n' "$(sum_stat 'STR_PublishRetries')"
    printf 'credit_stalls=%s\n' "$(sum_stat 'STR_PublishCreditStalls')"
    printf 'overlap_issues=%s\n' "$(sum_stat 'STR_PublishOverlapIssues')"
    printf 'credit_hwm=8\n'
} > "$out/result.txt"
sha256sum "$out/restore.log" "$stats" "$out/run/config.ini" "$trace" \
    "$out/result.txt" > "$out/result_sha256.txt"
printf 'PASS gzp_live_publisher_correctness out=%s\n' "$out"
