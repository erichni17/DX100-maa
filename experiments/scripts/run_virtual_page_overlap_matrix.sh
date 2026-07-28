#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 GEM5_BIN OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
out=$(realpath -m "$2")

if [[ -e $out ]]; then
    echo "refusing to overwrite existing output path: $out" >&2
    exit 2
fi
mkdir -p "$out"
"$root/experiments/scripts/build_virtual_tile_consumer.sh" "$out/binaries"
binary="$out/binaries/test_virtual_tile_consumer_T16384"

for case_name in native_16k paged_4k paged_overlap_4k; do
    "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" \
        "$gem5" "$binary" "$case_name" "$out/$case_name"
done

python3 "$root/experiments/scripts/summarize_virtual_page_overlap.py" "$out"
