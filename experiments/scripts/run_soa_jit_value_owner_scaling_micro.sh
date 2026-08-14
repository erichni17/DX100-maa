#!/usr/bin/env bash
set -euo pipefail

# Exact full-16K/physical-4K SoA/JIT value-owner capacity sweep.
if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "usage: $0 OUTDIR [GEM5]" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5=${2:-"$root/build/X86/gem5.opt"}
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
checkpoint=/data1/nier/dx100-runs/2026-08-14-soa-jit-overlap-premerge-fast/c8l8-checkpoint
guest=/data1/nier/dx100-runs/2026-08-14-soa-jit-capacity-combined-fbec9dbe-r1/input/guest
expected_hash=2761840269561229581
timeout_seconds=${DX100_TIMEOUT_SECONDS:-0}

[[ -x $gem5 ]] || { echo "missing gem5: $gem5" >&2; exit 2; }
[[ -f $config && -f $ramulator && -d $checkpoint && -x $guest ]] || exit 2
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
mkdir -p "$out"

common=(
    "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --checkpoint-dir="$checkpoint" --sys-clock 3.2GHz --cpu-clock 3.2GHz
    --caches --l1d_size=32kB --l1d_assoc=8 --l1d_mshrs=16 --l1d_write_buffers=8
    --l1i_size=32kB --l1i_assoc=8 --l1i_mshrs=16 --l1i_write_buffers=8
    --l2cache --l2_size=256kB --l2_assoc=4 --l2_mshrs=32 --l2_write_buffers=16
    --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
    --cacheline_size=64 --mem-type Ramulator2 --ramulator-config "$ramulator" --mem-channels=1
    --maa --maa_num_maas=1 --maa_num_indirect_units_per_maa=1
    --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096
    --maa_num_offset_table_entries=16384 --maa_num_offset_table_epoch_entries=16384
    --maa_num_initial_row_table_slices=16 --maa_virtual_index_buffer_lines=8
    --maa_soa_jit_active_contexts=32 --maa_soa_jit_value_lookahead=8
    --maa_soa_jit_value_cache_enable --maa_soa_jit_predicate_active_credits=16
    --maa_soa_jit_apply_lanes=1 --cmd "$guest" --options soa
)

stat_value() {
    local file=$1 key=$2
    awk -v key="$key" '$1 == key { print $2; exit }' "$file"
}

need_equal() {
    [[ $1 == "$2" ]] || {
        echo "ledger mismatch: $3 ($1 != $2)" >&2
        exit 1
    }
}

run_gem5() {
    if [[ $timeout_seconds -gt 0 ]]; then
        timeout "$timeout_seconds" "$@"
    else
        "$@"
    fi
}

printf 'owners\tsimTicks\tvalue_reads\tcached_responses\thits\tmerged\tevictions\tvalue_stalls\tcontext_stalls\tcache_hwm\tcoalescer_bytes\tactive_payload_bytes\n' >"$out/results.tsv"
for owners in 32 64 128 256; do
    run="$out/v$owners"
    mkdir "$run"
    run_gem5 "$gem5" --listener-mode=off --outdir="$run" \
        --debug-flags=MAAVirtualTrace --debug-file=soa_jit_trace.log \
        "${common[@]}" --maa_soa_jit_active_value_owners="$owners" \
        >"$run/restore.log" 2>&1

    [[ $(grep -Ec '^HYBRID_RMW_SOA_RESULT .*output_hash='"$expected_hash"' .*errors=0' "$run/restore.log" || true) -eq 1 ]]
    grep -Fqx 'ROI Ended' "$run/restore.log"
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' "$run/restore.log" || true) -eq 1 ]]
    [[ $(grep -Eic 'panic|fatal|assert|segmentation fault|aborted' "$run/restore.log" || true) -eq 0 ]]
    grep -Fq "soa_jit_active_value_owners=$owners" "$run/config.ini"
    for knob in soa_jit_active_contexts=32 soa_jit_predicate_active_credits=16 soa_jit_value_lookahead=8 virtual_index_buffer_lines=8 soa_jit_apply_lanes=1; do
        grep -Fq "$knob" "$run/config.ini"
    done

    stats="$run/stats.txt"
    selected=$(stat_value "$stats" system.maa.I0_IND_SoaJitSelected)
    rejected=$(stat_value "$stats" system.maa.I0_IND_SoaJitPredicateRejected)
    pi=$(stat_value "$stats" system.maa.I0_IND_SoaJitPredicateLineReads)
    pr=$(stat_value "$stats" system.maa.I0_IND_SoaJitPredicateLineResponses)
    vi=$(stat_value "$stats" system.maa.I0_IND_SoaJitValueReadIssues)
    vr=$(stat_value "$stats" system.maa.I0_IND_SoaJitValueReadResponses)
    fills=$(stat_value "$stats" system.maa.I0_IND_SoaJitValueFills)
    deliveries=$(stat_value "$stats" system.maa.I0_IND_SoaJitValueDeliveries)
    li=$(stat_value "$stats" system.maa.I0_IND_SoaJitLookaheadIssues)
    lr=$(stat_value "$stats" system.maa.I0_IND_SoaJitLookaheadResponses)
    ari=$(stat_value "$stats" system.maa.I0_IND_SoaJitAReadIssues)
    arr=$(stat_value "$stats" system.maa.I0_IND_SoaJitAReadResponses)
    awi=$(stat_value "$stats" system.maa.I0_IND_SoaJitAWriteIssues)
    awr=$(stat_value "$stats" system.maa.I0_IND_SoaJitAWriteResponses)
    terminal=$(stat_value "$stats" system.maa.I0_IND_SoaJitTerminalCompletions)
    need_equal "$((selected + rejected))" 32768 predicate_total
    need_equal "$pi" "$pr" predicate_ledger
    need_equal "$vi" "$vr" value_ledger
    need_equal "$fills" "$vr" fill_ledger
    need_equal "$deliveries" "$li" issue_delivery_ledger
    need_equal "$li" "$lr" response_delivery_ledger
    need_equal "$li" "$selected" selected_delivery_ledger
    need_equal "$ari" "$arr" aread_ledger
    need_equal "$awi" "$awr" awrite_ledger
    need_equal "$terminal" 2 terminal_ledger
    [[ $(grep -Ec 'event=soa_jit_complete .*terminal=1' "$run/soa_jit_trace.log" || true) -eq 2 ]]

    ticks=$(stat_value "$stats" simTicks)
    cached=$(stat_value "$stats" system.maa.I0_IND_SoaJitValueCachedResponses)
    hits=$(stat_value "$stats" system.maa.I0_IND_SoaJitValueHits)
    merged=$(stat_value "$stats" system.maa.I0_IND_SoaJitValueMergedWaiters)
    evictions=$(stat_value "$stats" system.maa.I0_IND_SoaJitValueEvictions)
    value_stalls=$(stat_value "$stats" system.maa.I0_IND_SoaJitValueStalls)
    context_stalls=$(stat_value "$stats" system.maa.I0_IND_SoaJitContextStalls)
    cache_hwm=$(stat_value "$stats" system.maa.I0_IND_SoaJitValueCacheHighWater)
    storage=$(grep -m1 'event=soa_jit_storage ' "$run/soa_jit_trace.log")
    coalescer_bytes=$(sed -n 's/.*fixed_value_owner_bytes=\([0-9][0-9]*\).*/\1/p' <<<"$storage")
    active_payload_bytes=$(sed -n 's/.*active_value_owner_payload_bytes=\([0-9][0-9]*\).*/\1/p' <<<"$storage")
    [[ $cache_hwm -ge 1 && $cache_hwm -le $owners ]]
    need_equal "$active_payload_bytes" "$((owners * 64))" active_payload
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$owners" "$ticks" "$vi" "$cached" "$hits" "$merged" \
        "$evictions" "$value_stalls" "$context_stalls" "$cache_hwm" \
        "$coalescer_bytes" "$active_payload_bytes" >>"$out/results.tsv"
done

{
    printf 'source_commit='; git -C "$root" rev-parse HEAD
    printf 'source_diff_sha256='; git -C "$root" diff --binary | sha256sum | awk '{print $1}'
    printf 'gem5_sha256='; sha256sum "$gem5" | awk '{print $1}'
    printf 'guest_sha256='; sha256sum "$guest" | awk '{print $1}'
    printf 'checkpoint=%s\nexpected_hash=%s\n' "$checkpoint" "$expected_hash"
    printf 'fixed_controls=contexts=32,predicate_credits=16,lookahead=8,index_lines=8,apply_lanes=1\n'
    printf 'timeout_seconds=%s\n' "$timeout_seconds"
} >"$out/manifest.txt"

awk 'NR == 1 { next } $1 == 32 { base=$2 } NR > 1 { printf "owners=%s simTicks=%s speedup_vs_32=%.6fx value_reads=%s value_stalls=%s coalescer_bytes=%s active_payload_bytes=%s\n", $1, $2, base/$2, $3, $8, $11, $12 }' \
    "$out/results.tsv" >"$out/summary.txt"
cat "$out/results.tsv"
cat "$out/summary.txt"
echo 'SOA_JIT_VALUE_OWNER_SCALING_MICRO_PASS'
