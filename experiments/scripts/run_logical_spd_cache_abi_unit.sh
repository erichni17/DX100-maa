#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

"${CXX:-g++}" -I"$root/include" -I"$root/benchmarks/API" -std=c++11 \
    -O2 -Wall -Wextra -Werror -Wno-ignored-qualifiers -pedantic \
    "$root/tests/maa/logical_spd_cache_abi_test.cc" \
    -o "$work/logical_spd_cache_abi_test"
"$work/logical_spd_cache_abi_test"
python3 "$root/experiments/tests/test_logical_spd_cache_abi_contract.py"
python3 "$root/experiments/tests/test_transparent_spd_controller_contract.py"
