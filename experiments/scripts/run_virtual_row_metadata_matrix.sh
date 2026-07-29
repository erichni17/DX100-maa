#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "usage: $0 GEM5_BIN TEST_BIN OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
binary=$(realpath "$2")
out=$(realpath -m "$3")
points=${MAA_ROW_METADATA_POINTS:-"64:8 32:8 64:4 16:8 32:4 64:2 8:8 16:4 32:2 64:1"}

if [[ -e $out ]]; then
    echo "refusing to overwrite existing output path: $out" >&2
    exit 2
fi
mkdir -p "$out"

printf '%s\n' $points > "$out/points.txt"
git -C "$root" status --short > "$out/source_status.txt"
git -C "$root" diff --binary > "$out/source.diff"
sha256sum "$gem5" "$binary" "$(realpath "$0")" \
    "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" \
    "$root/experiments/scripts/summarize_virtual_row_metadata.py" \
    "$out/points.txt" "$out/source.diff" "$out/source_status.txt" \
    > "$out/matrix_artifact_sha256.txt"

for point in $points; do
    [[ $point =~ ^([1-9][0-9]*):([1-9][0-9]*)$ ]] || {
        echo "invalid row-metadata point: $point" >&2
        exit 2
    }
    rows=${BASH_REMATCH[1]}
    entries=${BASH_REMATCH[2]}
    label="r${rows}_e${entries}"
    MAA_ROW_TABLE_SLICES=16 \
    MAA_ROW_TABLE_ROWS_PER_SLICE=$rows \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=$entries \
    MAA_VIRTUAL_RESPONSE_SLOTS=128 \
    MAA_VIRTUAL_RESPONSE_WORD_POOL=480 \
    MAA_VIRTUAL_COMBINE_SLOTS=384 \
    MAA_VIRTUAL_COMBINE_WORDS=4096 \
    MAA_VIRTUAL_COMBINE_WAYS=4 \
    MAA_VIRTUAL_COMBINE_VICTIM_POLICY=0 \
    MAA_VIRTUAL_COMBINE_BANKS=0 \
        "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" \
        "$gem5" "$binary" paged_overlap_4k "$out/$label"
done

python3 "$root/experiments/scripts/summarize_virtual_row_metadata.py" \
    "$out" --tsv "$out/summary.tsv" --markdown "$out/summary.md"
