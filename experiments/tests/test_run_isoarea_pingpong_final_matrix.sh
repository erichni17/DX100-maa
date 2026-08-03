#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd); script="$root/experiments/scripts/run_isoarea_pingpong_final_matrix.sh"
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
"$script" "$d" >/dev/null 2>&1 && exit 1 || true
test "$(<"$d/matrix.exit")" != 0
d=$(mktemp -d); mkdir -p "$d/inputs/bin" "$d/inputs/workload"
touch "$d/inputs/bin/gem5.opt" "$d/inputs/bin/libramulator.so" "$d/inputs/workload/test_virtual_tile_consumer_T16384"
printf 'bad  inputs/bin/gem5.opt\n' > "$d/inputs/bin/artifact_sha256.txt"
printf 'bad  inputs/workload/test_virtual_tile_consumer_T16384\n' > "$d/inputs/workload/workload.sha256"
"$script" "$d" >/dev/null 2>&1 && exit 1 || true
test "$(<"$d/matrix.exit")" != 0
d=$(mktemp -d); touch "$d/matrix.complete"
"$script" "$d" >/dev/null 2>&1 && exit 1 || true
test "$(<"$d/matrix.exit")" != 0
