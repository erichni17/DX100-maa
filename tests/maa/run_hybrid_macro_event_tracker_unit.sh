#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

cxx=${CXX:-g++}
source_test="$root/tests/maa/hybrid_macro_event_tracker_test.cc"
common=(-I"$root/src" -std=c++17 -Wall -Wextra -Werror -pedantic)

"$cxx" "${common[@]}" -O2 "$source_test" \
    -o "$work/hybrid_macro_event_tracker_opt"
"$work/hybrid_macro_event_tracker_opt"

"$cxx" "${common[@]}" -O1 -g -fno-omit-frame-pointer \
    -fsanitize=address,undefined "$source_test" \
    -o "$work/hybrid_macro_event_tracker_sanitize"
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 \
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
    "$work/hybrid_macro_event_tracker_sanitize"

python3 "$root/experiments/tests/test_hybrid_macro_profile_contract.py"
