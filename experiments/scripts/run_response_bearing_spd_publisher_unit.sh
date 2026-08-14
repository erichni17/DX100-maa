#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
build_dir=$(mktemp -d /tmp/dx100-response-bearing-spd.XXXXXX)
trap 'rm -rf -- "$build_dir"' EXIT

cxx=${CXX:-g++}
source_test="$root/tests/cpp/response_bearing_spd_publisher_test.cc"
common=(-I"$root/src" -std=c++17 -Wall -Wextra -Werror -pedantic)

"$cxx" "${common[@]}" -O2 "$source_test" \
    -o "$build_dir/response_bearing_spd_publisher_opt"
"$build_dir/response_bearing_spd_publisher_opt"

"$cxx" "${common[@]}" -O1 -g -fno-omit-frame-pointer \
    -fsanitize=address,undefined "$source_test" \
    -o "$build_dir/response_bearing_spd_publisher_sanitize"
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 \
UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
    "$build_dir/response_bearing_spd_publisher_sanitize"
