#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

"${CXX:-g++}" -I"$root/src" -std=c++17 -O2 -Wall -Wextra -Werror \
    -pedantic \
    "$root/tests/maa/logical_spd_cache_controller_test.cc" \
    -o "$work/logical_spd_cache_controller_test"
"$work/logical_spd_cache_controller_test"
python3 "$root/experiments/tests/test_logical_spd_cache_controller_contract.py"
