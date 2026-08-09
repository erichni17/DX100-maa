#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
build_dir=$(mktemp -d /tmp/dx100-bounded-metadata.XXXXXX)
trap 'rm -rf -- "$build_dir"' EXIT

tests=(
    bounded_range_pass_test
    bounded_quantile_ranges_test
    bounded_metadata_ledger_test
)
for test_name in "${tests[@]}"; do
    "${CXX:-g++}" -I"$root/src" -std=c++17 -O1 -g \
        -Wall -Wextra -Werror -fno-omit-frame-pointer \
        -fsanitize=address,undefined \
        "$root/tests/virtual_tile/${test_name}.cc" \
        -o "$build_dir/$test_name"
    ASAN_OPTIONS=detect_leaks=0 "$build_dir/$test_name"
done
