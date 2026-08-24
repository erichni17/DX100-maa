#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
source="$root/benchmarks/API/test_virtual_tile_consumer.cpp"
m5op="$root/util/m5/build/x86/abi/x86/m5op.S"
if [[ ! -e $m5op ]]; then
    m5op="$root/util/m5/src/abi/x86/m5op.S"
fi

if [[ -e $out ]]; then
    echo "refusing to overwrite existing output path: $out" >&2
    exit 2
fi
mkdir -p "$out"

for tile_size in 4096 16384; do
    binary="$out/test_virtual_tile_consumer_T${tile_size}"
    "${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
        -I"$root/util/m5/src" -std=c++11 -O3 -Wall -Wextra -g3 -fopenmp \
        -DGEM5 -DTILE_SIZE="$tile_size" -DNUM_CORES=4 \
        -DMAA_MEM_SIZE=0x80000000 "$m5op" "$source" -o "$binary"
done

"${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O3 -Wall -Wextra -g3 -fopenmp \
    -DGEM5 -DTILE_SIZE=16384 -DNUM_CORES=4 \
    -DVIRTUAL_CONSUMER_FP32_ADD -DMAA_MEM_SIZE=0x80000000 \
    "$m5op" "$source" -o "$out/test_virtual_tile_consumer_T16384_fp32_add"

{
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'logical_elements=4096,16384\n'
    printf 'generic_transparent_variant=fp32_add\n'
} > "$out/manifest.txt"
sha256sum "$source" "$0" \
    "$out/test_virtual_tile_consumer_T4096" \
    "$out/test_virtual_tile_consumer_T16384" \
    "$out/test_virtual_tile_consumer_T16384_fp32_add" \
    > "$out/artifact_sha256.txt"
