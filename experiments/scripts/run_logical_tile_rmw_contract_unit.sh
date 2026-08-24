#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cxx=${CXX:-g++}
source="$root/tests/maa/logical_tile_rmw_contract_test.cc"
for mode in optimized sanitize; do
    flags=(-I"$root/src" -std=c++17 -Wall -Wextra -Werror)
    if [[ $mode == optimized ]]; then flags+=(-O2); else flags+=(-O1 -g -fsanitize=address,undefined); fi
    "$cxx" "${flags[@]}" "$source" -o "$work/$mode"
    if [[ $mode == sanitize ]]; then
        ASAN_OPTIONS=detect_leaks=0 "$work/$mode"
    else
        "$work/$mode"
    fi
done
