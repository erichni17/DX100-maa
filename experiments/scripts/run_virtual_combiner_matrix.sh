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
ways_list=${MAA_VIRTUAL_COMBINE_WAYS_LIST:-"4 8 16 32 0"}

if [[ -e $out ]]; then
    echo "refusing to overwrite existing output path: $out" >&2
    exit 2
fi
mkdir -p "$out"

for ways in $ways_list; do
    [[ $ways =~ ^[0-9]+$ ]] || {
        echo "invalid combiner ways: $ways" >&2
        exit 2
    }
    MAA_VIRTUAL_RESPONSE_SLOTS=${MAA_VIRTUAL_RESPONSE_SLOTS:-128} \
    MAA_VIRTUAL_RESPONSE_WORD_POOL=${MAA_VIRTUAL_RESPONSE_WORD_POOL:-480} \
    MAA_VIRTUAL_COMBINE_SLOTS=${MAA_VIRTUAL_COMBINE_SLOTS:-384} \
    MAA_VIRTUAL_COMBINE_WORDS=${MAA_VIRTUAL_COMBINE_WORDS:-4096} \
    MAA_VIRTUAL_COMBINE_WAYS=$ways \
    MAA_VIRTUAL_COMBINE_BANKS=${MAA_VIRTUAL_COMBINE_BANKS:-0} \
        "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" \
        "$gem5" "$binary" paged_overlap_4k "$out/ways_$ways"
done
