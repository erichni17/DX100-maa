#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
binary=$(mktemp /tmp/bounded-four-run-merge-test.XXXXXX)
trap 'rm -f -- "$binary"' EXIT

${CXX:-c++} -std=c++17 -Wall -Wextra -Werror -pedantic \
    -I "$root/src" \
    "$root/tests/maa/bounded_four_run_merge_test.cc" \
    -o "$binary"
"$binary"
