#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cxx=${CXX:-g++}
source="$root/tests/maa/soa_jit_scalar_broadcast_test.cc"
for mode in optimized sanitize; do
    flags=(-I"$root/src" -std=c++17 -Wall -Wextra -Werror)
    if [[ $mode == optimized ]]; then
        flags+=(-O2)
    else
        flags+=(-O1 -g -fsanitize=address,undefined)
    fi
    "$cxx" "${flags[@]}" "$source" -o "$work/$mode"
    if [[ $mode == sanitize ]]; then
        ASAN_OPTIONS=detect_leaks=0 "$work/$mode"
    else
        "$work/$mode"
    fi
done
PYTHONPATH="$root" python3 -c '
from experiments.tests import test_soa_jit_scalar_broadcast_contract as t
for name in sorted(item for item in dir(t) if item.startswith("test_")):
    getattr(t, name)()
'
echo "soa_jit_scalar_broadcast_test: PASS"
