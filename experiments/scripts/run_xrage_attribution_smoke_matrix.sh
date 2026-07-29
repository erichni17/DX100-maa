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
max_parallel=${XRAGE_MAX_PARALLEL:-1}

[[ ! -e $out ]] || {
    echo "refusing to overwrite existing output: $out" >&2
    exit 2
}
[[ -z $(git -C "$root" status --short) ]] || {
    echo "XRAGE attribution requires a clean source worktree" >&2
    exit 2
}
[[ $max_parallel == 1 || $max_parallel == 2 ]] || {
    echo "XRAGE_MAX_PARALLEL must be 1 or 2" >&2
    exit 2
}
mkdir -p "$out"
{
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'max_parallel=%s\n' "$max_parallel"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$out/campaign_manifest.txt"

run_arm() {
    local arm=$1
    local binary=$2
    local physical=$3
    XRAGE_ARM="$arm" MAA_PHYSICAL_TILE_ELEMENTS="$physical" \
        "$runner" "$gem5" "$build/$binary" "$input" "$out/$arm"
}

wait_pair() {
    local first_pid=$1
    local second_pid=$2
    local first_rc second_rc
    set +e
    wait "$first_pid"
    first_rc=$?
    wait "$second_pid"
    second_rc=$?
    set -e
    [[ $first_rc -eq 0 && $second_rc -eq 0 ]]
}

if [[ $max_parallel -eq 1 ]]; then
    run_arm native spatter_maa_verify_16K 16384
    run_arm fused spatter_maa_fused_verify_16K 16384
    run_arm compact spatter_maa_virtual_verify_16K 16384
    run_arm direct_index_16k spatter_maa_virtual_index_verify_16K 16384
    run_arm direct_index_4k spatter_maa_virtual_index_verify_16K 4096
    run_arm fused_4k spatter_maa_fused_verify_4K 4096
else
    run_arm native spatter_maa_verify_16K 16384 &
    native_pid=$!
    run_arm fused spatter_maa_fused_verify_16K 16384 &
    fused_pid=$!
    wait_pair "$native_pid" "$fused_pid"

    run_arm compact spatter_maa_virtual_verify_16K 16384 &
    compact_pid=$!
    run_arm direct_index_16k spatter_maa_virtual_index_verify_16K 16384 &
    direct_16k_pid=$!
    wait_pair "$compact_pid" "$direct_16k_pid"

    run_arm direct_index_4k spatter_maa_virtual_index_verify_16K 4096 &
    direct_4k_pid=$!
    run_arm fused_4k spatter_maa_fused_verify_4K 4096 &
    fused_4k_pid=$!
    wait_pair "$direct_4k_pid" "$fused_4k_pid"
fi

python3 "$validator" "$out"
