#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 GEM5_BIN OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
out=$(realpath -m "$2")
[[ ! -e $out ]] || {
    echo "refusing to overwrite existing output: $out" >&2
    exit 2
}
mkdir -p "$out/bin"

cxx=${CXX:-g++}
common=(
    -I"$root/benchmarks/API" -I"$root/include" -I"$root/util/m5/src"
    -std=c++11 -O2 -Wall -Wextra -g3 -fopenmp -DGEM5
    -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000
    "$root/util/m5/src/abi/x86/m5op.S"
)
binary="$out/bin/test_fused_direct_transform"
single_core_binary="$out/bin/test_fused_direct_transform_1c"
"$cxx" "${common[@]}" -DNUM_CORES=4 \
    "$root/benchmarks/API/test_fused_direct_transform.cpp" -o "$binary"
"$cxx" "${common[@]}" -DNUM_CORES=1 \
    "$root/benchmarks/API/test_fused_direct_transform.cpp" \
    -o "$single_core_binary"

{
    printf 'simulator_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'compiler=%s\n' "$($cxx --version | head -n 1)"
} > "$out/source.txt"
sha256sum "$gem5" "$binary" "$single_core_binary" \
    "$root/benchmarks/API/test_fused_direct_transform.cpp" \
    "$root/benchmarks/API/MAA_gem5.hpp" \
    "$root/src/mem/MAA/ALU.cc" "$root/src/mem/MAA/ALU.hh" \
    "$root/src/mem/MAA/IndirectAccess.cc" \
    "$root/src/mem/MAA/IndirectAccess.hh" \
    "$root/src/mem/MAA/IF.cc" "$root/src/mem/MAA/IF.hh" \
    "$root/src/mem/MAA/CpuSidePort.cc" \
    "$root/src/mem/MAA/SPD.cc" "$root/src/mem/MAA/SPD.hh" \
    "$root/src/mem/MAA/MAA.cc" "$root/src/mem/MAA/MAA.hh" \
    "$root/src/mem/MAA/MAA.py" \
    "$root/src/mem/MAA/Invalidator.cc" \
    "$root/src/mem/MAA/Invalidator.hh" \
    "$root/src/mem/MAA/MultiRangeAccessTracker.hh" \
    "$root/experiments/scripts/run_fused_direct_transform_correctness.sh" \
    "$root/experiments/scripts/validate_virtual_gather.sh" \
    > "$out/artifact_sha256.txt"

GEM5_BIN="$gem5" \
    "$root/experiments/scripts/validate_virtual_gather.sh" \
    4097 exact "$out/exact" 3600 "$binary" 384 128 64 4096 1 0 480 \
    4 4 0 | tee "$out/exact.controller.log"

EXPECT_FAILURE=1 \
EXPECTED_FAILURE_REGEX='requires separately registered non-aliasing source and destination regions' \
GEM5_BIN="$gem5" \
    "$root/experiments/scripts/validate_virtual_gather.sh" \
    257 alias "$out/alias" 3600 "$binary" 384 128 64 4096 1 0 480 \
    4 4 0 | tee "$out/alias.controller.log"

GEM5_NUM_CPUS=1 GEM5_BIN="$gem5" \
    "$root/experiments/scripts/validate_virtual_gather.sh" \
    16384 drain "$out/live_drain" 3600 "$single_core_binary" \
    384 128 1 4096 1 0 480 \
    4 1 0 | tee "$out/live_drain.controller.log"
grep -Fq 'FUSED_DIRECT_LIVE_DRAIN_RETURNED' \
    "$out/live_drain/restore.log"
grep -Fq 'MAA drain waiting:' "$out/live_drain/restore.log"

# Keep the operation live until the reset pseudo-op reaches gem5.
EXPECT_FAILURE=1 \
EXPECTED_FAILURE_REGEX='stats reset requested during a live fused direct-sink operation' \
GEM5_BIN="$gem5" \
    "$root/experiments/scripts/validate_virtual_gather.sh" \
    16384 reset "$out/live_reset" 3600 "$binary" 384 128 1 4096 1 0 480 \
    4 1 0 | tee "$out/live_reset.controller.log"

MAA_NUM_MAAS=2 \
GEM5_BIN="$gem5" \
    "$root/experiments/scripts/validate_virtual_gather.sh" \
    4097 multimaa "$out/multimaa" 3600 "$binary" 384 128 1 4096 1 0 480 \
    4 1 0 | tee "$out/multimaa.controller.log"
[[ $(grep -Ec '^FUSED_DIRECT_MULTIMAA_PHASE name=(a_overlap|b_overlap|c_overlap|disjoint) errors=0$' \
    "$out/multimaa/restore.log") -eq 4 ]]

grep -Eq '^system\.maa\.I[0-9]+_IND_FusedALUWords[[:space:]]+4097' \
    "$out/exact/stats.txt"
grep -Eq '^system\.maa\.I[0-9]+_IND_FusedALUBatches[[:space:]]+[1-9]' \
    "$out/exact/stats.txt"
grep -Eq '^system\.maa\.I[0-9]+_IND_FusedResultTransferWords[[:space:]]+4097' \
    "$out/exact/stats.txt"
grep -Eq '^system\.maa\.I[0-9]+_IND_FusedResultTransferCycles[[:space:]]+[1-9]' \
    "$out/exact/stats.txt"
grep -Eq '^system\.maa\.I[0-9]+_IND_VirtWriteIssues[[:space:]]+' \
    "$out/exact/stats.txt"
grep -Eq '^system\.maa\.I[0-9]+_IND_VirtWriteCompletions[[:space:]]+' \
    "$out/exact/stats.txt"
grep -Eq '^system\.maa\.fused_direct_global_lease_conflict_deferrals[[:space:]]+[1-9]' \
    "$out/multimaa/stats.txt"
grep -Eq '^system\.maa\.fused_direct_global_lease_high_water[[:space:]]+2' \
    "$out/multimaa/stats.txt"

echo "FUSED_DIRECT_TRANSFORM_CORRECTNESS_PASS"
