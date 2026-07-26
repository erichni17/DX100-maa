#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 GEM5_BIN OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
out=$(realpath -m "$2")
validator="$root/experiments/scripts/validate_virtual_gather.sh"
binary="$root/benchmarks/API/test_virtual_gather_T16K.o"

if [[ -e $out ]]; then
    echo "refusing to overwrite existing output path: $out" >&2
    exit 2
fi
mkdir -p "$out"

{
    printf 'simulator_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'logical_tile_elements=16384\n'
    printf 'physical_tile_elements=4096\n'
} > "$out/manifest.txt"
sha256sum "$gem5" "$binary" "$validator" "$0" \
    > "$out/artifact_sha256.txt"

run_success() {
    local name=$1 n=$2 physical=$3
    MAA_PHYSICAL_TILE_ELEMENTS=$physical GEM5_BIN="$gem5" \
        "$validator" "$n" random "$out/$name" 0 "$binary" \
        384 96 64 4096 1 0 480 4 4 4 \
        > "$out/$name.controller.log" 2>&1
}

run_failure() {
    local name=$1 n=$2 physical=$3 regex=$4
    MAA_PHYSICAL_TILE_ELEMENTS=$physical GEM5_BIN="$gem5" \
        EXPECT_FAILURE=1 EXPECTED_FAILURE_REGEX="$regex" \
        "$validator" "$n" random "$out/$name" 0 "$binary" \
        384 96 64 4096 1 0 480 4 4 4 \
        > "$out/$name.controller.log" 2>&1
}

# Default physical capacity must preserve the pre-controller behavior.
run_success default_16k 4096 0

# A 4K payload is valid when logical addressing remains 16K.
run_success physical_4k_boundary 4096 4096

# The next element must fail rather than consume an unmodeled fifth Ki-element.
run_failure physical_4k_overflow 4097 4096 \
    'SPD element 4096 exceeds physical tile capacity 4096'

touch "$out/capacity_gate.pass"
