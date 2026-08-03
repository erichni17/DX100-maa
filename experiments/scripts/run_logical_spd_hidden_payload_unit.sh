#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

cxx=${CXX:-g++}
transport_source="$root/src/mem/MAA/LogicalSPDCacheTransport.cc"
common=(-I"$root" -I"$root/src" -std=c++17 -Wall -Wextra -Werror -pedantic)

run_cpp_gate() {
    local name=$1
    local source=$2

    "$cxx" "${common[@]}" -O2 "$source" "$transport_source" \
        -o "$work/${name}_opt"
    "$work/${name}_opt"

    "$cxx" "${common[@]}" -O1 -g -fno-omit-frame-pointer \
        -fsanitize=address,undefined "$source" "$transport_source" \
        -o "$work/${name}_sanitize"
    ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 \
    UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
        "$work/${name}_sanitize"
}

run_cpp_gate logical_spd_hidden_payload_test \
    "$root/tests/maa/logical_spd_hidden_payload_test.cc"
run_cpp_gate logical_spd_cache_transport_test \
    "$root/tests/maa/logical_spd_cache_transport_test.cc"
run_cpp_gate logical_spd_cache_vertical_slice_test \
    "$root/tests/maa/logical_spd_cache_vertical_slice_test.cc"

python3 "$root/experiments/tests/test_logical_spd_hidden_payload_contract.py"
python3 "$root/experiments/tests/test_spd_hardware_accounting.py"
