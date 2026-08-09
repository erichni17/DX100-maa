#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
build_dir=$(mktemp -d /tmp/dx100-descriptor-spool.XXXXXX)
trap 'rm -rf -- "$build_dir"' EXIT

"${CXX:-g++}" -I"$root/src" -std=c++17 -O1 -g \
    -Wall -Wextra -Werror -fno-omit-frame-pointer \
    -fsanitize=address,undefined \
    "$root/tests/virtual_tile/bounded_descriptor_spool_test.cc" \
    -o "$build_dir/bounded_descriptor_spool_test"
ASAN_OPTIONS=detect_leaks=0 \
    "$build_dir/bounded_descriptor_spool_test"
