#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
binary=$(mktemp /tmp/dx100-bounded-range-pass.XXXXXX)
trap 'rm -f -- "$binary"' EXIT

"${CXX:-g++}" -I"$root/src" -std=c++17 -O2 -Wall -Wextra -Werror \
    "$root/tests/virtual_tile/bounded_range_pass_test.cc" -o "$binary"
"$binary"
