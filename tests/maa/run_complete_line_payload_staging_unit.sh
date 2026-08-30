#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
common=(-I"$root/src" -std=c++17 -Wall -Wextra -Werror -pedantic)
src="$root/tests/maa/complete_line_payload_staging_test.cc"
${CXX:-g++} "${common[@]}" -O2 "$src" -o "$work/opt"
"$work/opt"
${CXX:-g++} "${common[@]}" -O1 -g -fno-omit-frame-pointer \
    -fsanitize=address,undefined "$src" -o "$work/sanitize"
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 \
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 "$work/sanitize"
