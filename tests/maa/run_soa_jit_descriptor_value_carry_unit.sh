#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
binary=$(mktemp /tmp/soa-jit-descriptor-value-carry.XXXXXX)
trap 'rm -f "$binary"' EXIT

${CXX:-g++} -std=c++17 -Wall -Wextra -Werror \
    -I"$root/src" -I"$root/build/X86" \
    "$root/tests/maa/soa_jit_descriptor_value_carry_test.cc" \
    -o "$binary"
"$binary"
