#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT
common=(-std=c++17 -Wall -Wextra -Werror -pedantic -I"$root/tests/virtual_tile")
c++ "${common[@]}" -O3 "$root/tests/virtual_tile/logical_page_scheduler_test.cc" -o "$build/optimized"
"$build/optimized"
c++ "${common[@]}" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer "$root/tests/virtual_tile/logical_page_scheduler_test.cc" -o "$build/sanitized"
# LeakSanitizer requires ptrace, which is unavailable in the normal gem5
# sandbox.  ASan's bounds/use-after-free checks and UBSan remain enabled.
ASAN_OPTIONS=detect_leaks=0 "$build/sanitized"
