#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cxx=${CXX:-g++}
source_test="$repo/tests/maa/inline_operand_retirement_test.cc"
common=(-std=c++17 -Wall -Wextra -Werror -I"$repo/include" -I"$repo/src")

"$cxx" "${common[@]}" -O2 "$source_test" -o "$work/optimized"
"$work/optimized"
"$cxx" "${common[@]}" -O1 -g -fno-omit-frame-pointer \
    -fsanitize=address,undefined "$source_test" -o "$work/sanitize"
ASAN_OPTIONS=detect_leaks=0 "$work/sanitize"
echo INLINE_OPERAND_RETIREMENT_UNIT_PASS
