#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 GEM5_BIN OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
script=$(realpath "$0")
gem5=$(realpath "$1")
out=$(realpath -m "$2")
cxx=${CXX:-g++}

rm -rf "$out"
mkdir -p "$out/bin"

binary="$out/bin/test_virtual_gather_multiunit"
"$cxx" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O3 -Wall -g3 -fopenmp \
    -DGEM5 -DTILE_SIZE=16384 -DNUM_CORES=4 -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/build/x86/abi/x86/m5op.S" \
    "$root/benchmarks/API/test_virtual_gather_multiunit.cpp" \
    -o "$binary"

{
    printf 'simulator_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'compiler=%s\n' "$($cxx --version | head -n 1)"
    printf 'indirect_units=2\n'
    printf 'masked_writes=1\n'
    printf 'same_line_disjoint_masks=1\n'
    printf 'distinct_payloads=1\n'
    printf 'retirement_cache_response_latency=2048\n'
} > "$out/source.txt"
sha256sum "$gem5" \
    "$root/src/mem/MAA/MAA.py" \
    "$root/src/mem/MAA/MAA.hh" \
    "$root/src/mem/MAA/MAA.cc" \
    "$root/src/mem/MAA/Port.cc" \
    "$root/src/mem/MAA/CacheSidePort.cc" \
    "$root/src/mem/MAA/IndirectAccess.cc" \
    "$root/src/mem/MAA/IndirectAccess.hh" \
    "$root/src/mem/MAA/StreamAccess.cc" \
    "$root/configs/common/Options.py" \
    "$root/configs/common/MAAConfig.py" \
    "$root/benchmarks/API/test_virtual_gather_multiunit.cpp" \
    "$root/benchmarks/API/MAA_gem5.hpp" \
    "$root/experiments/scripts/validate_virtual_gather.sh" \
    "$script" "$binary" > "$out/artifact_sha256.txt"
git -C "$root" diff HEAD -- \
    src/mem/MAA/MAA.py src/mem/MAA/MAA.hh src/mem/MAA/MAA.cc \
    src/mem/MAA/Port.cc src/mem/MAA/CacheSidePort.cc \
    src/mem/MAA/IndirectAccess.cc src/mem/MAA/IndirectAccess.hh \
    src/mem/MAA/StreamAccess.cc \
    configs/common/Options.py \
    configs/common/MAAConfig.py \
    benchmarks/API/test_virtual_gather_multiunit.cpp \
    experiments/scripts/validate_virtual_gather.sh \
    experiments/scripts/run_virtual_gather_multiunit_same_line.sh \
    > "$out/source.diff"

GEM5_BIN="$gem5" MAA_NUM_INDIRECT_UNITS_PER_MAA=2 \
    MAA_RETIREMENT_CACHE_RESPONSE_LATENCY=2048 \
    "$root/experiments/scripts/validate_virtual_gather.sh" \
    4096 multiunit_same_line "$out/run" 21600 "$binary" \
    384 96 64 4096 1 0 480 4 4 4

read -r i0_issues i0_completions i0_conflicts \
    i1_issues i1_completions i1_conflicts < <(
    awk '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 == "system.maa.I0_IND_VirtWriteIssues" {
            i0i = $2
        }
        section == 1 && $1 == "system.maa.I0_IND_VirtWriteCompletions" {
            i0c = $2
        }
        section == 1 && $1 == "system.maa.I0_IND_VirtWriteAddressConflicts" {
            i0x = $2
        }
        section == 1 && $1 == "system.maa.I1_IND_VirtWriteIssues" {
            i1i = $2
        }
        section == 1 && $1 == "system.maa.I1_IND_VirtWriteCompletions" {
            i1c = $2
        }
        section == 1 && $1 == "system.maa.I1_IND_VirtWriteAddressConflicts" {
            i1x = $2
        }
        /^---------- End Simulation Statistics/ && section == 1 {
            printf "%d %d %d %d %d %d\n",
                   i0i, i0c, i0x, i1i, i1c, i1x
            exit
        }
    ' "$out/run/stats.txt"
)

if [[ $i0_issues -le 0 || $i1_issues -le 0 ||
      $i0_issues -ne $i0_completions ||
      $i1_issues -ne $i1_completions ||
      $((i0_conflicts + i1_conflicts)) -le 0 ]]; then
    printf 'invalid multiunit counters: I0=%s/%s conflicts=%s I1=%s/%s conflicts=%s\n' \
        "$i0_issues" "$i0_completions" "$i0_conflicts" \
        "$i1_issues" "$i1_completions" "$i1_conflicts" >&2
    exit 1
fi

printf 'MULTIUNIT_SAME_LINE_PASS I0=%s/%s conflicts=%s I1=%s/%s conflicts=%s\n' \
    "$i0_issues" "$i0_completions" "$i0_conflicts" \
    "$i1_issues" "$i1_completions" "$i1_conflicts"
: > "$out/multiunit.pass"
