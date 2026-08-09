#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT

${CXX:-g++} -std=c++17 -O1 -g -Wall -Wextra -Werror \
    -fsanitize=address,undefined -fno-omit-frame-pointer \
    -I"$root/src" -I"$root" \
    "$root/tests/virtual_tile/online_row_window_test.cc" \
    -o "$build/online_row_window_test"
ASAN_OPTIONS=detect_leaks=0 "$build/online_row_window_test"
