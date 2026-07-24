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
max_parallel=${MAX_PARALLEL:-4}
[[ $max_parallel =~ ^[1-9][0-9]*$ ]] || {
    echo "MAX_PARALLEL must be a positive integer" >&2
    exit 2
}

rm -rf "$out"
mkdir -p "$out/bin" "$out/cases" "$out/controllers"

cxx=${CXX:-g++}
common=(
    -I"$root/benchmarks/API" -I"$root/include" -I"$root/util/m5/src"
    -std=c++11 -O3 -Wall -g3 -fopenmp -DGEM5 -DTILE_SIZE=16384
    -DNUM_CORES=4 -DMAA_MEM_SIZE=0x80000000
    "$root/util/m5/build/x86/abi/x86/m5op.S"
)
"$cxx" "${common[@]}" "$root/benchmarks/API/test_virtual_gather.cpp" \
    -o "$out/bin/test_virtual_gather32"
"$cxx" "${common[@]}" "$root/benchmarks/API/test_virtual_gather64.cpp" \
    -o "$out/bin/test_virtual_gather64"

{
    printf 'simulator_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'compiler=%s\n' "$($cxx --version | head -n 1)"
    printf 'max_parallel=%s\n' "$max_parallel"
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
    "$root/benchmarks/API/test_virtual_gather.cpp" \
    "$root/benchmarks/API/test_virtual_gather64.cpp" \
    "$root/benchmarks/API/MAA_gem5.hpp" \
    "$root/experiments/scripts/validate_virtual_gather.sh" \
    "$script" "$out"/bin/* \
    > "$out/artifact_sha256.txt"
git -C "$root" diff HEAD -- \
    src/mem/MAA/CpuSidePort.cc src/mem/MAA/MAA.py \
    src/mem/MAA/MAA.hh src/mem/MAA/MAA.cc src/mem/MAA/Port.cc \
    src/mem/MAA/CacheSidePort.cc src/mem/MAA/IndirectAccess.cc \
    src/mem/MAA/IndirectAccess.hh src/mem/MAA/StreamAccess.cc \
    configs/common/Options.py \
    configs/common/MAAConfig.py \
    benchmarks/API/test_virtual_gather.cpp \
    benchmarks/API/test_virtual_gather64.cpp \
    experiments/scripts/validate_virtual_gather.sh \
    experiments/scripts/run_virtual_gather_correctness_matrix.sh \
    > "$out/source.diff"

cases=(
    '32_native_unmasked|32|native|0|0'
    '32_random_unmasked|32|random|0|0'
    '32_random_masked|32|random|1|0'
    '32_dirty_masked|32|dirty|1|0'
    '32_condition_unmasked|32|condition|0|0'
    '32_condition_masked|32|condition|1|0'
    '32_allfalse_unmasked|32|allfalse|0|0'
    '32_allfalse_masked|32|allfalse|1|0'
    '32_alltrue_unmasked|32|alltrue|0|0'
    '32_alltrue_masked|32|alltrue|1|0'
    '32_boundary_unmasked|32|boundary|0|0'
    '32_boundary_masked|32|boundary|1|0'
    '32_page_unmasked|32|page|0|0'
    '32_page_masked|32|page|1|0'
    '32_line_masked|32|line|1|1'
    '32_short_unmasked|32|short|0|1'
    '32_short_masked|32|short|1|1'
    '32_unregistered_unmasked|32|unregistered|0|1'
    '64_native_unmasked|64|native|0|0'
    '64_random_unmasked|64|random|0|0'
    '64_random_masked|64|random|1|0'
    '64_dirty_masked|64|dirty|1|0'
    '64_condition_unmasked|64|condition|0|0'
    '64_condition_masked|64|condition|1|0'
    '64_allfalse_unmasked|64|allfalse|0|0'
    '64_allfalse_masked|64|allfalse|1|0'
    '64_alltrue_unmasked|64|alltrue|0|0'
    '64_alltrue_masked|64|alltrue|1|0'
    '64_boundary_unmasked|64|boundary|0|0'
    '64_boundary_masked|64|boundary|1|0'
    '64_page_unmasked|64|page|0|0'
    '64_page_masked|64|page|1|0'
    '64_line_masked|64|line|1|1'
    '64_short_unmasked|64|short|0|1'
    '64_short_masked|64|short|1|1'
    '64_unregistered_unmasked|64|unregistered|0|1'
)

run_case() {
    local name=$1 width=$2 pattern=$3 masked=$4 expected_failure=$5
    local binary="$out/bin/test_virtual_gather$width"
    local case_out="$out/cases/$name"
    local controller="$out/controllers/$name.log"
    local expected_failure_regex='virtual (backing index|retirement write).*exceeds'
    if [[ $pattern == unregistered ]]; then
        expected_failure_regex='Address .* does not belong to any region|Backing address .* is not in a registered memory region'
    fi
    local rc
    set +e
    GEM5_BIN="$gem5" EXPECT_FAILURE="$expected_failure" \
        EXPECTED_FAILURE_REGEX="$expected_failure_regex" \
        "$root/experiments/scripts/validate_virtual_gather.sh" \
        4097 "$pattern" "$case_out" 21600 "$binary" \
        384 96 64 4096 "$masked" 0 480 4 4 4 \
        > "$controller" 2>&1
    rc=$?
    set -e
    printf '%s\n' "$rc" > "$out/controllers/$name.exit"
    if [[ $rc -eq 0 ]]; then
        : > "$out/controllers/$name.pass"
    else
        : > "$out/controllers/$name.fail"
    fi
    return "$rc"
}

status=0
running=0
for spec in "${cases[@]}"; do
    IFS='|' read -r name width pattern masked expected_failure <<< "$spec"
    run_case "$name" "$width" "$pattern" "$masked" "$expected_failure" &
    running=$((running + 1))
    if [[ $running -ge $max_parallel ]]; then
        wait -n || status=1
        running=$((running - 1))
    fi
done
while [[ $running -gt 0 ]]; do
    wait -n || status=1
    running=$((running - 1))
done

extract_first_stats() {
    awk '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ /^system\.maa\.I[0-9]+_IND_VirtWriteIssues$/ {
            i += $2
        }
        section == 1 && $1 ~ /^system\.maa\.I[0-9]+_IND_VirtWriteCompletions$/ {
            c += $2
        }
        /^---------- End Simulation Statistics/ && section == 1 {
            printf "%s\t%s\n", i + 0, c + 0; exit
        }
    ' "$1"
}

printf 'name\twidth\tpattern\tmasked\texpected_failure\tissues\tcompletions\tvalid\n' \
    > "$out/results.tsv"
for spec in "${cases[@]}"; do
    IFS='|' read -r name width pattern masked expected_failure <<< "$spec"
    issues=0
    completions=0
    valid=1
    [[ -f "$out/controllers/$name.pass" ]] || valid=0
    if [[ $expected_failure -eq 0 && $valid -eq 1 ]]; then
        read -r issues completions \
            < <(extract_first_stats "$out/cases/$name/stats.txt")
        [[ -n $issues && $issues -eq $completions ]] || valid=0
        if [[ $pattern == native || $pattern == allfalse ]]; then
            [[ $issues -eq 0 ]] || valid=0
        else
            [[ $issues -gt 0 ]] || valid=0
        fi
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$name" "$width" "$pattern" "$masked" "$expected_failure" \
        "$issues" "$completions" "$valid" >> "$out/results.tsv"
    [[ $valid -eq 1 ]] || status=1
done

if [[ $status -ne 0 ]]; then
    echo "virtual gather correctness matrix failed" >&2
    cat "$out/results.tsv" >&2
    exit 1
fi
: > "$out/matrix.pass"
cat "$out/results.tsv"
