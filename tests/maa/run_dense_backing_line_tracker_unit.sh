#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

cxx=${CXX:-g++}
common=(
    -std=c++17 -Wall -Wextra -Werror -I"$root/src"
    "$root/tests/maa/dense_backing_line_tracker_test.cc"
)

"$cxx" -O2 "${common[@]}" -o "$tmp/optimized"
"$tmp/optimized"

"$cxx" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer \
    "${common[@]}" -o "$tmp/sanitized"
ASAN_OPTIONS=detect_leaks=0 "$tmp/sanitized"
