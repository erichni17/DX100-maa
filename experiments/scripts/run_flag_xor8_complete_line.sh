#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 9 ]]; then
    echo "usage: $0 GEM5 GUEST CASES OUT MAX_PARALLEL SOURCE_COMMIT BINARY_SHA256 RAMULATOR_LIB RUNNER" >&2
    exit 2
fi

gem5=$(realpath "$1")
guest=$(realpath "$2")
cases=$(realpath "$3")
out=$(realpath -m "$4")
parallel=$5
source_commit=$6
binary_sha=$7
ramulator_lib=$(realpath "$8")
runner_source=$(realpath "$9")
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
provenance=/tmp/gem5-complete-tail.provenance
combine_ways=${FLAG_COMBINE_WAYS:-8}
combine_xor_shift=${FLAG_COMBINE_XOR_SHIFT:-7}
combine_lookup_latency=${FLAG_COMBINE_LOOKUP_LATENCY:-0}
page_ordered_drain=${FLAG_PAGE_ORDERED_DRAIN:-0}

[[ $parallel =~ ^[1-9][0-9]*$ && $source_commit =~ ^[0-9a-f]{40}$ &&
   $binary_sha =~ ^[0-9a-f]{64}$ ]] || {
    echo "invalid parallelism, source commit, or binary hash" >&2
    exit 2
}
[[ $combine_ways =~ ^(4|8|16)$ &&
   $combine_xor_shift =~ ^([0-9]|[1-5][0-9]|6[0-3])$ &&
   $combine_lookup_latency =~ ^[0-8]$ &&
   $page_ordered_drain =~ ^[01]$ ]] || {
    echo "FLAG combiner ways/shift must be 4/8/16 and [0,63]" >&2
    exit 2
}
[[ $(sha256sum "$gem5" | awk '{print $1}') == "$binary_sha" ]] || {
    echo "gem5 binary hash mismatch" >&2
    exit 2
}
[[ -f $provenance && ! -e $out ]] || {
    echo "missing provenance or preexisting output" >&2
    exit 2
}

mkdir -p "$out/cases" "$out/rows" "$out/inputs/lib" "$out/inputs/runner"
cp "$ramulator_lib" "$out/inputs/lib/libramulator.so"
cp "$runner_source" "$out/inputs/runner/run_xrage_direct_index_smoke.sh"
frozen_lib=$out/inputs/lib/libramulator.so
runner=$out/inputs/runner/run_xrage_direct_index_smoke.sh
cp "$cases" "$out/cases.list"
{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'runner_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'binary_sha256=%s\n' "$binary_sha"
    printf 'ramulator_sha256=%s\n' "$(sha256sum "$frozen_lib" | awk '{print $1}')"
    printf 'runner_sha256=%s\n' "$(sha256sum "$runner" | awk '{print $1}')"
    printf 'geometry=logical16384,physical4096,tags2048,ways%s,xor%s,words3072,response1024,drain1,lookup%s,page_ordered%s\n' \
        "$combine_ways" "$combine_xor_shift" "$combine_lookup_latency" \
        "$page_ordered_drain"
    printf 'timeout=none\n'
} > "$out/manifest.txt"

run_one() {
    local id=$1 input=$2 length=$3
    local run_out=$out/cases/$id
    env \
        DX100_ROOT_OVERRIDE="$root" \
        MAA_PHYSICAL_TILE_ELEMENTS=4096 \
        MAA_GUEST_ABI_TILE_ELEMENTS=16384 \
        MAA_VIRTUAL_COMBINE_SLOTS=2048 \
        MAA_VIRTUAL_COMBINE_WORDS=3072 \
        MAA_VIRTUAL_COMBINE_WAYS="$combine_ways" \
        MAA_VIRTUAL_COMBINE_SET_XOR_SHIFT="$combine_xor_shift" \
        MAA_VIRTUAL_COMBINE_LOOKUP_LATENCY_CYCLES="$combine_lookup_latency" \
        MAA_VIRTUAL_PAGE_ORDERED_COMBINER_DRAIN="$page_ordered_drain" \
        MAA_VIRTUAL_RESPONSE_SLOTS=128 \
        MAA_VIRTUAL_RESPONSE_WORD_POOL=1024 \
        MAA_VIRTUAL_INDEX_BUFFER_LINES=128 \
        MAA_NUM_INITIAL_ROW_TABLE_SLICES=32 \
        MAA_ROW_TABLE_ROWS_PER_SLICE=64 \
        MAA_NUM_INDIRECT_UNITS_PER_MAA=1 \
        MAA_VIRTUAL_COMPLETE_LINE_ONLY=1 \
        MAA_VIRTUAL_COMPLETE_LINE_DRAIN_LINES_PER_CYCLE=1 \
        XRAGE_ARM=direct_index_4k \
        XRAGE_GUEST_ARM=direct4 \
        XRAGE_RESULT_SCALE=1 \
        XRAGE_SIMULATOR_SOURCE_COMMIT="$source_commit" \
        XRAGE_SIMULATOR_PROVENANCE="$provenance" \
        LD_LIBRARY_PATH="$(dirname "$frozen_lib")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        "$runner" "$gem5" "$guest" "$input" "$run_out"

    [[ $(<"$run_out/checkpoint.exit") == 0 &&
       $(<"$run_out/restore.exit") == 0 &&
       -f $run_out/xrage_attribution_smoke.pass ]] || {
        echo "nonterminal FLAG run: $id" >&2
        return 1
    }
    local hash ticks writes completions full partial width issued stalls peak
    local lookup_issues lookup_completions lookup_wait lookup_peak
    local page_selections page_deferrals
    read -r hash ticks writes completions full partial width issued stalls peak \
        lookup_issues lookup_completions lookup_wait lookup_peak \
        page_selections page_deferrals < <(
        awk -F '\t' 'NR == 2 {print $1, $2, $5, $6, $7, $8, $9, $10, $11, $12, $61, $62, $63, $64, $65, $66}' \
            "$run_out/result.tsv"
    )
    local expected_full=$((length / 8))
    local expected_partial=$((length % 8 == 0 ? 0 : 1))
    [[ $width -eq 1 && $writes -eq $completions &&
       $full -eq $expected_full && $partial -eq $expected_partial &&
       $writes -eq $((full + partial)) && $issued -eq $full ]] || {
        echo "FLAG line/tail closure failed: $id" >&2
        return 1
    }
    if [[ $combine_lookup_latency -eq 0 ]]; then
        [[ $lookup_issues -eq 0 && $lookup_completions -eq 0 &&
           $lookup_wait -eq 0 && $lookup_peak -eq 0 ]] || return 1
    else
        [[ $lookup_issues -eq $length && $lookup_completions -eq $length &&
           $lookup_peak -gt 0 && $lookup_peak -le 1024 ]] || return 1
    fi
    if [[ $page_ordered_drain -eq 0 ]]; then
        [[ $page_selections -eq 0 && $page_deferrals -eq 0 ]] || return 1
    else
        [[ $page_selections -eq $full ]] || return 1
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$id" "$length" "$ticks" "$writes" "$full" "$partial" \
        "$stalls" "$peak" "$lookup_wait" "$lookup_peak" \
        "$page_selections" "$page_deferrals" "$hash" \
        > "$out/rows/$id.tsv"
}
export -f run_one
export out root runner provenance gem5 guest source_commit frozen_lib \
    combine_ways combine_xor_shift combine_lookup_latency page_ordered_drain

xargs -r -P "$parallel" -n 3 bash -c \
    'run_one "$1" "$2" "$3"' _ < "$cases"

{
    printf 'id\tlength\tticks\twrites\tfull\tpartial\tstall_cycles\tpeak_sum\tlookup_wait_cycles\tlookup_peak_sum\tpage_selections\tpage_deferrals\thash\n'
    cat "$out"/rows/*.tsv | sort
} > "$out/results.tsv"
[[ $(wc -l < "$out/results.tsv") -eq 15 ]] || {
    echo "incomplete FLAG XOR8 matrix" >&2
    exit 1
}
printf 'PASS_FLAG_XOR8_COMPLETE_LINE\n' > "$out/campaign.pass"
cat "$out/results.tsv"
