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
    -DTILE_SIZE=4096 -DNUM_CORES=4 -DMAA_MEM_SIZE=0x80000000
    "$root/util/m5/src/abi/x86/m5op.S"
)
binary="$out/bin/test_xrage_zero_payload"
"$cxx" "${common[@]}" \
    "$root/benchmarks/API/test_xrage_zero_payload.cpp" -o "$binary"

{
    printf 'simulator_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'simulator_dirty_tree=%s\n' "$(git -C "$root" status --porcelain | wc -l)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'compiler=%s\n' "$($cxx --version | head -n 1)"
    printf 'logical_tile_elements=4096\n'
    printf 'offset_table_entries=4096\n'
    printf 'offset_epoch_entries=4096\n'
    printf 'row_table_slices=16\n'
    printf 'row_table_rows=16\n'
    printf 'index_lines=1\nresponse_slots=1\ncombiner_slots=1\n'
    printf 'combiner_words=1\nwrite_credits=1\nresult_words_per_cycle=1\n'
    printf 'result_banks=1\nrange_passes=0\nindex_partitions=1\n'
} > "$out/source.txt"
sha256sum "$gem5" "$binary" \
    "$root/benchmarks/API/test_xrage_zero_payload.cpp" \
    "$root/benchmarks/API/MAA_gem5.hpp" \
    "$root/src/mem/MAA/XRAGEZeroPayload.hh" \
    "$root/src/mem/MAA/ALU.cc" "$root/src/mem/MAA/IndirectAccess.cc" \
    "$root/src/mem/MAA/IF.cc" "$root/src/mem/MAA/CpuSidePort.cc" \
    > "$out/artifact_sha256.txt"

run_case() {
    local n=$1
    local mode=$2
    local destination=$3
    shift 3
    MAA_LOGICAL_TILE_ELEMENTS=4096 \
    MAA_PHYSICAL_TILE_ELEMENTS=4096 \
    MAA_OFFSET_TABLE_ENTRIES=4096 \
    MAA_OFFSET_EPOCH_ENTRIES=4096 \
    MAA_ROW_TABLE_SLICES=16 \
    MAA_ROW_TABLE_ROWS=16 \
    GEM5_BIN="$gem5" "$@" \
        "$root/experiments/scripts/validate_virtual_gather.sh" \
        "$n" "$mode" "$destination" 3600 "$binary" \
        1 1 1 1 0 0 0 1 1 1
}

run_case 4096 suite "$out/suite" env | tee "$out/suite.controller.log"
run_case 4097 split "$out/split_4097" env | \
    tee "$out/split_4097.controller.log"

run_case 4097 too_large "$out/too_large" \
    env EXPECT_FAILURE=1 \
    EXPECTED_FAILURE_REGEX='strict-4K contract rejected configuration: logical_entries' \
    | tee "$out/too_large.controller.log"
run_case 257 ac_alias "$out/ac_alias" \
    env EXPECT_FAILURE=1 \
    EXPECTED_FAILURE_REGEX='requires separately registered non-aliasing source and destination regions' \
    | tee "$out/ac_alias.controller.log"
run_case 257 bc_alias "$out/bc_alias" \
    env EXPECT_FAILURE=1 \
    EXPECTED_FAILURE_REGEX='forbids consumed B/C.*overlap' \
    | tee "$out/bc_alias.controller.log"
run_case 257 drain "$out/live_drain" \
    env EXPECT_FAILURE=1 \
    EXPECTED_FAILURE_REGEX='checkpoint/drain requested with live instruction.*serialization is unsupported' \
    | tee "$out/live_drain.controller.log"
run_case 257 reset "$out/live_reset" \
    env EXPECT_FAILURE=1 \
    EXPECTED_FAILURE_REGEX='stats reset requested during a live fused direct-sink operation' \
    | tee "$out/live_reset.controller.log"

sum_stat() {
    local stats=$1
    local suffix=$2
    awk -v suffix="$suffix" '
        /^---------- Begin Simulation Statistics/ { active = 1; next }
        /^---------- End Simulation Statistics/ && active { exit }
        active && $1 ~ suffix "$" { sum += $2; found = 1 }
        END { print found ? sum : 0 }
    ' "$stats"
}

check_exact_counts() {
    local stats=$1
    local expected=$2
    local index_words fused_words result_words spd_reads spd_writes
    local write_issues write_completions
    index_words=$(sum_stat "$stats" 'IND_VirtIndexWords')
    fused_words=$(sum_stat "$stats" 'IND_FusedALUWords')
    result_words=$(sum_stat "$stats" 'IND_FusedResultTransferWords')
    spd_reads=$(sum_stat "$stats" 'IND_CyclesSPDReadAccess')
    spd_writes=$(sum_stat "$stats" 'IND_CyclesSPDWriteAccess')
    write_issues=$(sum_stat "$stats" 'IND_VirtWriteIssues')
    write_completions=$(sum_stat "$stats" 'IND_VirtWriteCompletions')
    [[ $index_words -eq $expected && $fused_words -eq $expected && \
       $result_words -eq $expected ]] || {
        echo "word accounting mismatch: index=$index_words fused=$fused_words result=$result_words expected=$expected" >&2
        exit 1
    }
    [[ $spd_reads -eq 0 && $spd_writes -eq 0 ]] || {
        echo "zero-payload operation used SPD data path: reads=$spd_reads writes=$spd_writes" >&2
        exit 1
    }
    [[ $write_issues -gt 0 && $write_issues -eq $write_completions ]] || {
        echo "write ACK closure mismatch: issued=$write_issues completed=$write_completions" >&2
        exit 1
    }
    printf 'exact_counts index=%s fused=%s result=%s spd_reads=%s spd_writes=%s writes=%s acks=%s\n' \
        "$index_words" "$fused_words" "$result_words" "$spd_reads" \
        "$spd_writes" "$write_issues" "$write_completions"
}

check_exact_counts "$out/suite/stats.txt" 8240 | tee "$out/suite.counts"
check_exact_counts "$out/split_4097/stats.txt" 4097 | \
    tee "$out/split_4097.counts"

grep -Eq 'XRAGE_ZERO_PAYLOAD_RESULT mode=suite logical=8240 descriptors=6 .*errors=0' \
    "$out/suite/restore.log"
grep -Eq 'XRAGE_ZERO_PAYLOAD_RESULT mode=split logical=4097 descriptors=2 .*errors=0' \
    "$out/split_4097/restore.log"

echo "XRAGE_ZERO_PAYLOAD_CORRECTNESS_PASS"
