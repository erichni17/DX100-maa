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
runner="$root/experiments/scripts/run_virtual_tile_consumer_case.sh"
summary="$root/experiments/scripts/summarize_virtual_row_grow_matrix.py"

[[ -x $gem5 && -x $binary ]] || {
    echo "missing gem5 or test binary" >&2
    exit 2
}
[[ ! -e $out ]] || {
    echo "refusing to overwrite existing output path: $out" >&2
    exit 2
}
mkdir -p "$out"

git -C "$root" status --short > "$out/source_status.txt"
git -C "$root" diff --binary > "$out/source.diff"
sha256sum "$gem5" "$binary" "$0" "$runner" "$summary" \
    "$out/source_status.txt" "$out/source.diff" \
    > "$out/matrix_artifact_sha256.txt"

points=(
    "full_legacy:64:0"
    "full_grow:64:1"
    "half_legacy:32:0"
    "half_grow:32:1"
)
for point in "${points[@]}"; do
    IFS=: read -r label rows grow <<< "$point"
    MAA_ROW_TABLE_SLICES=16 \
    MAA_ROW_TABLE_ROWS_PER_SLICE="$rows" \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_VIRTUAL_GROW_ORDER="$grow" \
    MAA_VIRTUAL_RESPONSE_SLOTS=128 \
    MAA_VIRTUAL_RESPONSE_WORD_POOL=480 \
    MAA_VIRTUAL_COMBINE_SLOTS=384 \
    MAA_VIRTUAL_COMBINE_WORDS=4096 \
    MAA_VIRTUAL_COMBINE_WAYS=4 \
    MAA_VIRTUAL_COMBINE_VICTIM_POLICY=0 \
    MAA_VIRTUAL_COMBINE_BANKS=0 \
        "$runner" "$gem5" "$binary" paged_overlap_4k "$out/$label"
done

python3 "$summary" "$out"
