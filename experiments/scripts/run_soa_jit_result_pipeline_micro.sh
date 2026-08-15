#!/usr/bin/env bash
set -euo pipefail

# Two-repetition exact API gate for the default-off 64-line A-result pipeline.
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
[[ $timeout_seconds =~ ^[0-9]+$ ]] || {
    echo "DX100_TIMEOUT_SECONDS must be a non-negative integer" >&2
    exit 2
}
launcher=()
if ((timeout_seconds > 0)); then
    launcher=(timeout "$timeout_seconds")
fi

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
    --maa_soa_jit_active_value_owners=64 --maa_soa_jit_value_lookahead=8
    --maa_soa_jit_value_cache_enable --maa_soa_jit_pre_a_value_lookahead
    --maa_soa_jit_predicate_active_credits=16 --maa_soa_jit_apply_lanes=1
    --maa_soa_jit_value_prefetch_credits=0 --cmd "$guest" --options soa
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

trace_sum() {
    local file=$1 key=$2
    awk -v key="$key" '
        /event=soa_jit_result_pipeline / {
            for (field = 1; field <= NF; ++field) {
                split($field, pair, "=")
                if (pair[1] == key) sum += pair[2]
            }
        }
        END { print sum + 0 }
    ' "$file"
}

trace_first() {
    local file=$1 key=$2
    awk -v key="$key" '
        /event=soa_jit_result_pipeline / {
            for (field = 1; field <= NF; ++field) {
                split($field, pair, "=")
                if (pair[1] == key) { print pair[2]; exit }
            }
        }
    ' "$file"
}

printf 'repetition\tarm\tcontexts\tsimTicks\tcontext_stalls\tcontext_hwm\tread_write_overlap_ticks\tdual_region_overlap_ticks\tserialized_write_only_ticks\ttraffic_hwm_r1\tfixed_result_payload_bytes\tactive_result_payload_bytes\tfixed_lookahead_value_payload_bytes\tactive_lookahead_value_payload_bytes\tfixed_max_transient_write_payload_bytes\tactive_max_transient_write_payload_bytes\tfixed_result_contexts_bytes\tfixed_result_nonpayload_bytes\tbaseline_32_result_contexts_bytes\tincremental_result_contexts_bytes_vs_32\tincremental_result_context_nonpayload_bytes_vs_32\tincremental_result_waiter_mask_bytes_vs_32\tincremental_result_total_nonpayload_bytes_vs_32\tincremental_result_total_state_bytes_vs_32\n' >"$out/results.tsv"

for repetition in 1 2; do
    for contexts in 32 64; do
        arm=control
        [[ $contexts -eq 64 ]] && arm=treatment
        run="$out/rep${repetition}-${arm}"
        mkdir "$run"
        "${launcher[@]}" "$gem5" --listener-mode=off \
            --outdir="$run" --debug-flags=MAAVirtualTrace \
            --debug-file=soa_jit_trace.log "${common[@]}" \
            --maa_soa_jit_active_contexts="$contexts" \
            >"$run/restore.log" 2>&1

        [[ $(grep -Ec '^HYBRID_RMW_SOA_RESULT .*output_hash='"$expected_hash"' .*errors=0' "$run/restore.log" || true) -eq 1 ]]
        grep -Fqx 'ROI Ended' "$run/restore.log"
        [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' "$run/restore.log" || true) -eq 1 ]]
        [[ $(grep -Eic 'panic|fatal|assert|segmentation fault|aborted' "$run/restore.log" || true) -eq 0 ]]
        grep -Fq "soa_jit_active_contexts=$contexts" "$run/config.ini"
        for knob in soa_jit_predicate_active_credits=16 soa_jit_active_value_owners=64 soa_jit_value_lookahead=8 virtual_index_buffer_lines=8 soa_jit_apply_lanes=1 soa_jit_value_prefetch_credits=0 soa_jit_pre_a_value_lookahead=true; do
            grep -Fq "$knob" "$run/config.ini"
        done

        stats="$run/stats.txt"
        trace="$run/soa_jit_trace.log"
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
        need_equal "$(grep -Ec 'event=soa_jit_complete .*terminal=1' "$trace" || true)" 2 completion_trace
        need_equal "$(grep -Ec 'event=soa_jit_result_pipeline .*terminal=1' "$trace" || true)" 2 result_pipeline_trace

        fixed_payload=$(trace_first "$trace" fixed_result_payload_bytes)
        active_payload=$(trace_first "$trace" active_result_payload_bytes)
        fixed_lookahead_payload=$(trace_first "$trace" fixed_lookahead_value_payload_bytes)
        active_lookahead_payload=$(trace_first "$trace" active_lookahead_value_payload_bytes)
        fixed_transient_write_payload=$(trace_first "$trace" fixed_max_transient_write_payload_bytes)
        active_transient_write_payload=$(trace_first "$trace" active_max_transient_write_payload_bytes)
        context_bytes=$(trace_first "$trace" fixed_result_context_bytes)
        fixed_contexts=$(trace_first "$trace" fixed_result_contexts_bytes)
        nonpayload=$(trace_first "$trace" fixed_result_nonpayload_bytes)
        baseline_contexts=$(trace_first "$trace" baseline_32_result_contexts_bytes)
        incremental_contexts=$(trace_first "$trace" incremental_result_contexts_bytes_vs_32)
        incremental_nonpayload=$(trace_first "$trace" incremental_result_nonpayload_bytes_vs_32)
        fixed_waiter_masks=$(trace_first "$trace" fixed_result_waiter_mask_bytes)
        baseline_waiter_masks=$(trace_first "$trace" baseline_32_result_waiter_mask_bytes)
        incremental_waiter_masks=$(trace_first "$trace" incremental_result_waiter_mask_bytes_vs_32)
        incremental_total_nonpayload=$(trace_first "$trace" incremental_result_total_nonpayload_bytes_vs_32)
        incremental_total_state=$(trace_first "$trace" incremental_result_total_state_bytes_vs_32)
        need_equal "$fixed_payload" 4096 fixed_result_payload_bytes
        need_equal "$active_payload" "$((contexts * 64))" active_result_payload_bytes
        need_equal "$fixed_lookahead_payload" 4096 fixed_lookahead_value_payload_bytes
        need_equal "$active_lookahead_payload" "$((contexts * 64))" active_lookahead_value_payload_bytes
        need_equal "$fixed_transient_write_payload" 4096 fixed_max_transient_write_payload_bytes
        need_equal "$active_transient_write_payload" "$((contexts * 64))" active_max_transient_write_payload_bytes
        need_equal "$fixed_contexts" "$((context_bytes * 64))" fixed_result_contexts_bytes
        need_equal "$nonpayload" "$((fixed_contexts - fixed_payload - fixed_lookahead_payload))" fixed_result_nonpayload_bytes
        need_equal "$baseline_contexts" "$((context_bytes * 32))" baseline_result_contexts_bytes
        need_equal "$incremental_contexts" "$((context_bytes * 32))" incremental_result_contexts_bytes
        need_equal "$incremental_nonpayload" "$(((context_bytes - 128) * 32))" incremental_result_nonpayload_bytes
        need_equal "$fixed_waiter_masks" 8192 fixed_result_waiter_mask_bytes
        need_equal "$baseline_waiter_masks" 4096 baseline_result_waiter_mask_bytes
        need_equal "$incremental_waiter_masks" 4096 incremental_result_waiter_mask_bytes
        need_equal "$incremental_total_nonpayload" "$((incremental_nonpayload + incremental_waiter_masks))" incremental_result_total_nonpayload_bytes
        need_equal "$incremental_total_state" "$((incremental_contexts + incremental_waiter_masks))" incremental_result_total_state_bytes

        overlap=$(trace_sum "$trace" read_write_overlap_ticks)
        dual=$(trace_sum "$trace" dual_region_overlap_ticks)
        write_only=$(trace_sum "$trace" serialized_write_only_ticks)
        traffic_r1=$(trace_sum "$trace" traffic_hwm_r1)
        if [[ $contexts -eq 32 ]]; then
            need_equal "$traffic_r1" 0 inactive_region_traffic
            need_equal "$dual" 0 inactive_region_overlap
        else
            [[ $traffic_r1 -gt 0 ]]
        fi

        ticks=$(stat_value "$stats" simTicks)
        stalls=$(stat_value "$stats" system.maa.I0_IND_SoaJitContextStalls)
        hwm=$(stat_value "$stats" system.maa.I0_IND_SoaJitContextHighWater)
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$repetition" "$arm" "$contexts" "$ticks" "$stalls" \
            "$hwm" "$overlap" "$dual" "$write_only" "$traffic_r1" \
            "$fixed_payload" "$active_payload" "$fixed_lookahead_payload" \
            "$active_lookahead_payload" "$fixed_transient_write_payload" \
            "$active_transient_write_payload" "$fixed_contexts" \
            "$nonpayload" "$baseline_contexts" "$incremental_contexts" \
            "$incremental_nonpayload" "$incremental_waiter_masks" \
            "$incremental_total_nonpayload" "$incremental_total_state" \
            >>"$out/results.tsv"
    done
done

{
    printf 'source_commit='; git -C "$root" rev-parse HEAD
    printf 'source_diff_sha256='; git -C "$root" diff --binary | sha256sum | awk '{print $1}'
    printf 'gem5_sha256='; sha256sum "$gem5" | awk '{print $1}'
    printf 'guest_sha256='; sha256sum "$guest" | awk '{print $1}'
    printf 'checkpoint=%s\nexpected_hash=%s\n' "$checkpoint" "$expected_hash"
    printf 'treatment_delta=maa_soa_jit_active_contexts:32->64\n'
    printf 'fixed_controls=predicate_credits=16,value_owners=64,lookahead=8,index_lines=8,apply_lanes=1,pre_a=1,sequential_prefetch=0\n'
    printf 'timeout_seconds=%s\n' "$timeout_seconds"
} >"$out/manifest.txt"

awk '
    NR == 1 { next }
    $2 == "control" { control[$1] = $4 }
    $2 == "treatment" {
        printf "repetition=%s control=%s treatment=%s speedup=%.9fx delta_ticks=%d\n",
               $1, control[$1], $4, control[$1] / $4, control[$1] - $4
    }
' "$out/results.tsv" >"$out/summary.txt"

cat "$out/results.tsv"
cat "$out/summary.txt"
echo 'SOA_JIT_RESULT_PIPELINE_EVIDENCE_COMPLETE'
