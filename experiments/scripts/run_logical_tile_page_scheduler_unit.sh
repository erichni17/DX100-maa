#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

cxx=${CXX:-g++}
source_test="$root/tests/maa/logical_tile_page_scheduler_test.cc"
common=(-I"$root/src" -std=c++17 -Wall -Wextra -Werror -pedantic)

for mode in optimized sanitize; do
    flags=("${common[@]}")
    if [[ $mode == optimized ]]; then
        flags+=(-O2)
    else
        flags+=(-O1 -g -fno-omit-frame-pointer)
        flags+=(-fsanitize=address,undefined)
    fi
    "$cxx" "${flags[@]}" "$source_test" -o "$work/$mode"
    if [[ $mode == sanitize ]]; then
        ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 \
        UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
            "$work/$mode"
    else
        "$work/$mode"
    fi
done

python3 "$root/experiments/tests/test_logical_tile_page_scheduler.py"
echo "logical_tile_page_scheduler_test: PASS"
