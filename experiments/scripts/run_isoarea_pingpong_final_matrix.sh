#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd); out=$1
log="$out/matrix.log"; status="$out/matrix.exit"
trap 's=$?; printf "%s\n" "$s" > "$status"' EXIT
[[ ! -e $status && ! -e $out/matrix.complete && ! -e $log ]] || { echo stale matrix marker >&2; exit 2; }
mkdir -p "$out"; exec > >(tee "$log") 2>&1
bin="$out/inputs/bin/gem5.opt"; lib="$out/inputs/bin/libramulator.so"
work="$out/inputs/workload/test_virtual_tile_consumer_T16384"
for f in "$bin" "$lib" "$work" "$out/inputs/bin/artifact_sha256.txt" "$out/inputs/workload/workload.sha256"; do [[ -f $f ]] || { echo missing "$f" >&2; exit 2; }; done
grep -F "$(sha256sum "$bin")" "$out/inputs/bin/artifact_sha256.txt" >/dev/null
grep -F "$(sha256sum "$lib")" "$out/inputs/bin/artifact_sha256.txt" >/dev/null
test "$(sha256sum "$work" | awk '{print $1}')" = "$(awk '{print $1}' "$out/inputs/workload/workload.sha256")"
LD_LIBRARY_PATH="$out/inputs/bin" ldd "$bin" | grep -F "libramulator.so => $lib" >/dev/null
for arm in isoarea_serial_4k isoarea_serial_2k isoarea_pingpong_2k; do
  [[ ! -e $out/$arm && ! -e $out/$arm.stage ]] || { echo stale arm "$arm" >&2; exit 2; }
  printf 'stage=%s checkpoint-pending\n' "$arm" > "$out/$arm.stage"
  DX100_SHARED_CHECKPOINT_DIR="$out/shared-checkpoint" DX100_SHARED_TREATMENT_FILE="$out/$arm.treatment" LD_LIBRARY_PATH="$out/inputs/bin" "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" "$bin" "$work" "$arm" "$out/$arm"
  [[ $(<"$out/$arm/checkpoint.exit") == 0 && $(<"$out/$arm/restore.exit") == 0 ]] || exit 1
  printf 'stage=%s complete\n' "$arm" > "$out/$arm.stage"
done
touch "$out/matrix.complete"
