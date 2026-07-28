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
policy_list=${MAA_VIRTUAL_COMBINE_VICTIM_POLICY_LIST:-"0 1 2"}

if [[ -e $out ]]; then
    echo "refusing to overwrite existing output path: $out" >&2
    exit 2
fi
mkdir -p "$out"

for policy in $policy_list; do
    [[ $policy =~ ^[012]$ ]] || {
        echo "invalid combiner victim policy: $policy" >&2
        exit 2
    }
    MAA_VIRTUAL_RESPONSE_SLOTS=${MAA_VIRTUAL_RESPONSE_SLOTS:-128} \
    MAA_VIRTUAL_RESPONSE_WORD_POOL=${MAA_VIRTUAL_RESPONSE_WORD_POOL:-480} \
    MAA_VIRTUAL_COMBINE_SLOTS=${MAA_VIRTUAL_COMBINE_SLOTS:-384} \
    MAA_VIRTUAL_COMBINE_WORDS=${MAA_VIRTUAL_COMBINE_WORDS:-4096} \
    MAA_VIRTUAL_COMBINE_WAYS=${MAA_VIRTUAL_COMBINE_WAYS:-4} \
    MAA_VIRTUAL_COMBINE_VICTIM_POLICY=$policy \
    MAA_VIRTUAL_COMBINE_BANKS=${MAA_VIRTUAL_COMBINE_BANKS:-0} \
        "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" \
        "$gem5" "$binary" paged_overlap_4k "$out/policy_$policy"
done
