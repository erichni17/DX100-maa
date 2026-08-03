#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

common=(
    -I"$root/src" -std=c++17 -Wall -Wextra -Werror -pedantic
    "$root/tests/maa/logical_spd_cache_vertical_slice_test.cc"
)
"${CXX:-g++}" "${common[@]}" -O2 -o "$work/vertical_slice"
"$work/vertical_slice"

"${CXX:-g++}" "${common[@]}" -O1 -g -fno-omit-frame-pointer \
    -fsanitize=address,undefined -o "$work/vertical_slice_san"
: "${ASAN_OPTIONS:=detect_leaks=1}"
: "${UBSAN_OPTIONS:=halt_on_error=1}"
ASAN_OPTIONS="$ASAN_OPTIONS" UBSAN_OPTIONS="$UBSAN_OPTIONS" \
    "$work/vertical_slice_san"

python3 "$root/experiments/tests/test_logical_spd_cache_vertical_slice_contract.py"
