#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 8 ]]; then
    echo "usage: $0 GEM5 GUEST INPUT OUT MAX_PARALLEL SOURCE_COMMIT BINARY_SHA256 RAMULATOR_LIB" >&2
    exit 2
fi

gem5=$(realpath "$1")
guest=$(realpath "$2")
input=$(realpath "$3")
out=$(realpath -m "$4")
parallel=$5
source_commit=$6
binary_sha=$7
ramulator_lib=$(realpath "$8")
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
runner=$root/experiments/scripts/run_xrage_direct_index_smoke.sh
provenance=/tmp/gem5-complete-tail.provenance

[[ $parallel =~ ^[1-5]$ ]] || {
    echo "MAX_PARALLEL must be in [1,5]" >&2
    exit 2
}
[[ $source_commit =~ ^[0-9a-f]{40}$ &&
   $binary_sha =~ ^[0-9a-f]{64}$ ]] || {
    echo "invalid source commit or binary SHA-256" >&2
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

mkdir -p "$out/arms" "$out/rows" "$out/inputs/lib"
cp "$ramulator_lib" "$out/inputs/lib/libramulator.so"
frozen_lib=$out/inputs/lib/libramulator.so
{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'runner_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'binary_sha256=%s\n' "$binary_sha"
    printf 'ramulator_sha256=%s\n' \
        "$(sha256sum "$frozen_lib" | awk '{print $1}')"
    printf 'input_sha256=%s\n' "$(sha256sum "$input" | awk '{print $1}')"
    printf 'payload_words_per_cycle=0,1,2,4,8\n'
    printf 'geometry=logical16384,physical4096,combiner1536x8/xor7/2560,response1024\n'
    printf 'lookup_latency_cycles=3\n'
    printf 'combiner_banks=4\n'
    printf 'drain_lines_per_cycle=1\n'
    printf 'timeout=none\n'
} > "$out/manifest.txt"

run_width() {
    local width=$1
    local arm_out=$out/arms/width$width
    env \
        DX100_ROOT_OVERRIDE="$root" \
        MAA_PHYSICAL_TILE_ELEMENTS=4096 \
        MAA_GUEST_ABI_TILE_ELEMENTS=16384 \
        MAA_VIRTUAL_COMBINE_SLOTS=1536 \
        MAA_VIRTUAL_COMBINE_WORDS=2560 \
        MAA_VIRTUAL_COMBINE_WAYS=8 \
        MAA_VIRTUAL_COMBINE_SET_XOR_SHIFT=7 \
        MAA_VIRTUAL_COMBINE_BANKS=4 \
        MAA_VIRTUAL_RESPONSE_SLOTS=128 \
        MAA_VIRTUAL_RESPONSE_WORD_POOL=1024 \
        MAA_VIRTUAL_COMBINE_LOOKUP_LATENCY_CYCLES=3 \
        MAA_VIRTUAL_PAGE_ORDERED_COMBINER_DRAIN=1 \
        MAA_VIRTUAL_COMPLETE_LINE_DRAIN_LINES_PER_CYCLE=1 \
        MAA_VIRTUAL_COMPLETE_LINE_PAYLOAD_WORDS_PER_CYCLE="$width" \
        MAA_VIRTUAL_INDEX_BUFFER_LINES=128 \
        MAA_NUM_INITIAL_ROW_TABLE_SLICES=32 \
        MAA_ROW_TABLE_ROWS_PER_SLICE=64 \
        MAA_NUM_INDIRECT_UNITS_PER_MAA=1 \
        MAA_DIRECT_RETIREMENT_LINE_HANDOFF=1 \
        MAA_VIRTUAL_COMPLETE_LINE_ONLY=1 \
        XRAGE_ARM=direct_index_4k \
        XRAGE_GUEST_ARM=direct4x3 \
        XRAGE_RESULT_SCALE=3 \
        XRAGE_EXPECTED_DIRECT_DESCRIPTORS=4 \
        XRAGE_EXPECTED_DIRECT_CONTEXT_HIGH_WATER=4 \
        XRAGE_SIMULATOR_SOURCE_COMMIT="$source_commit" \
        XRAGE_SIMULATOR_PROVENANCE="$provenance" \
        LD_LIBRARY_PATH="$(dirname "$frozen_lib")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        "$runner" "$gem5" "$guest" "$input" "$arm_out"

    [[ $(<"$arm_out/checkpoint.exit") == 0 &&
       $(<"$arm_out/restore.exit") == 0 &&
       -f $arm_out/xrage_attribution_smoke.pass ]] || {
        echo "nonterminal payload width $width" >&2
        return 1
    }

    local hash ticks writes completions full partial recorded starts
    local staging_completions read_cycles blocked_cycles
    read -r hash ticks writes completions full partial recorded starts \
        staging_completions read_cycles blocked_cycles < <(
        awk -F '\t' '
            NR == 1 {
                for (i = 1; i <= NF; i++) col[$i] = i
                next
            }
            NR == 2 {
                print $(col["output_hash"]), $(col["roi_simTicks"]),
                    $(col["virtual_write_issues"]),
                    $(col["virtual_write_completions"]),
                    $(col["virtual_full_line_writes"]),
                    $(col["virtual_partial_writes"]),
                    $(col["complete_line_payload_words_per_cycle"]),
                    $(col["complete_line_payload_starts"]),
                    $(col["complete_line_payload_completions"]),
                    $(col["complete_line_payload_read_cycles"]),
                    $(col["complete_line_payload_blocked_cycles"])
            }
        ' "$arm_out/result.tsv"
    )
    [[ $recorded -eq $width && $writes -eq 8192 &&
       $completions -eq 8192 && $full -eq 8192 && $partial -eq 0 ]] || {
        echo "payload width $width failed exact producer closure" >&2
        return 1
    }
    if [[ $width -eq 0 ]]; then
        [[ $starts -eq 0 && $staging_completions -eq 0 &&
           $read_cycles -eq 0 && $blocked_cycles -eq 0 ]] || {
            echo "disabled payload staging recorded work" >&2
            return 1
        }
    else
        local expected_cycles=$((8192 * ((8 + width - 1) / width)))
        [[ $starts -eq 8192 && $staging_completions -eq 8192 &&
           $read_cycles -eq $expected_cycles ]] || {
            echo "payload width $width failed exact staging closure" >&2
            return 1
        }
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$width" "$ticks" "$hash" "$starts" "$read_cycles" \
        "$blocked_cycles" \
        "$(sha256sum "$arm_out/run/stats.txt" | awk '{print $1}')" \
        > "$out/rows/width$width.tsv"
}
export -f run_width
export out root runner provenance gem5 guest input source_commit frozen_lib

printf '%s\n' 0 1 2 4 8 | \
    xargs -P "$parallel" -n 1 bash -c 'run_width "$1"' _

{
    printf 'width\tticks\thash\tstarts\tread_cycles\tblocked_cycles'
    printf '\tstats_sha256\n'
    sort -n "$out"/rows/*.tsv
} > "$out/results.tsv"
[[ $(wc -l < "$out/results.tsv") -eq 6 ]] || {
    echo "incomplete payload matrix" >&2
    exit 1
}
[[ $(tail -n +2 "$out/results.tsv" | cut -f3 | sort -u | wc -l) -eq 1 ]] || {
    echo "payload sweep output hashes differ" >&2
    exit 1
}
printf 'PASS_XRAGE_COMPLETE_LINE_PAYLOAD_SWEEP\n' > "$out/sweep.pass"
cat "$out/results.tsv"
