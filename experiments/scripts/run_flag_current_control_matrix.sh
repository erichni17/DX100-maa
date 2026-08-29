#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 7 ]]; then
    echo "usage: $0 GEM5 GUEST CASES OUT MAX_PARALLEL SOURCE_COMMIT BINARY_SHA256" >&2
    exit 2
fi

gem5=$(realpath "$1")
guest=$(realpath "$2")
cases=$(realpath "$3")
out=$(realpath -m "$4")
parallel=$5
source_commit=$6
binary_sha=$7
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
runner=$root/experiments/scripts/run_xrage_direct_index_smoke.sh
provenance=/tmp/gem5-complete-tail.provenance

[[ $parallel =~ ^[1-9][0-9]*$ ]] || {
    echo "MAX_PARALLEL must be positive" >&2
    exit 2
}
[[ $source_commit =~ ^[0-9a-f]{40}$ ]] || {
    echo "SOURCE_COMMIT must be a full Git commit" >&2
    exit 2
}
[[ $binary_sha =~ ^[0-9a-f]{64}$ ]] || {
    echo "BINARY_SHA256 must be a SHA-256 digest" >&2
    exit 2
}
[[ $(sha256sum "$gem5" | awk '{print $1}') == "$binary_sha" ]] || {
    echo "gem5 binary hash mismatch" >&2
    exit 2
}
[[ -f $provenance ]] || {
    echo "missing simulator provenance: $provenance" >&2
    exit 2
}
[[ ! -e $out ]] || {
    echo "output already exists: $out" >&2
    exit 2
}

mkdir -p "$out/cases" "$out/rows"
cp "$cases" "$out/cases.list"
{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'runner_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'binary_sha256=%s\n' "$binary_sha"
    printf 'arms=fused16,compact16,direct4_small,direct4_max\n'
    printf 'max_parallel=%s\n' "$parallel"
    printf 'timeout=none\n'
} > "$out/manifest.txt"

tasks=$out/tasks.tsv
: > "$tasks"
while IFS=$'\t' read -r id input length; do
    [[ -n $id && -f $input && $length =~ ^[1-9][0-9]*$ ]] || {
        echo "invalid case: $id $input $length" >&2
        exit 2
    }
    for arm in fused16 compact16 direct4_small direct4_max; do
        printf '%s\t%s\t%s\t%s\n' "$id" "$input" "$length" "$arm" >> "$tasks"
    done
done < "$cases"
[[ $(wc -l < "$tasks") -eq 56 ]] || {
    echo "expected 56 matrix points" >&2
    exit 2
}

run_one() {
    local id=$1 input=$2 length=$3 arm=$4
    local run_out=$out/cases/$id/$arm
    local physical xr_arm guest_arm slots words ways response_words
    case "$arm" in
        fused16)
            physical=16384; xr_arm=fused; guest_arm=fused16
            slots=384; words=4096; ways=4; response_words=480
            ;;
        compact16)
            physical=16384; xr_arm=compact; guest_arm=compact16
            slots=384; words=4096; ways=4; response_words=480
            ;;
        direct4_small)
            physical=4096; xr_arm=direct_index_4k; guest_arm=direct4
            slots=16; words=128; ways=4; response_words=1024
            ;;
        direct4_max)
            physical=4096; xr_arm=direct_index_4k; guest_arm=direct4
            slots=2048; words=3072; ways=16; response_words=1024
            ;;
        *)
            echo "unsupported arm: $arm" >&2
            return 2
            ;;
    esac

    env \
        DX100_ROOT_OVERRIDE="$root" \
        MAA_PHYSICAL_TILE_ELEMENTS="$physical" \
        MAA_GUEST_ABI_TILE_ELEMENTS=16384 \
        MAA_VIRTUAL_COMBINE_SLOTS="$slots" \
        MAA_VIRTUAL_COMBINE_WORDS="$words" \
        MAA_VIRTUAL_COMBINE_WAYS="$ways" \
        MAA_VIRTUAL_RESPONSE_SLOTS=128 \
        MAA_VIRTUAL_RESPONSE_WORD_POOL="$response_words" \
        MAA_VIRTUAL_INDEX_BUFFER_LINES=128 \
        MAA_NUM_INITIAL_ROW_TABLE_SLICES=32 \
        MAA_ROW_TABLE_ROWS_PER_SLICE=64 \
        MAA_NUM_INDIRECT_UNITS_PER_MAA=1 \
        MAA_VIRTUAL_COMPLETE_LINE_ONLY=0 \
        XRAGE_ARM="$xr_arm" \
        XRAGE_GUEST_ARM="$guest_arm" \
        XRAGE_RESULT_SCALE=1 \
        XRAGE_SIMULATOR_SOURCE_COMMIT="$source_commit" \
        XRAGE_SIMULATOR_PROVENANCE="$provenance" \
        "$runner" "$gem5" "$guest" "$input" "$run_out"

    [[ $(<"$run_out/checkpoint.exit") == 0 &&
       $(<"$run_out/restore.exit") == 0 &&
       -f $run_out/xrage_attribution_smoke.pass ]] || {
        echo "nonterminal run: $id/$arm" >&2
        return 1
    }
    local hash ticks writes completions full partial
    read -r hash ticks writes completions < <(
        awk -F '\t' 'NR == 2 {print $1, $2, $5, $6}' "$run_out/result.tsv"
    )
    full=$(awk '$1 == "system.maa.I0_IND_VirtFullLineWrites" {print $2; exit}' \
        "$run_out/run/stats.txt")
    partial=$(awk '$1 == "system.maa.I0_IND_VirtPartialWrites" {print $2; exit}' \
        "$run_out/run/stats.txt")
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$id" "$arm" "$length" "$ticks" "$writes" "$completions" \
        "${full:-0}" "${partial:-0}" "$hash" > "$out/rows/$id.$arm.tsv"
}
export -f run_one
export out root runner provenance gem5 guest source_commit

xargs -P "$parallel" -n 4 bash -c 'run_one "$@"' _ < "$tasks"

{
    printf 'id\tarm\tlength\tticks\twrites\tcompletions\tfull\tpartial\thash\n'
    cat "$out"/rows/*.tsv | sort
} > "$out/results.tsv"
[[ $(wc -l < "$out/results.tsv") -eq 57 ]] || {
    echo "incomplete matrix" >&2
    exit 1
}
printf 'PASS_FLAG_CURRENT_CONTROL_MATRIX\n' > "$out/matrix.pass"
cat "$out/results.tsv"
