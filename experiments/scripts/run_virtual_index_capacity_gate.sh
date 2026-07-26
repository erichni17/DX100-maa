#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 GEM5_BIN OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
out=$(realpath -m "$2")
validator="$root/experiments/scripts/validate_virtual_gather.sh"
source="$root/benchmarks/API/test_virtual_index_gather.cpp"
binary="$out/test_virtual_index_gather_T16K.o"

if [[ -e $out ]]; then
    echo "refusing to overwrite existing output path: $out" >&2
    exit 2
fi
mkdir -p "$out"

${CXX:-g++} -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O3 -Wall -g3 -fopenmp \
    -DGEM5 -DTILE_SIZE=16384 -DNUM_CORES=4 -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/build/x86/abi/x86/m5op.S" "$source" -o "$binary"

{
    printf 'simulator_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'logical_tile_elements=16384\n'
    printf 'physical_tile_elements=4096\n'
    printf 'index_buffer_bytes=64\n'
} > "$out/manifest.txt"
sha256sum "$gem5" "$binary" "$source" "$validator" "$0" \
    > "$out/artifact_sha256.txt"

MAA_PHYSICAL_TILE_ELEMENTS=4096 GEM5_BIN="$gem5" \
    "$validator" 16384 random "$out/random_16k_on_4k" 0 "$binary" \
    384 96 64 4096 1 0 480 4 4 4 \
    > "$out/random_16k_on_4k.controller.log" 2>&1

stats="$out/random_16k_on_4k/stats.txt"
read -r line_reads index_words index_hwm spd_reads write_issues \
    write_completions < <(
    awk '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ /IND_VirtIndexLineReads$/ { lr += $2 }
        section == 1 && $1 ~ /IND_VirtIndexWords$/ { iw += $2 }
        section == 1 && $1 ~ /IND_VirtIndexWordHighWater$/ { hw += $2 }
        section == 1 && $1 ~ /IND_CyclesSPDReadAccess$/ { sr += $2 }
        section == 1 && $1 ~ /IND_VirtWriteIssues$/ { wi += $2 }
        section == 1 && $1 ~ /IND_VirtWriteCompletions$/ { wc += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            print lr + 0, iw + 0, hw + 0, sr + 0, wi + 0, wc + 0
            exit
        }
    ' "$stats"
)
[[ $line_reads -gt 0 ]] || {
    echo "direct-index gate issued no index reads" >&2
    exit 1
}
[[ $index_words -eq 16384 ]] || {
    echo "direct-index gate delivered $index_words/16384 words" >&2
    exit 1
}
[[ $index_hwm -gt 0 && $index_hwm -le 16 ]] || {
    echo "direct-index buffer high water $index_hwm is outside [1,16]" >&2
    exit 1
}
[[ $spd_reads -eq 0 ]] || {
    echo "direct-index gate unexpectedly used $spd_reads SPD read cycles" >&2
    exit 1
}
[[ $write_issues -gt 0 && $write_issues -eq $write_completions ]] || {
    echo "retirement writes are unbalanced: $write_issues/$write_completions" >&2
    exit 1
}
printf 'line_reads=%s\nindex_words=%s\nindex_word_high_water=%s\n' \
    "$line_reads" "$index_words" "$index_hwm" > "$out/capacity_evidence.txt"
printf 'spd_read_cycles=%s\nwrite_issues=%s\nwrite_completions=%s\n' \
    "$spd_reads" "$write_issues" "$write_completions" \
    >> "$out/capacity_evidence.txt"

touch "$out/virtual_index_capacity_gate.pass"
