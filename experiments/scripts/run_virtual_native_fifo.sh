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

binary="$out/bin/test_virtual_native_fifo"
"$cxx" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O3 -Wall -g3 -fopenmp \
    -DGEM5 -DTILE_SIZE=16384 -DNUM_CORES=4 -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/build/x86/abi/x86/m5op.S" \
    "$root/benchmarks/API/test_virtual_native_fifo.cpp" \
    -o "$binary"

{
    printf 'simulator_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'compiler=%s\n' "$($cxx --version | head -n 1)"
    printf 'indirect_units=6\n'
    printf 'masked_writes=1\n'
    printf 'retirement_cache_response_latency=32768\n'
    printf 'minimum_expected_deferrals=5\n'
    printf 'minimum_expected_queue_deferrals=1\n'
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
    "$root/benchmarks/API/test_virtual_native_fifo.cpp" \
    "$root/benchmarks/API/MAA_gem5.hpp" \
    "$root/experiments/scripts/validate_virtual_gather.sh" \
    "$script" "$binary" > "$out/artifact_sha256.txt"
git -C "$root" diff HEAD -- \
    src/mem/MAA/MAA.py src/mem/MAA/MAA.hh src/mem/MAA/MAA.cc \
    src/mem/MAA/Port.cc src/mem/MAA/CacheSidePort.cc \
    src/mem/MAA/IndirectAccess.cc src/mem/MAA/IndirectAccess.hh \
    src/mem/MAA/StreamAccess.cc \
    configs/common/Options.py configs/common/MAAConfig.py \
    benchmarks/API/test_virtual_native_fifo.cpp \
    experiments/scripts/validate_virtual_gather.sh \
    experiments/scripts/run_virtual_native_fifo.sh \
    > "$out/source.diff"

GEM5_BIN="$gem5" MAA_NUM_INDIRECT_UNITS_PER_MAA=6 \
    MAA_RETIREMENT_CACHE_RESPONSE_LATENCY=32768 \
    "$root/experiments/scripts/validate_virtual_gather.sh" \
    4096 native_fifo_depth "$out/run" 21600 "$binary" \
    384 96 64 4096 1 0 480 4 4 4

read -r deferrals queue_deferrals issues completions < <(
    awk '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 &&
            $1 == "system.maa.virtual_retirement_native_deferrals" {
            deferrals = $2
        }
        section == 1 &&
            $1 == "system.maa.virtual_retirement_queue_deferrals" {
            queue_deferrals = $2
        }
        section == 1 && $1 ~ /^system\.maa\.I[0-5]_IND_VirtWriteIssues$/ {
            issues += $2
        }
        section == 1 &&
            $1 ~ /^system\.maa\.I[0-5]_IND_VirtWriteCompletions$/ {
            completions += $2
        }
        /^---------- End Simulation Statistics/ && section == 1 {
            printf "%d %d %d %d\n", deferrals, queue_deferrals,
                   issues, completions
            exit
        }
    ' "$out/run/stats.txt"
)

if [[ $deferrals -lt 5 || $queue_deferrals -lt 1 || $issues -le 0 ||
      $issues -ne $completions ]]; then
    printf 'invalid FIFO counters: deferrals=%s queue=%s virtual=%s/%s\n' \
        "$deferrals" "$queue_deferrals" "$issues" "$completions" >&2
    exit 1
fi

printf 'VIRTUAL_NATIVE_FIFO_PASS deferrals=%s queue=%s virtual=%s/%s\n' \
    "$deferrals" "$queue_deferrals" "$issues" "$completions"
: > "$out/native_fifo.pass"
