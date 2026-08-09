#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
build_dir=$(mktemp -d -t fused-direct-unit.XXXXXX)
trap 'rm -rf -- "$build_dir"' EXIT

cxx=${CXX:-g++}
"$cxx" -std=c++17 -O2 -g3 -Wall -Wextra -Werror \
    -fsanitize=address,undefined -fno-omit-frame-pointer \
    -I"$root/src" \
    "$root/tests/maa/multi_range_access_tracker_test.cc" \
    -o "$build_dir/multi_range_access_tracker_test"

ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 \
    "$build_dir/multi_range_access_tracker_test"

echo "FUSED_DIRECT_TRANSFORM_UNIT_PASS"
