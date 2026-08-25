#!/usr/bin/env bash
# Candidate-only, trace-free full GAPBS SSSP S22 correctness/performance gate.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
# These are deliberately named overrides: a physical-SPD aperture candidate
# must be identified by both its executable and immutable digest.
default_gem5=/data1/nier/dx100-binaries/gem5-1e079112469892681d661925db09ccfbc845d1a2ce45c79e1d9a4902c19a9863.opt
default_gem5_sha256=1e079112469892681d661925db09ccfbc845d1a2ce45c79e1d9a4902c19a9863
gem5=${SSSP_CANDIDATE_GEM5:-$default_gem5}
gem5_sha256=${SSSP_CANDIDATE_GEM5_SHA256:-$default_gem5_sha256}
aperture_candidate_gate=${SSSP_APERTURE_CANDIDATE_GATE:-false}
gem5=$(realpath -m "$gem5")
frozen_ramulator=/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/input/libramulator.so
frozen_ramulator_sha256=76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753
config="$root/configs/deprecated/example/se.py"
ramulator_config="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
source_file="$root/benchmarks/gapbs/src/sssp.cc"
frozen_sweep="$root/experiments/analysis/physical_tile_sweep_baseline_20260822.json"
frozen_sweep_sha256=d8cd2afe18de4f7983b1d9d59a0ea04e102a51bc7146a9d85c3c9a19cc73d069

external_graph=/data1/nier/worktrees/DX100-full-tile-sweep-20260720/benchmarks/gapbs/serialized_graph_22.wsg
external_graph_sha256=23eb25e34343334976554071a8184f7b03358fe1892ba44cd2f5a38369f4eebc
external_graph_bytes=1090514493
native_out=/data1/nier/dx100-runs/2026-07-20-full-tile-sweep/repair3-validation/gapbs/sssp_s22_t16384_m2GB_gem5.opt.ovl_base_sha256_1ff4a396b98d6c838f695c4cbd631ca16e7ed12407365f17707bcf6df93e1343
native_first_roi_ticks=758524789379
oracle='SSSP_FINGERPRINT vertices=4194304 reached=4194304 unreachable=0 distance_sum=569278395 max_distance=258 hash_a=aaf3a6a5d4662d36 hash_b=9ffcf4962b364007 triangle_violations=0 missing_predecessors=0 nonpositive_weights=0 negative_distances=0 result=PASS'

usage() {
    echo "usage: $0 OUTDIR" >&2
    echo "       $0 --validate OUTDIR" >&2
    exit 2
}

hash_value() {
    sha256sum "$1" | awk '{print $1}'
}

require_hash() {
    local path=$1 expected=$2
    [[ -f $path && $(hash_value "$path") == "$expected" ]]
}

require_boolean() {
    [[ $1 == true || $1 == false ]]
}

manifest_value() {
    local manifest=$1 key=$2
    awk -F= -v key="$key" '$1 == key {print substr($0, length(key) + 2)}' \
        "$manifest"
}

terminal_value() {
    local line=$1 key=$2
    tr ' ' '\n' <<<"$line" | awk -F= -v key="$key" \
        '$1 == key {print substr($0, length(key) + 2)}'
}

hash_tree() {
    local directory=$1
    (
        cd "$directory"
        find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
    )
}

stat_sum() {
    local stats=$1 suffix=$2
    awk -v suffix="$suffix" '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ ("_" suffix "$") {
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

validate_evidence() (
    set -euo pipefail
    local out=$1 require_wrapper=${2:-false}
    local manifest="$out/candidate.manifest"
    local checkpoint_log="$out/checkpoint.log"
    local restore="$out/run/restore.log"
    local stats="$out/run/stats.txt"
    local terminal

    [[ -s $manifest && -s $checkpoint_log && -s $restore && -s $stats ]]
    [[ $(<"$out/checkpoint.exit") == 0 ]]
    [[ $(<"$out/run/restore.exit") == 0 ]]
    if [[ $require_wrapper == true ]]; then
        [[ -s $out/wrapper.status ]]
        grep -Fqx 'exit_code=0' "$out/wrapper.status"
        grep -Fqx 'PASS' "$out/gate.complete"
        grep -Fqx 'validation=PASS' "$out/result.txt"
    fi

    require_hash "$gem5" "$gem5_sha256"
    [[ $(manifest_value "$manifest" candidate_gem5_path) == "$gem5" ]]
    [[ $(manifest_value "$manifest" candidate_gem5_sha256) == "$gem5_sha256" ]]
    [[ $(manifest_value "$manifest" aperture_candidate_gate) == \
        "$aperture_candidate_gate" ]]
    grep -Fqx "$gem5_sha256  $gem5" \
        "$out/provenance/artifacts.before.sha256"
    grep -Fqx "$gem5_sha256  $gem5" \
        "$out/provenance/artifacts.after.sha256"
    require_hash "$out/input/serialized_graph_22.wsg" \
        "$external_graph_sha256"
    [[ $(stat -Lc %s "$out/input/serialized_graph_22.wsg") -eq \
        $external_graph_bytes ]]
    require_hash "$out/bin/sssp_maa_2G_old_result_hybrid_fp" \
        "$(manifest_value "$manifest" candidate_guest_sha256)"
    require_hash "$frozen_sweep" "$frozen_sweep_sha256"

    hash_tree "$out/checkpoint" \
        >"$out/provenance/checkpoint.callback.files.sha256.tmp"
    mv "$out/provenance/checkpoint.callback.files.sha256.tmp" \
        "$out/provenance/checkpoint.callback.files.sha256"
    cmp -s "$out/provenance/checkpoint.before.files.sha256" \
        "$out/provenance/checkpoint.after.files.sha256"
    cmp -s "$out/provenance/checkpoint.before.files.sha256" \
        "$out/provenance/checkpoint.callback.files.sha256"
    [[ $(<"$out/provenance/checkpoint.before.identity.sha256") == \
        "$(hash_value "$out/provenance/checkpoint.before.files.sha256")" ]]
    [[ $(<"$out/provenance/checkpoint.after.identity.sha256") == \
        "$(<"$out/provenance/checkpoint.before.identity.sha256")" ]]

    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
        "$checkpoint_log" || true) -eq 1 ]]
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
        "$restore" || true) -eq 1 ]]
    [[ $(grep -Fxc 'ROI End!!!' "$restore" || true) -eq 1 ]]
    [[ $(grep -Fxc "$oracle" "$restore" || true) -eq 1 ]]
    [[ $(grep -Ec '^SSSP_OLD_RESULT_HYBRID_TERMINAL ' "$restore" || true) \
        -eq 1 ]]
    ! grep -Eiq 'user[ -]?interrupt|interrupt received' \
        "$checkpoint_log" "$restore"
    ! grep -Eiq 'panic|fatal|assert|abort|segmentation fault|error:' \
        "$checkpoint_log" "$restore"
    ! grep -Eq 'SSSP_FINGERPRINT|SSSP_OLD_RESULT_HYBRID_TERMINAL|ROI End!!!' \
        "$checkpoint_log"

    local resolved
    for resolved in \
        num_maas=1 num_indirect_units_per_maa=4 \
        num_tile_elements=16384 physical_tile_elements=4096 \
        num_offset_table_entries=16384 \
        num_offset_table_epoch_entries=16384 \
        num_initial_row_table_slices=32 \
        soa_jit_old_result_pressure_policy=densest \
        soa_jit_old_result_partial_credits=4 \
        soa_jit_active_contexts=8 soa_jit_active_value_owners=64 \
        soa_jit_predicate_active_credits=1 soa_jit_value_lookahead=1 \
        soa_jit_value_prefetch_credits=0 soa_jit_apply_lanes=1 \
        soa_jit_value_cache_enable=true \
        soa_jit_pre_a_value_lookahead=true; do
        grep -Fqx "$resolved" "$out/run/config.ini"
    done
    [[ $(grep -Ec '^\[system\.mem_ctrls[01]\]$' \
        "$out/run/config.ini" || true) -eq 2 ]]

    [[ $(grep -Ec '^---------- Begin Simulation Statistics' "$stats" || true) \
        -eq 2 ]]
    [[ $(grep -Ec '^---------- End Simulation Statistics' "$stats" || true) \
        -eq 2 ]]
    mapfile -t sim_ticks < <(awk '$1 == "simTicks" {print $2}' "$stats")
    [[ ${#sim_ticks[@]} -eq 2 ]]
    [[ ${sim_ticks[0]} =~ ^[1-9][0-9]*$ ]]
    [[ ${sim_ticks[1]} =~ ^[1-9][0-9]*$ ]]
    (( sim_ticks[1] >= sim_ticks[0] ))

    terminal=$(grep '^SSSP_OLD_RESULT_HYBRID_TERMINAL ' "$restore")
    [[ $(terminal_value "$terminal" treatment) == old_result_hybrid ]]
    [[ $(terminal_value "$terminal" logical_reorder_words) == 16384 ]]
    [[ $(terminal_value "$terminal" physical_spd_words) == 4096 ]]
    [[ $(terminal_value "$terminal" row_table_slices) == 32 ]]
    [[ $(terminal_value "$terminal" predicate_span) == coherent_aligned ]]
    [[ $(terminal_value "$terminal" old_result_span) == coherent_aligned ]]
    [[ $(terminal_value "$terminal" duplicate_order) == \
        legacy_physical_pages ]]
    [[ $(terminal_value "$terminal" host_spd_reads) == 0 ]]
    [[ $(terminal_value "$terminal" hidden_result_payload_bytes) == 0 ]]
    [[ $(terminal_value "$terminal" counts_close) == 1 ]]

    local eligible routed index_pages value_pages old_words legacy_words
    eligible=$(terminal_value "$terminal" eligible_windows)
    routed=$(terminal_value "$terminal" routed_windows)
    index_pages=$(terminal_value "$terminal" index_publish_pages)
    value_pages=$(terminal_value "$terminal" value_publish_pages)
    old_words=$(terminal_value "$terminal" old_result_words)
    legacy_words=$(terminal_value "$terminal" legacy_words)
    for value in "$eligible" "$routed" "$index_pages" "$value_pages" \
        "$old_words" "$legacy_words"; do
        [[ $value =~ ^[0-9]+$ ]]
    done
    (( routed > 0 && routed <= eligible ))
    (( index_pages == routed * 4 ))
    (( value_pages == routed * 4 ))
    (( old_words == routed * 16384 ))

    local instructions terminals selected rejected predicate_issues
    local predicate_responses index_words value_issues value_responses
    local value_fills cached value_hits deliveries lookahead_issues
    local lookahead_responses pre_a_issues pre_a_ready pre_a_uses
    local a_read_issues a_read_responses a_write_issues a_write_responses
    local captures old_issues old_responses active_contexts active_owners
    local apply_lanes partial_limits dense_policy
    instructions=$(stat_sum "$stats" IND_SoaJitInstructions)
    terminals=$(stat_sum "$stats" IND_SoaJitTerminalCompletions)
    selected=$(stat_sum "$stats" IND_SoaJitSelected)
    rejected=$(stat_sum "$stats" IND_SoaJitPredicateRejected)
    predicate_issues=$(stat_sum "$stats" IND_SoaJitPredicateLineReads)
    predicate_responses=$(stat_sum "$stats" IND_SoaJitPredicateLineResponses)
    index_words=$(stat_sum "$stats" IND_VirtIndexWords)
    value_issues=$(stat_sum "$stats" IND_SoaJitValueReadIssues)
    value_responses=$(stat_sum "$stats" IND_SoaJitValueReadResponses)
    value_fills=$(stat_sum "$stats" IND_SoaJitValueFills)
    cached=$(stat_sum "$stats" IND_SoaJitValueCachedResponses)
    value_hits=$(stat_sum "$stats" IND_SoaJitValueHits)
    deliveries=$(stat_sum "$stats" IND_SoaJitValueDeliveries)
    lookahead_issues=$(stat_sum "$stats" IND_SoaJitLookaheadIssues)
    lookahead_responses=$(stat_sum "$stats" IND_SoaJitLookaheadResponses)
    pre_a_issues=$(stat_sum "$stats" IND_SoaJitPreAValueIssues)
    pre_a_ready=$(stat_sum "$stats" IND_SoaJitPreAValueReadyAtAResponse)
    pre_a_uses=$(stat_sum "$stats" IND_SoaJitPreAValueUses)
    a_read_issues=$(stat_sum "$stats" IND_SoaJitAReadIssues)
    a_read_responses=$(stat_sum "$stats" IND_SoaJitAReadResponses)
    a_write_issues=$(stat_sum "$stats" IND_SoaJitAWriteIssues)
    a_write_responses=$(stat_sum "$stats" IND_SoaJitAWriteResponses)
    captures=$(stat_sum "$stats" IND_SoaJitOldResultCaptures)
    old_issues=$(stat_sum "$stats" IND_SoaJitOldResultWriteIssues)
    old_responses=$(stat_sum "$stats" IND_SoaJitOldResultWriteResponses)
    active_contexts=$(stat_sum "$stats" IND_SoaJitActiveContexts)
    active_owners=$(stat_sum "$stats" IND_SoaJitActiveValueOwners)
    apply_lanes=$(stat_sum "$stats" IND_SoaJitActiveApplyLanes)
    partial_limits=$(stat_sum "$stats" IND_SoaJitOldResultPartialCreditLimit)
    dense_policy=$(stat_sum "$stats" IND_SoaJitOldResultDensePolicy)

    (( instructions > 0 && instructions == routed && terminals == routed ))
    (( selected > 0 && captures == selected ))
    (( index_words == selected + rejected ))
    (( predicate_issues == predicate_responses ))
    (( value_issues > 0 && value_issues == value_responses ))
    (( value_responses == value_fills && value_fills == cached ))
    (( value_hits > 0 && deliveries == selected ))
    (( lookahead_issues == lookahead_responses ))
    (( pre_a_issues > 0 && pre_a_issues == pre_a_uses ))
    (( pre_a_ready <= pre_a_issues ))
    (( a_read_issues > 0 && a_read_issues == a_read_responses ))
    (( a_write_issues > 0 && a_write_issues == a_write_responses ))
    (( a_read_issues == a_write_issues ))
    (( old_issues > 0 && old_issues == old_responses ))
    (( active_contexts == instructions * 8 ))
    (( active_owners == instructions * 64 ))
    (( apply_lanes == instructions ))
    (( partial_limits == instructions * 4 ))
    (( dense_policy == instructions ))

    if [[ $aperture_candidate_gate == true ]]; then
        local boundary_drops aperture_rejections
        boundary_drops=$(stat_sum "$stats" cpu_spd_boundary_prefetch_drops)
        aperture_rejections=$(stat_sum "$stats" cpu_spd_out_of_range_rejections)
        [[ $boundary_drops =~ ^[0-9]+$ && $aperture_rejections =~ ^[0-9]+$ ]]
        [[ $(manifest_value "$manifest" \
            first_window_cpu_spd_boundary_prefetch_drops) == "$boundary_drops" ]]
        [[ $(manifest_value "$manifest" \
            first_window_cpu_spd_out_of_range_rejections) == "$aperture_rejections" ]]
        (( boundary_drops > 0 && aperture_rejections == 0 ))
    fi
)

write_result() {
    local out=$1 restore="$1/run/restore.log" stats="$1/run/stats.txt"
    local terminal eligible routed routing_status boundary_drops aperture_rejections
    terminal=$(grep '^SSSP_OLD_RESULT_HYBRID_TERMINAL ' "$restore")
    eligible=$(terminal_value "$terminal" eligible_windows)
    routed=$(terminal_value "$terminal" routed_windows)
    if (( routed == eligible )); then
        routing_status=all_eligible_windows_routed
    else
        routing_status=eligible_subset_routed_fallbacks_preserved
    fi
    if [[ $aperture_candidate_gate == true ]]; then
        boundary_drops=$(stat_sum "$stats" cpu_spd_boundary_prefetch_drops)
        aperture_rejections=$(stat_sum "$stats" cpu_spd_out_of_range_rejections)
    else
        boundary_drops=not_checked
        aperture_rejections=not_checked
    fi
    {
        printf 'schema=dx100.sssp.old_result_hybrid.full.result.v1\n'
        printf 'validation=PASS\n'
        printf 'candidate_first_roi_simTicks=%s\n' \
            "$(awk '$1 == "simTicks" {print $2; exit}' "$stats")"
        printf 'candidate_final_simTicks=%s\n' \
            "$(awk '$1 == "simTicks" {value=$2} END {print value}' "$stats")"
        printf 'frozen_native16_first_roi_simTicks=%s\n' \
            "$native_first_roi_ticks"
        printf 'comparison_status=measured_candidate_unpromoted\n'
        printf 'eligible_windows=%s\nrouted_windows=%s\n' "$eligible" "$routed"
        printf 'routing_status=%s\n' "$routing_status"
        printf 'legacy_words=%s\n' \
            "$(terminal_value "$terminal" legacy_words)"
        printf 'soa_jit_instructions=%s\n' \
            "$(stat_sum "$stats" IND_SoaJitInstructions)"
        printf 'soa_jit_terminals=%s\n' \
            "$(stat_sum "$stats" IND_SoaJitTerminalCompletions)"
        printf 'old_result_write_issues=%s\n' \
            "$(stat_sum "$stats" IND_SoaJitOldResultWriteIssues)"
        printf 'old_result_write_responses=%s\n' \
            "$(stat_sum "$stats" IND_SoaJitOldResultWriteResponses)"
        printf 'candidate_checkpoint_sha256=%s\n' \
            "$(<"$out/provenance/checkpoint.before.identity.sha256")"
        printf 'candidate_guest_sha256=%s\n' \
            "$(manifest_value "$out/candidate.manifest" candidate_guest_sha256)"
        printf 'input_sha256=%s\n' "$external_graph_sha256"
        printf 'aperture_candidate_gate=%s\n' "$aperture_candidate_gate"
        printf 'cpu_spd_boundary_prefetch_drops=%s\n' "$boundary_drops"
        printf 'cpu_spd_out_of_range_rejections=%s\n' "$aperture_rejections"
    } >"$out/result.txt"
}

record_aperture_stats() {
    local out=$1 stats="$1/run/stats.txt" boundary_drops aperture_rejections
    if [[ $aperture_candidate_gate == true ]]; then
        boundary_drops=$(stat_sum "$stats" cpu_spd_boundary_prefetch_drops)
        aperture_rejections=$(stat_sum "$stats" cpu_spd_out_of_range_rejections)
    else
        boundary_drops=not_checked
        aperture_rejections=not_checked
    fi
    {
        printf 'first_window_cpu_spd_boundary_prefetch_drops=%s\n' \
            "$boundary_drops"
        printf 'first_window_cpu_spd_out_of_range_rejections=%s\n' \
            "$aperture_rejections"
    } >>"$out/candidate.manifest"
}

validate_callback() {
    local out=$1 rc
    set +e
    validate_evidence "$out" true
    rc=$?
    set -e
    {
        printf 'schema=dx100.sssp.old_result_hybrid.full.callback.v1\n'
        printf 'validation_exit=%s\n' "$rc"
        printf 'validated_at=%s\n' "$(date -Ins)"
    } >"$out/callback.validation.status.tmp"
    mv "$out/callback.validation.status.tmp" \
        "$out/callback.validation.status"
    (( rc == 0 ))
}

adopt_validation_manifest() {
    local out=$1 manifest="$1/candidate.manifest"
    [[ -s $manifest ]]
    if [[ -z ${SSSP_CANDIDATE_GEM5+x} ]]; then
        gem5=$(manifest_value "$manifest" candidate_gem5_path)
    fi
    if [[ -z ${SSSP_CANDIDATE_GEM5_SHA256+x} ]]; then
        gem5_sha256=$(manifest_value "$manifest" candidate_gem5_sha256)
    fi
    if [[ -z ${SSSP_APERTURE_CANDIDATE_GATE+x} ]]; then
        aperture_candidate_gate=$(manifest_value \
            "$manifest" aperture_candidate_gate)
    fi
    gem5=$(realpath -m "$gem5")
}

if [[ $# -eq 2 && $1 == --validate ]]; then
    validation_out=$(realpath -m "$2")
    adopt_validation_manifest "$validation_out"
    require_boolean "$aperture_candidate_gate" || {
        echo "frozen aperture_candidate_gate must be true or false" >&2
        exit 2
    }
    validate_callback "$validation_out"
    exit
fi

require_boolean "$aperture_candidate_gate" || {
    echo "SSSP_APERTURE_CANDIDATE_GATE must be true or false" >&2
    exit 2
}
[[ $# -eq 1 ]] || usage

out=$(realpath -m "$1")
[[ ! -e $out ]] || {
    echo "refusing existing output: $out" >&2
    exit 2
}
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence run from a dirty source tree" >&2
    git -C "$root" status --short >&2
    exit 1
}
[[ -x $gem5 ]] || { echo "missing candidate gem5: $gem5" >&2; exit 2; }
require_hash "$gem5" "$gem5_sha256"
require_hash "$frozen_ramulator" "$frozen_ramulator_sha256"
require_hash "$frozen_sweep" "$frozen_sweep_sha256"
require_hash "$external_graph" "$external_graph_sha256"
[[ $(stat -Lc %s "$external_graph") -eq $external_graph_bytes ]]
[[ -s $native_out/run.log && -s $native_out/stats.txt ]]
[[ $(awk '$1 == "simTicks" {print $2; exit}' "$native_out/stats.txt") == \
    "$native_first_roi_ticks" ]]
[[ $(grep -Fxc "$oracle" "$native_out/run.log" || true) -eq 1 ]]

mkdir -p "$out/bin" "$out/input" "$out/checkpoint" "$out/run" \
    "$out/provenance"

write_wrapper_status() {
    local rc=$?
    trap - EXIT
    {
        printf 'schema=dx100.sssp.old_result_hybrid.full.wrapper.v1\n'
        printf 'exit_code=%s\n' "$rc"
        printf 'finished_at=%s\n' "$(date -Ins)"
    } >"$out/wrapper.status.tmp"
    mv "$out/wrapper.status.tmp" "$out/wrapper.status"
    exit "$rc"
}
trap write_wrapper_status EXIT

export LD_LIBRARY_PATH="$(dirname "$frozen_ramulator"):${LD_LIBRARY_PATH:-}"
resolved_ramulator=$(ldd "$gem5" | awk '$1 == "libramulator.so" {print $3}')
[[ $(realpath "$resolved_ramulator") == $(realpath "$frozen_ramulator") ]]

graph="$out/input/serialized_graph_22.wsg"
cp --reflink=auto --preserve=mode,timestamps "$external_graph" "$graph"
chmod 0444 "$graph"
require_hash "$graph" "$external_graph_sha256"

guest="$out/bin/sssp_maa_2G_old_result_hybrid_fp"
cxx=${CXX:-g++}
"$cxx" -I"$root/benchmarks/gapbs/src" -I"$root/benchmarks/API" \
    -I"$root/include" -I"$root/util/m5/src" -std=c++11 -O3 -Wall \
    -Wextra -Werror -Wno-ignored-qualifiers -Wno-unused-parameter -fopenmp \
    -DGEM5 -DMAA -DNUM_CORES=4 -DNUM_TILES_PER_CORE=8 \
    -DTILE_SIZE=16384 -DMAA_CONSUMER_TILE_SIZE=4096 \
    -DMAA_MEM_SIZE=0x80000000 -DSSSP_FP_ENABLE=1 \
    -DSSSP_OLD_RESULT_HYBRID=1 \
    "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$guest"
chmod 0555 "$guest"
candidate_guest_sha256=$(hash_value "$guest")
source_commit=$(git -C "$root" rev-parse HEAD)
options="-f $graph -n 1 -v"

native_run_log_sha256=$(hash_value "$native_out/run.log")
native_stats_sha256=$(hash_value "$native_out/stats.txt")
{
    printf 'schema=dx100.sssp.old_result_hybrid.full.external.v1\n'
    printf 'graph_source_path=%s\n' "$external_graph"
    printf 'graph_sha256=%s\ngraph_bytes=%s\n' \
        "$external_graph_sha256" "$external_graph_bytes"
    printf 'native_options=-f GRAPH -n 1 -v\n'
    printf 'oracle=%s\n' "$oracle"
    printf 'native16_first_roi_simTicks=%s\n' "$native_first_roi_ticks"
    printf 'native16_raw_out=%s\n' "$native_out"
    printf 'native16_run_log_sha256=%s\n' "$native_run_log_sha256"
    printf 'native16_stats_sha256=%s\n' "$native_stats_sha256"
    printf 'physical_sweep_manifest=%s\n' "$frozen_sweep"
    printf 'physical_sweep_manifest_sha256=%s\n' "$frozen_sweep_sha256"
    printf 'native_checkpoint_execution=not_reused\n'
    printf 'native_guest_execution=not_reused\n'
} >"$out/external_reference.manifest"

{
    printf 'schema=dx100.sssp.old_result_hybrid.full.candidate.v1\n'
    printf 'source_commit=%s\nsource_path=%s\nsource_sha256=%s\n' \
        "$source_commit" "$source_file" "$(hash_value "$source_file")"
    printf 'candidate_gem5_path=%s\ncandidate_gem5_sha256=%s\n' \
        "$gem5" "$gem5_sha256"
    printf 'default_gem5_path=%s\ndefault_gem5_sha256=%s\n' \
        "$default_gem5" "$default_gem5_sha256"
    printf 'ramulator_library_path=%s\nramulator_library_sha256=%s\n' \
        "$frozen_ramulator" "$frozen_ramulator_sha256"
    printf 'candidate_guest_path=%s\ncandidate_guest_sha256=%s\n' \
        "$guest" "$candidate_guest_sha256"
    printf 'candidate_input_path=%s\ncandidate_input_sha256=%s\n' \
        "$graph" "$external_graph_sha256"
    printf 'candidate_options=-f INPUT -n 1 -v\n'
    printf 'logical_elements=16384\nphysical_tile_elements=4096\n'
    printf 'offset_table_entries=16384\noffset_table_epoch_entries=16384\n'
    printf 'row_table_slices=32\nindirect_units=4\n'
    printf 'old_result_pressure_policy=densest\nold_result_partial_credits=4\n'
    printf 'value_cache_enable=true\nactive_value_owners=64\n'
    printf 'pre_a_value_lookahead=true\nactive_contexts=8\n'
    printf 'tails_and_fallbacks=preserved\n'
    printf 'aperture_candidate_gate=%s\n' "$aperture_candidate_gate"
    printf 'native_arms=0\nfull_graph=true\ntrace=false\nwall_timeout=none\n'
} >"$out/candidate.manifest"

sha256sum "$gem5" "$frozen_ramulator" "$guest" "$graph" "$source_file" \
    "$config" "$ramulator_config" "$0" >"$out/provenance/artifacts.before.sha256"

checkpoint_command=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" "$config"
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1
    --cmd "$guest" --options "$options"
)
printf '%q ' "${checkpoint_command[@]}" >"$out/checkpoint.command"
printf '\n' >>"$out/checkpoint.command"
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${checkpoint_command[@]}" \
    >"$out/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" >"$out/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
    "$out/checkpoint.log" || true) -eq 1 ]]
! grep -Eiq 'user[ -]?interrupt|interrupt received' "$out/checkpoint.log"
! grep -Eiq 'panic|fatal|assert|abort|segmentation fault|error:' \
    "$out/checkpoint.log"
! grep -Eq 'SSSP_FINGERPRINT|SSSP_OLD_RESULT_HYBRID_TERMINAL|ROI End!!!' \
    "$out/checkpoint.log"
require_hash "$graph" "$external_graph_sha256"
require_hash "$guest" "$candidate_guest_sha256"

hash_tree "$out/checkpoint" \
    >"$out/provenance/checkpoint.before.files.sha256"
hash_value "$out/provenance/checkpoint.before.files.sha256" \
    >"$out/provenance/checkpoint.before.identity.sha256"
find "$out/checkpoint" -type f -exec chmod 0444 {} +
find "$out/checkpoint" -type d -exec chmod 0555 {} +

restore_command=(
    "$gem5" --listener-mode=off --outdir="$out/run" "$config"
    --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --checkpoint-dir="$out/checkpoint"
    --sys-clock 3.2GHz --cpu-clock 3.2GHz
    --caches --l1d_size=32kB --l1d_assoc=8
    --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8
    --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher
    --l1i_mshrs=16 --l1i_write_buffers=8
    --l2cache --l2_size=256kB --l2_assoc=4
    --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16
    --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256
    --l3_write_buffers=128 --l3_ports=4 --cacheline_size=64
    --mem-type Ramulator2 --ramulator-config "$ramulator_config"
    --mem-channels=2 --maa_ncbus_width=32
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
    --cmd "$guest" --options "$options"
)
printf '%q ' "${restore_command[@]}" >"$out/run/command"
printf '\n' >>"$out/run/command"
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${restore_command[@]}" \
    >"$out/run/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" >"$out/run/restore.exit"
[[ $restore_rc -eq 0 ]]

hash_tree "$out/checkpoint" \
    >"$out/provenance/checkpoint.after.files.sha256"
hash_value "$out/provenance/checkpoint.after.files.sha256" \
    >"$out/provenance/checkpoint.after.identity.sha256"
sha256sum "$gem5" "$frozen_ramulator" "$guest" "$graph" "$source_file" \
    "$config" "$ramulator_config" "$0" >"$out/provenance/artifacts.after.sha256"
cmp -s "$out/provenance/artifacts.before.sha256" \
    "$out/provenance/artifacts.after.sha256"

record_aperture_stats "$out"
validate_evidence "$out" false
write_result "$out"
printf 'PASS\n' >"$out/gate.complete"
cat "$out/result.txt"
echo "SSSP_OLD_RESULT_HYBRID_FULL_PASS out=$out"
