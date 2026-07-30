#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 0 ]]; then
    echo "usage: $0" >&2
    exit 2
fi

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

cxx=${CXX:-g++}
if ! command -v "$cxx" >/dev/null 2>&1; then
    echo "missing C++ compiler: $cxx" >&2
    exit 2
fi
if ! command -v systemd-run >/dev/null 2>&1; then
    echo "systemd-run is required for the 16-GiB execution boundary" >&2
    exit 2
fi

build_dir=$(mktemp -d "${TMPDIR:-/tmp}/lanl-maa-overlay-gate.XXXXXX")
cleanup()
{
    rm -rf -- "$build_dir"
}
trap cleanup EXIT

bounded()
{
    systemd-run --user --scope --quiet --collect \
        --property=MemoryHigh=12G \
        --property=MemoryMax=16G \
        --property=MemorySwapMax=0 \
        --property=CPUQuota=400% \
        -- "$@"
}

common_flags=(
    -std=c++17
    -Wall
    -Wextra
    -Werror
    -I "$repo_root/src"
)
sanitizer_flags=(
    -O1
    -g
    -fno-omit-frame-pointer
    -fsanitize=address,undefined
)
sources=(
    tests/lanl_maa/line_table_geometry_test.cc
    tests/lanl_maa/operation_payload_port_model_test.cc
    tests/lanl_maa/shared_overlay_cost_test.cc
    tests/lanl_maa/shared_overlay_mode_barrier_test.cc
)

for source in "${sources[@]}"; do
    name=$(basename "$source" .cc)
    warning_binary="$build_dir/${name}.warnings"
    sanitizer_binary="$build_dir/${name}.sanitizers"

    bounded "$cxx" "${common_flags[@]}" "$source" -o "$warning_binary"
    bounded "$warning_binary"

    bounded "$cxx" "${common_flags[@]}" "${sanitizer_flags[@]}" \
        "$source" -o "$sanitizer_binary"
    bounded env ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 \
        UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
        "$sanitizer_binary"
done

echo "LANL-MAA shared-shell warning and sanitizer gate: PASS"
