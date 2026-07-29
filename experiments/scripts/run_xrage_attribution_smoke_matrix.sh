#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "usage: $0 GEM5_BIN BUILD_DIR INPUT_JSON OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
runner="$root/experiments/scripts/run_xrage_direct_index_smoke.sh"
gem5=$(realpath "$1")
build=$(realpath "$2")
input=$(realpath "$3")
out=$(realpath -m "$4")
validator="$root/experiments/scripts/validate_xrage_attribution_smoke.py"

[[ ! -e $out ]] || {
    echo "refusing to overwrite existing output: $out" >&2
    exit 2
}
[[ -z $(git -C "$root" status --short) ]] || {
    echo "XRAGE attribution requires a clean source worktree" >&2
    exit 2
}
mkdir -p "$out"

run_arm() {
    local arm=$1
    local binary=$2
    local physical=$3
    XRAGE_ARM="$arm" MAA_PHYSICAL_TILE_ELEMENTS="$physical" \
        "$runner" "$gem5" "$build/$binary" "$input" "$out/$arm"
}

run_arm native spatter_maa_verify_16K 16384
run_arm fused spatter_maa_fused_verify_16K 16384
run_arm compact spatter_maa_virtual_verify_16K 16384
run_arm direct_index_16k spatter_maa_virtual_index_verify_16K 16384
run_arm direct_index_4k spatter_maa_virtual_index_verify_16K 4096

python3 "$validator" "$out"
