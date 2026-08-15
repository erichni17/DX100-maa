#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

cxx=${CXX:-g++}
common=(-std=c++17 -Wall -Wextra -Werror -I"$root/src")

"$cxx" "${common[@]}" -O2 \
    "$root/tests/maa/shared_index_dual_rmw_test.cc" \
    -o "$work/shared_index_dual_rmw_opt"
"$work/shared_index_dual_rmw_opt"

"$cxx" "${common[@]}" -O1 -g \
    -fsanitize=address,undefined -fno-omit-frame-pointer \
    "$root/tests/maa/shared_index_dual_rmw_test.cc" \
    -o "$work/shared_index_dual_rmw_sanitize"
ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 \
    "$work/shared_index_dual_rmw_sanitize"
