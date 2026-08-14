#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
cxx=${CXX:-g++}
source_test="$root/tests/maa/virtual_combiner_page_order_test.cc"
common=(-I"$root/src" -std=c++17 -Wall -Wextra -Werror -pedantic)
"$cxx" "${common[@]}" -O2 "$source_test" -o "$work/page_order_opt"
"$work/page_order_opt"
"$cxx" "${common[@]}" -O1 -g -fno-omit-frame-pointer \
    -fsanitize=address,undefined "$source_test" -o "$work/page_order_asan"
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 \
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 "$work/page_order_asan"
