#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
build_dir=$(mktemp -d -t xrage-zero-payload-unit.XXXXXX)
trap 'rm -rf -- "$build_dir"' EXIT

cxx=${CXX:-g++}
common=(
    -std=c++17 -O2 -g3 -Wall -Wextra -Werror
    -fsanitize=address,undefined -fno-omit-frame-pointer
    -I"$root/src"
)

"$cxx" "${common[@]}" \
    "$root/tests/maa/xrage_zero_payload_accounting_test.cc" \
    -o "$build_dir/xrage_zero_payload_accounting_test"
"$cxx" "${common[@]}" \
    "$root/tests/maa/multi_range_access_tracker_test.cc" \
    -o "$build_dir/multi_range_access_tracker_test"

ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 \
    "$build_dir/xrage_zero_payload_accounting_test"
ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 \
    "$build_dir/multi_range_access_tracker_test"

echo "XRAGE_ZERO_PAYLOAD_UNIT_PASS"
