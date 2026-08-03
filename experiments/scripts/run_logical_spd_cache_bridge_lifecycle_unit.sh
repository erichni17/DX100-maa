#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

cxx=${CXX:-g++}
test_source="$root/tests/maa/logical_spd_cache_bridge_lifecycle_test.cc"
bridge_source="$root/src/mem/MAA/LogicalSPDCacheGem5Bridge.cc"
transport_source="$root/src/mem/MAA/LogicalSPDCacheTransport.cc"
common=(-I"$root" -I"$root/src" -std=c++17 -Wall -Wextra -Werror -pedantic)

"$cxx" "${common[@]}" -O2 "$test_source" "$bridge_source" \
    "$transport_source" -o "$work/logical_spd_bridge_lifecycle_opt"
"$work/logical_spd_bridge_lifecycle_opt"

"$cxx" "${common[@]}" -O1 -g -fno-omit-frame-pointer \
    -fsanitize=address,undefined "$test_source" "$bridge_source" \
    "$transport_source" -o "$work/logical_spd_bridge_lifecycle_sanitize"
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 \
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
    "$work/logical_spd_bridge_lifecycle_sanitize"

python3 "$root/experiments/tests/test_logical_spd_cache_bridge_lifecycle_contract.py"
