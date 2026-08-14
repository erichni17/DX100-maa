#!/usr/bin/env bash
set -euo pipefail

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
committed_source_paths=(
    configs/common/MAAConfig.py configs/common/Options.py
    experiments/scripts/run_soa_jit_descriptor_value_carry_fill_credits_sweep.sh
    src/mem/MAA/IndirectAccess.cc src/mem/MAA/IndirectAccess.hh
    src/mem/MAA/MAA.cc src/mem/MAA/MAA.hh src/mem/MAA/MAA.py
    src/mem/MAA/Tables.cc src/mem/MAA/Tables.hh
    tests/maa/soa_jit_descriptor_value_carry_test.cc
    tests/test_soa_jit_descriptor_value_carry_contract.py
)

[[ -x $gem5 ]] || { echo "missing gem5: $gem5" >&2; exit 2; }
[[ -f $config && -f $ramulator && -d $checkpoint && -x $guest ]] || exit 2
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
[[ -z $(git -C "$root" status --porcelain) ]] || {
    echo "refusing uncommitted source provenance" >&2
    exit 2
}
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
    --maa_soa_jit_value_prefetch_credits=0 --maa_soa_jit_active_value_owners=32
    --maa_soa_jit_apply_lanes=1 --cmd "$guest" --options soa
)

stat_value() {
    awk -v key="$2" '$1 == key { value = $2 } END { print value }' "$1"
}

trace_sum() {
    awk -v key="$2" '
        /event=soa_jit_complete / {
            for (i = 1; i <= NF; ++i) {
                split($i, pair, "=")
                if (pair[1] == key) sum += pair[2]
            }
        }
        END { print sum + 0 }
    ' "$1"
}

trace_max() {
    awk -v key="$2" '
        /event=soa_jit_complete / {
            for (i = 1; i <= NF; ++i) {
                split($i, pair, "=")
                if (pair[1] == key && pair[2] > maximum) maximum = pair[2]
            }
        }
        END { print maximum + 0 }
    ' "$1"
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

printf 'rep\tarm\tcredits\tsimTicks\tfill_cycles\trequest_cycles\tlater_value_reads\tcarry_fill_reads\towner_hwm\tselected\tentry_incremental_bytes\tpool_modeled_bytes\tpool_host_bytes\n' >"$out/results.tsv"
for rep in 1 2; do
    for arm in control carry_c1 carry_c4 carry_c8 carry_c16; do
        credits=${arm#carry_c}
        treatment=false
        if [[ $arm == control ]]; then
            credits=1
        else
            treatment=true
        fi
        run="$out/${arm}_r${rep}"
        mkdir "$run"
        extra=(--maa_soa_jit_descriptor_value_carry_fill_credits="$credits")
        if [[ $treatment == true ]]; then
            extra+=(--maa_soa_jit_descriptor_value_carry)
        fi
        run_gem5 "$gem5" --listener-mode=off --outdir="$run" \
            --debug-flags=MAAVirtualTrace,MAAReorderTrace \
            --debug-file=soa_jit_trace.log \
            "${common[@]}" "${extra[@]}" >"$run/restore.log" 2>&1

        [[ $(grep -Ec '^HYBRID_RMW_SOA_RESULT .*output_hash='"$expected_hash"' .*errors=0' "$run/restore.log" || true) -eq 1 ]]
        grep -Fqx 'ROI Ended' "$run/restore.log"
        [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' "$run/restore.log" || true) -eq 1 ]]
        [[ $(grep -Eic 'panic|fatal|assert|segmentation fault|aborted' "$run/restore.log" || true) -eq 0 ]]
        grep -Fq "soa_jit_descriptor_value_carry=$treatment" "$run/config.ini"
        grep -Fq "soa_jit_descriptor_value_carry_fill_credits=$credits" "$run/config.ini"
        for knob in soa_jit_active_contexts=32 soa_jit_predicate_active_credits=16 soa_jit_value_lookahead=8 virtual_index_buffer_lines=8 soa_jit_apply_lanes=1 soa_jit_active_value_owners=32 soa_jit_value_prefetch_credits=0; do
            grep -Fq "$knob" "$run/config.ini"
        done

        stats="$run/stats.txt"
        trace="$run/soa_jit_trace.log"
        terminal=$(stat_value "$stats" system.maa.I0_IND_SoaJitTerminalCompletions)
        selected=$(stat_value "$stats" system.maa.I0_IND_SoaJitSelected)
        rejected=$(stat_value "$stats" system.maa.I0_IND_SoaJitPredicateRejected)
        value_issues=$(stat_value "$stats" system.maa.I0_IND_SoaJitValueReadIssues)
        value_responses=$(stat_value "$stats" system.maa.I0_IND_SoaJitValueReadResponses)
        deliveries=$(stat_value "$stats" system.maa.I0_IND_SoaJitValueDeliveries)
        lookahead_issues=$(stat_value "$stats" system.maa.I0_IND_SoaJitLookaheadIssues)
        carry_issues=$(awk '
            /event=soa_jit_complete / {
                for (i = 1; i <= NF; ++i) if ($i ~ /^carry_fill_reads=/) {
                    split($i, sides, "="); split(sides[2], counts, "/")
                    sum += counts[1]
                }
            }
            END { print sum + 0 }
        ' "$trace")
        carry_responses=$(awk '
            /event=soa_jit_complete / {
                for (i = 1; i <= NF; ++i) if ($i ~ /^carry_fill_reads=/) {
                    split($i, sides, "="); split(sides[2], counts, "/")
                    sum += counts[2]
                }
            }
            END { print sum + 0 }
        ' "$trace")
        carried_operands=$(trace_sum "$trace" carried_operands)
        carried_applies=$(trace_sum "$trace" carried_applies)
        owner_hwm=$(trace_max "$trace" carry_fill_owner_hwm)
        need_equal "$terminal" 2 terminal_ledger
        need_equal "$selected" 29689 selected_exact
        need_equal "$rejected" 3079 rejected_exact
        need_equal "$((selected + rejected))" 32768 predicate_ledger
        need_equal "$value_issues" "$value_responses" value_ledger
        need_equal "$deliveries" "$lookahead_issues" delivery_ledger
        need_equal "$lookahead_issues" "$selected" selected_ledger
        need_equal "$carry_issues" "$carry_responses" carry_read_ledger
        [[ $(grep -Ec 'event=soa_jit_complete schema=3 .*terminal=1' "$trace" || true) -eq 2 ]]
        [[ $(grep -Ec 'schema=dx100.reorder_summary.v1 .*reconciled=1 classification=inherited/partitioned' "$trace" || true) -eq 2 ]]

        if [[ $arm == control ]]; then
            need_equal "$carry_issues" 0 control_carry_reads
            need_equal "$carried_operands" 0 control_carried_operands
            need_equal "$carried_applies" 0 control_carried_applies
            need_equal "$owner_hwm" 0 control_owner_hwm
        else
            need_equal "$value_issues" 0 treatment_later_value_reads
            need_equal "$carry_issues" 2048 treatment_fill_reads
            need_equal "$carried_operands" "$selected" treatment_carried_operands
            need_equal "$carried_applies" "$selected" treatment_carried_applies
            [[ $owner_hwm -gt 0 && $owner_hwm -le $credits ]]
        fi

        storage=$(grep -m1 'event=soa_jit_storage schema=3 ' "$trace")
        entry_bytes=$(sed -n 's/.*carry_entry_incremental_bytes=\([0-9][0-9]*\).*/\1/p' <<<"$storage")
        pool_bytes=$(sed -n 's/.*carry_unit_incremental_modeled_bytes=\([0-9][0-9]*\).*/\1/p' <<<"$storage")
        pool_host_bytes=$(sed -n 's/.*carry_unit_host_bytes=\([0-9][0-9]*\).*/\1/p' <<<"$storage")
        offset_entry_bytes=$(sed -n 's/.*offset_entry_bytes=\([0-9][0-9]*\).*/\1/p' <<<"$storage")
        need_equal "$entry_bytes" 0 entry_growth
        need_equal "$pool_bytes" 1200 pool_modeled_growth
        need_equal "$pool_host_bytes" 1280 pool_host_growth
        need_equal "$offset_entry_bytes" 16 offset_entry_size
        [[ $(grep -Ec 'event=soa_jit_storage schema=3 .*carry_fill_owner_modeled_bytes=75 .*carry_fill_owner_host_bytes=80 .*carry_fill_max_owners=16 .*carry_fill_active_credits='"$credits" "$trace" || true) -eq 2 ]]

        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$rep" "$arm" "$credits" "$(stat_value "$stats" simTicks)" \
            "$(stat_value "$stats" system.maa.I0_IND_CyclesFill)" \
            "$(stat_value "$stats" system.maa.I0_IND_CyclesRequest)" \
            "$value_issues" "$carry_issues" "$owner_hwm" "$selected" \
            "$entry_bytes" "$pool_bytes" "$pool_host_bytes" \
            >>"$out/results.tsv"
    done
done

awk '
    NR == 1 { next }
    {
        key = $2
        if (!(key in ticks)) {
            ticks[key] = $4; fill[key] = $5; request[key] = $6
            reads[key] = $7; carry[key] = $8; hwm[key] = $9
        } else if (ticks[key] != $4 || fill[key] != $5 ||
                   request[key] != $6 || reads[key] != $7 ||
                   carry[key] != $8 || hwm[key] != $9) {
            print "nondeterministic metrics for " key > "/dev/stderr"
            exit 1
        }
    }
' "$out/results.tsv"

{
    printf 'source_commit='; git -C "$root" rev-parse HEAD
    printf 'committed_source_archive_sha256='
    git -C "$root" archive --format=tar HEAD -- \
        "${committed_source_paths[@]}" | sha256sum | awk '{print $1}'
    printf 'committed_source_paths=%s\n' "${committed_source_paths[*]}"
    printf 'worktree_clean='; [[ -z $(git -C "$root" status --porcelain) ]] && echo true || echo false
    printf 'gem5_sha256='; sha256sum "$gem5" | awk '{print $1}'
    printf 'guest_sha256='; sha256sum "$guest" | awk '{print $1}'
    printf 'checkpoint=%s\nexpected_hash=%s\n' "$checkpoint" "$expected_hash"
    printf 'repetitions=2\narms=control,carry_c1,carry_c4,carry_c8,carry_c16\n'
    printf 'treatment_delta=maa_soa_jit_descriptor_value_carry=true,fill_credits=1/4/8/16\n'
    printf 'storage=offset_entry_bytes=16,carry_entry_incremental_bytes=0,owner_modeled_bytes=75,owner_host_bytes=80,fixed_owners=16,pool_modeled_bytes=1200,pool_host_bytes=1280,active_selector_bits=2,carry_enable_bits=1\n'
    printf 'fixed_controls=contexts=32,predicate_credits=16,lookahead=8,index_lines=8,apply_lanes=1,value_owners=32,value_prefetch_credits=0,value_cache_enable=true\n'
    printf 'timeout_seconds=%s\n' "$timeout_seconds"
} >"$out/manifest.txt"

awk '
    NR == 1 { next }
    $2 == "control" { control[$1] = $4; next }
    {
        beats = $4 < control[$1] ? 1 : 0
        printf "rep=%s arm=%s credits=%s control_ticks=%s treatment_ticks=%s speedup=%.6fx beats_control=%s fill_cycles=%s request_cycles=%s later_value_reads=%s carry_fill_reads=%s owner_hwm=%s\n", $1, $2, $3, control[$1], $4, control[$1]/$4, beats, $5, $6, $7, $8, $9
        if (beats) wins[$2]++
    }
    END {
        decision = "reject"
        for (arm in wins) if (wins[arm] == 2) decision = "promote:" arm
        print "decision=" decision
    }
' "$out/results.tsv" >"$out/summary.txt"
cat "$out/results.tsv"
cat "$out/summary.txt"
echo 'SOA_JIT_DESCRIPTOR_VALUE_CARRY_FILL_CREDITS_SWEEP_PASS'
