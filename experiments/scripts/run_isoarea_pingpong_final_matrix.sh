#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
out=$1
bin="$out/inputs/bin/gem5.opt"; lib="$out/inputs/bin"
testbin="$out/inputs/workload/test_virtual_tile_consumer_T16384"
expected=$(awk '{print $1}' "$out/inputs/workload/workload.sha256")
test "$(sha256sum "$testbin" | awk '{print $1}')" = "$expected"
exec > >(tee -a "$out/matrix.log") 2>&1
trap 's=$?; printf "%s\n" "$s" > "$out/matrix.exit"' EXIT
for arm in isoarea_serial_4k isoarea_serial_2k isoarea_pingpong_2k; do
  printf 'stage=%s checkpoint-pending\n' "$arm" | tee "$out/$arm.stage"
  LD_LIBRARY_PATH="$lib" "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" "$bin" "$testbin" "$arm" "$out/$arm"
  test -f "$out/$arm/checkpoint.exit" && test -f "$out/$arm/restore.exit"
  printf 'stage=%s complete\n' "$arm" | tee "$out/$arm.stage"
done
touch "$out/matrix.complete"
