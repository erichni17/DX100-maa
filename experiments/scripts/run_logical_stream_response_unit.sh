#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

"${CXX:-g++}" -I"$root/src" -std=c++17 -O2 -Wall -Wextra -Werror \
    -pedantic "$root/tests/maa/logical_stream_response_test.cc" \
    -o "$work/logical_stream_response_test"
"$work/logical_stream_response_test"
python3 "$root/experiments/tests/test_logical_stream_response_contract.py"
