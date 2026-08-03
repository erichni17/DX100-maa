#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

cxx=${CXX:-g++}
source_test="$root/tests/maa/logical_spd_hidden_payload_test.cc"
transport_source="$root/src/mem/MAA/LogicalSPDCacheTransport.cc"
common=(-I"$root/src" -std=c++17 -Wall -Wextra -Werror -pedantic)

"$cxx" "${common[@]}" -O2 "$source_test" "$transport_source" \
    -o "$work/logical_spd_hidden_payload_test_opt"
"$work/logical_spd_hidden_payload_test_opt"

"$cxx" "${common[@]}" -O1 -g -fno-omit-frame-pointer \
    -fsanitize=address,undefined "$source_test" "$transport_source" \
    -o "$work/logical_spd_hidden_payload_test_sanitize"
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 \
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
    "$work/logical_spd_hidden_payload_test_sanitize"

python3 "$root/experiments/tests/test_logical_spd_hidden_payload_contract.py"
python3 "$root/experiments/tests/test_spd_hardware_accounting.py"
