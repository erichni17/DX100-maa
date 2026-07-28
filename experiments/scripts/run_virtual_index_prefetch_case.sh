#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
    echo "usage: $0 GEM5_BIN TEST_BIN N BUFFER_LINES OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
binary=$(realpath "$2")
n=$3
buffer_lines=$4
out=$(realpath -m "$5")
pattern=${VIRTUAL_INDEX_PATTERN:-random}
physical=${VIRTUAL_PHYSICAL_TILE_ELEMENTS:-4096}
rt_rows=${VIRTUAL_RT_ROWS_PER_SLICE:-64}
rt_entries=${VIRTUAL_RT_ENTRIES_PER_SUBSLICE_ROW:-8}
response_slots=${VIRTUAL_RESPONSE_SLOTS:-96}
response_word_pool=${VIRTUAL_RESPONSE_WORD_POOL:-480}
grow_order=${MAA_VIRTUAL_GROW_ORDER:-0}
debug_flags=${VIRTUAL_DEBUG_FLAGS:-}
debug_args=()
if [[ -n $debug_flags ]]; then
    debug_args=("--debug-flags=$debug_flags" "--debug-file=virtual-debug.log")
fi
[[ $grow_order == 0 || $grow_order == 1 ]] || {
    echo "MAA_VIRTUAL_GROW_ORDER must be 0 or 1" >&2
    exit 2
}
grow_order_args=()
if [[ $grow_order == 1 ]]; then
    grow_order_args+=(--maa_virtual_grow_order)
fi

[[ $n =~ ^[1-9][0-9]*$ && $n -le 16384 ]] || {
    echo "N must be in [1,16384]" >&2
    exit 2
}
[[ $buffer_lines =~ ^[1-9][0-9]*$ && $buffer_lines -le 64 ]] || {
    echo "BUFFER_LINES must be in [1,64]" >&2
    exit 2
}
case "$pattern" in
random|fanout|same_line|line_revisit) ;;
*) echo "invalid VIRTUAL_INDEX_PATTERN: $pattern" >&2; exit 2 ;;
esac
for value in "$physical" "$rt_rows" "$rt_entries" "$response_slots" \
             "$response_word_pool"; do
    [[ $value =~ ^[1-9][0-9]*$ ]] || {
        echo "virtual gate capacities must be positive integers" >&2
        exit 2
    }
done
[[ $physical -le 16384 ]] || {
    echo "VIRTUAL_PHYSICAL_TILE_ELEMENTS must not exceed 16384" >&2
    exit 2
}
if [[ -e $out ]]; then
    echo "refusing to overwrite existing output path: $out" >&2
    exit 2
fi
mkdir -p "$out"

config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
{
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'elements=%s\n' "$n"
    printf 'pattern=%s\n' "$pattern"
    printf 'logical_tile_elements=16384\n'
    printf 'physical_tile_elements=%s\n' "$physical"
    printf 'virtual_index_buffer_lines=%s\n' "$buffer_lines"
    printf 'row_table_rows_per_slice=%s\n' "$rt_rows"
    printf 'row_table_entries_per_subslice_row=%s\n' "$rt_entries"
    printf 'virtual_response_slots=%s\n' "$response_slots"
    printf 'virtual_response_word_pool=%s\n' "$response_word_pool"
    printf 'virtual_grow_order=%s\n' "$grow_order"
    printf 'debug_flags=%s\n' "$debug_flags"
    printf 'index_payload_capacity_bytes=%s\n' "$((buffer_lines * 64))"
    printf 'timeout=none\n'
} > "$out/manifest.txt"
git -C "$root" status --short > "$out/source_status.txt"
git -C "$root" diff --binary > "$out/source.diff"
sha256sum "$gem5" "$binary" "$config" "$ramulator" "$0" \
    "$root/benchmarks/API/test_virtual_index_gather.cpp" \
    "$root/configs/common/MAAConfig.py" \
    "$root/configs/common/Options.py" \
    "$root/src/mem/MAA/IndirectAccess.cc" \
    "$root/src/mem/MAA/IndirectAccess.hh" \
    "$root/src/mem/MAA/MAA.cc" \
    "$root/src/mem/MAA/MAA.hh" \
    "$root/src/mem/MAA/MAA.py" \
    "$out/source.diff" "$out/source_status.txt" \
    > "$out/artifact_sha256.txt"

set +e
/usr/bin/time -f 'checkpoint_wall=%e checkpoint_rss_kb=%M' \
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" \
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
    --max-checkpoints=1 --cmd "$binary" --options "$n $pattern" \
    > "$out/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]] || {
    echo "checkpoint failed with rc=$checkpoint_rc" >&2
    exit 1
}
grep -Eq 'VIRTUAL_GATHER_LAYOUT mem_size=2147483648' \
    "$out/checkpoint.log" || {
    echo "binary/config memory map mismatch" >&2
    exit 1
}

set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
/usr/bin/time -f 'restore_wall=%e restore_rss_kb=%M' \
    "$gem5" "${debug_args[@]}" --listener-mode=off \
    --outdir="$out/run" "$config" \
    --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB \
    --checkpoint-dir="$out/checkpoint" \
    --sys-clock 3.2GHz --cpu-clock 3.2GHz \
    --caches --l1d_size=32kB --l1d_assoc=8 \
    --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8 \
    --l1i_size=32kB --l1i_assoc=8 \
    --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16 --l1i_write_buffers=8 \
    --l2cache --l2_size=256kB --l2_assoc=4 \
    --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16 \
    --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256 \
    --l3_write_buffers=128 --l3_ports=4 --cacheline_size=64 \
    --mem-type Ramulator2 --ramulator-config "$ramulator" --mem-channels=1 \
    --maa --maa_num_tile_elements=16384 \
    --maa_physical_tile_elements="$physical" \
    --maa_num_initial_row_table_slices=16 \
    --maa_num_row_table_rows_per_slice="$rt_rows" \
    --maa_num_row_table_entries_per_subslice_row="$rt_entries" \
    --maa_virtual_combine_slots=384 --maa_virtual_combine_words=4096 \
    --maa_virtual_combine_ways=4 --maa_virtual_combine_banks=0 \
    --maa_virtual_response_slots="$response_slots" \
    --maa_virtual_response_word_pool="$response_word_pool" \
    --maa_virtual_words_per_cycle=4 \
    --maa_virtual_max_outstanding_writes=64 --maa_virtual_masked_writes \
    --maa_virtual_index_buffer_lines="$buffer_lines" \
    "${grow_order_args[@]}" \
    --cmd "$binary" --options "$n $pattern" > "$out/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" > "$out/restore.exit"
[[ $restore_rc -eq 0 ]] || {
    echo "restore failed with rc=$restore_rc" >&2
    exit 1
}

result_count=$(grep -Ec \
    "^VIRTUAL_GATHER_RESULT n=${n} pattern=${pattern} hash=[0-9]+ errors=0$" \
    "$out/restore.log" || true)
roi_count=$(grep -Fxc 'ROI Ended' "$out/restore.log" || true)
fatal_count=$(grep -Eic \
    'panic|fatal|assert|abort|segmentation fault|error:' \
    "$out/restore.log" || true)
[[ $result_count -eq 1 && $roi_count -eq 1 && $fatal_count -eq 0 ]] || {
    printf 'invalid completion: result=%s roi=%s fatal=%s\n' \
        "$result_count" "$roi_count" "$fatal_count" >&2
    exit 1
}
output_hash=$(sed -nE \
    "s/^VIRTUAL_GATHER_RESULT n=${n} pattern=${pattern} hash=([0-9]+) errors=0$/\\1/p" \
    "$out/restore.log")
[[ $output_hash =~ ^[0-9]+$ ]] || {
    echo "missing deterministic output hash" >&2
    exit 1
}

read -r ticks insts line_reads line_hwm index_words index_hwm rt_full \
    write_issues write_completions spd_reads total_indirect_reads < <(
    awk '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 == "simTicks" { ticks = $2 }
        section == 1 && $1 == "simInsts" { insts = $2 }
        section == 1 && $1 ~ /IND_VirtIndexLineReads$/ { lr += $2 }
        section == 1 && $1 ~ /IND_VirtIndexLineHighWater$/ { lh += $2 }
        section == 1 && $1 ~ /IND_VirtIndexWords$/ { iw += $2 }
        section == 1 && $1 ~ /IND_VirtIndexWordHighWater$/ { wh += $2 }
        section == 1 && $1 ~ /IND_NumRTFull$/ { rf += $2 }
        section == 1 && $1 ~ /IND_VirtWriteIssues$/ { wi += $2 }
        section == 1 && $1 ~ /IND_VirtWriteCompletions$/ { wc += $2 }
        section == 1 && $1 ~ /IND_CyclesSPDReadAccess$/ { sr += $2 }
        section == 1 && $1 ~ /IND_LoadsCacheHitResponding$/ { cr += $2 }
        section == 1 && $1 ~ /IND_LoadsCacheHitAccessing$/ { ca += $2 }
        section == 1 && $1 ~ /IND_LoadsMemAccessing$/ { mr += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            print ticks + 0, insts + 0, lr + 0, lh + 0, iw + 0,
                  wh + 0, rf + 0, wi + 0, wc + 0, sr + 0,
                  cr + ca + mr
            exit
        }
    ' "$out/run/stats.txt"
)
[[ $ticks -gt 0 && $insts -gt 0 && $line_reads -gt 0 ]] || {
    echo "missing performance or direct-index activity" >&2
    exit 1
}
[[ $index_words -eq $n ]] || {
    echo "direct-index gate delivered $index_words/$n words" >&2
    exit 1
}
[[ $line_hwm -gt 0 && $line_hwm -le $buffer_lines ]] || {
    echo "line high water $line_hwm exceeds depth $buffer_lines" >&2
    exit 1
}
[[ $index_hwm -gt 0 && $index_hwm -le $((buffer_lines * 16)) ]] || {
    echo "word high water $index_hwm exceeds depth payload" >&2
    exit 1
}
[[ $write_issues -gt 0 && $write_issues -eq $write_completions ]] || {
    echo "unbalanced virtual retirement: $write_issues/$write_completions" >&2
    exit 1
}
source_reads=$((total_indirect_reads - line_reads))
[[ $source_reads -gt 0 ]] || {
    echo "invalid source-read count: total=$total_indirect_reads index=$line_reads" >&2
    exit 1
}
[[ $spd_reads -eq 0 ]] || {
    echo "direct-index case used $spd_reads SPD read cycles" >&2
    exit 1
}

{
    printf 'elements\tpattern\tphysical_elements\tbuffer_lines\tbuffer_bytes'
    printf '\toutput_hash\tsimTicks\tsimInsts'
    printf '\tline_reads\tline_hwm\tindex_words\tindex_hwm\trt_full'
    printf '\tsource_reads\twrite_issues\twrite_completions\tspd_read_cycles\n'
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$n" "$pattern" "$physical" "$buffer_lines" \
        "$((buffer_lines * 64))" "$output_hash" \
        "$ticks" "$insts" "$line_reads" "$line_hwm" "$index_words" \
        "$index_hwm" "$rt_full" "$source_reads" "$write_issues" \
        "$write_completions" "$spd_reads"
} > "$out/result.tsv"
touch "$out/virtual_index_prefetch_case.pass"
cat "$out/result.tsv"
