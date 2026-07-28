#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "usage: $0 GEM5_BIN TEST_BIN CASE OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
binary=$(realpath "$2")
case_name=$3
out=$(realpath -m "$4")

case "$case_name" in
native_16k)
    mode=native
    page=16384
    physical=16384
    virtual=0
    direct=0
    reload_only=0
    ;;
native_4k)
    mode=native
    page=4096
    physical=4096
    virtual=0
    direct=0
    reload_only=0
    ;;
paged_16k)
    mode=paged
    page=16384
    physical=16384
    virtual=1
    direct=1
    reload_only=0
    ;;
paged_4k)
    mode=paged
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=0
    ;;
paged_staged_16k)
    mode=paged_staged
    page=16384
    physical=16384
    virtual=1
    direct=0
    reload_only=0
    ;;
paged_reload_warm_4k)
    mode=paged_reload_warm
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=1
    ;;
paged_reload_cold_4k)
    mode=paged_reload_cold
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=1
    ;;
*)
    echo "unknown consumer case: $case_name" >&2
    exit 2
    ;;
esac

if [[ -e $out ]]; then
    echo "refusing to overwrite existing output path: $out" >&2
    exit 2
fi
mkdir -p "$out"

config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
{
    printf 'case=%s\n' "$case_name"
    printf 'mode=%s\n' "$mode"
    printf 'logical_tile_elements=16384\n'
    printf 'page_elements=%s\n' "$page"
    printf 'physical_tile_elements=%s\n' "$physical"
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'timeout=none\n'
} > "$out/manifest.txt"
git -C "$root" status --short > "$out/source_status.txt"
git -C "$root" diff --binary > "$out/source.diff"
sha256sum "$gem5" "$binary" "$config" "$ramulator" "$0" \
    "$root/benchmarks/API/test_virtual_tile_consumer.cpp" \
    "$root/src/mem/MAA/IndirectAccess.cc" \
    "$root/src/mem/MAA/IndirectAccess.hh" \
    "$out/source.diff" "$out/source_status.txt" \
    > "$out/artifact_sha256.txt"

set +e
/usr/bin/time -f 'checkpoint_wall=%e checkpoint_rss_kb=%M' \
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" \
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
    --max-checkpoints=1 --cmd "$binary" --options "$mode $page" \
    > "$out/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]] || {
    echo "checkpoint failed with rc=$checkpoint_rc" >&2
    exit 1
}
grep -Eq "VIRTUAL_TILE_CONSUMER_LAYOUT mode=${mode} page_elements=${page} logical_elements=16384 mem_size=2147483648" \
    "$out/checkpoint.log" || {
    echo "binary/config consumer contract mismatch" >&2
    exit 1
}

set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
/usr/bin/time -f 'restore_wall=%e restore_rss_kb=%M' \
    "$gem5" --listener-mode=off --outdir="$out/run" \
    --debug-flags=MAAVirtualTrace --debug-file=virtual_trace.log "$config" \
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
    --maa_virtual_combine_slots=384 --maa_virtual_combine_words=4096 \
    --maa_virtual_combine_ways=4 --maa_virtual_combine_banks=0 \
    --maa_virtual_response_slots=96 --maa_virtual_response_word_pool=480 \
    --maa_virtual_words_per_cycle=4 \
    --maa_virtual_max_outstanding_writes=64 --maa_virtual_masked_writes \
    --maa_virtual_index_buffer_lines=4 \
    --cmd "$binary" --options "$mode $page" > "$out/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" > "$out/restore.exit"
[[ $restore_rc -eq 0 ]] || {
    echo "restore failed with rc=$restore_rc" >&2
    exit 1
}

result_count=$(grep -Ec \
    "^VIRTUAL_TILE_CONSUMER_RESULT mode=${mode} page_elements=${page} hash=[0-9]+ errors=0$" \
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
    "s/^VIRTUAL_TILE_CONSUMER_RESULT mode=${mode} page_elements=${page} hash=([0-9]+) errors=0$/\\1/p" \
    "$out/restore.log")

read -r ticks insts index_words index_hwm write_issues write_completions \
    pages_ready pages_ready_early first_page_cycles all_page_cycles \
    page_span_cycles \
    indirect_spd_reads stream_spd_reads stream_writes alu_compute \
    l3_read_hits l3_read_misses memory_bytes_read cpu_cycles < <(
    awk '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 == "simTicks" { ticks = $2 }
        section == 1 && $1 == "simInsts" { insts = $2 }
        section == 1 && $1 ~ /IND_VirtIndexWords$/ { iw += $2 }
        section == 1 && $1 ~ /IND_VirtIndexWordHighWater$/ { hw += $2 }
        section == 1 && $1 ~ /IND_VirtWriteIssues$/ { wi += $2 }
        section == 1 && $1 ~ /IND_VirtWriteCompletions$/ { wc += $2 }
        section == 1 && $1 ~ /IND_VirtPagesReady$/ { pr += $2 }
        section == 1 && $1 ~ /IND_VirtPagesReadyBeforeSourceDrain$/ { pe += $2 }
        section == 1 && $1 ~ /IND_VirtFirstPageReadyCycles$/ { pf += $2 }
        section == 1 && $1 ~ /IND_VirtAllPagesReadyCycles$/ { pa += $2 }
        section == 1 && $1 ~ /IND_VirtPageReadySpanCycles$/ { ps += $2 }
        section == 1 && $1 ~ /IND_CyclesSPDReadAccess$/ { ir += $2 }
        section == 1 && $1 ~ /STR_CyclesSPDReadAccess$/ { sr += $2 }
        section == 1 && $1 == "system.maa.numInst_STRWR" { sw += $2 }
        section == 1 && $1 ~ /ALU_CyclesCompute$/ { ac += $2 }
        section == 1 && $1 == "system.l3.ReadReq_T.hits::maa" { lh = $2 }
        section == 1 && $1 == "system.l3.ReadReq_T.misses::maa" { lm = $2 }
        section == 1 && $1 == "system.mem_ctrls.bytesRead::maa" { mb = $2 }
        section == 1 && $1 == "system.switch_cpus0.numCycles" { cc = $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            print ticks + 0, insts + 0, iw + 0, hw + 0,
                  wi + 0, wc + 0, pr + 0, pe + 0, pf + 0, pa + 0,
                  ps + 0, ir + 0, sr + 0, sw + 0, ac + 0,
                  lh + 0, lm + 0, mb + 0, cc + 0
            exit
        }
    ' "$out/run/stats.txt"
)
[[ $ticks -gt 0 && $insts -gt 0 && $stream_spd_reads -gt 0 && \
   $stream_writes -gt 0 && $alu_compute -gt 0 ]] || {
    echo "missing first-ROI performance or consumer activity" >&2
    exit 1
}
if [[ $reload_only -eq 1 ]]; then
    [[ $index_words -eq 0 && $write_issues -eq 0 && \
       $write_completions -eq 0 && $indirect_spd_reads -eq 0 ]] || {
        echo "reload-only window includes gather activity" >&2
        exit 1
    }
elif [[ $virtual -eq 1 ]]; then
    [[ $write_issues -gt 0 && $write_issues -eq $write_completions ]] || {
        echo "unbalanced virtual retirement: $write_issues/$write_completions" >&2
        exit 1
    }
    expected_pages=$((16384 / physical))
    [[ $pages_ready -eq $expected_pages && $first_page_cycles -gt 0 && \
       $all_page_cycles -ge $first_page_cycles && \
       $page_span_cycles -eq $((all_page_cycles - first_page_cycles)) ]] || {
        echo "invalid virtual page readiness: pages=$pages_ready/$expected_pages first=$first_page_cycles all=$all_page_cycles span=$page_span_cycles" >&2
        exit 1
    }
    trace="$out/run/virtual_trace.log"
    trace_pages=$(grep -c 'event=page_ready' "$trace" || true)
    [[ $trace_pages -eq $expected_pages ]] || {
        echo "invalid virtual page trace count: $trace_pages/$expected_pages" >&2
        exit 1
    }
    {
        printf 'tick\tunit\tpage\tready_count\ttotal_pages'
        printf '\tissued_words\tcompleted_words\tsources_drained\n'
        awk '
            /event=page_ready/ {
                sub(/:$/, "", $1)
                for (i = 3; i <= NF; ++i) {
                    split($i, kv, "=")
                    value[kv[1]] = kv[2]
                }
                split(value["pages"], pages, "/")
                print $1, value["unit"], value["page"], pages[1], pages[2],
                      value["issued"], value["completed"],
                      value["sources_drained"]
                delete value
                delete pages
            }
        ' OFS='\t' "$trace"
    } > "$out/page_readiness.tsv"
    if [[ $direct -eq 1 ]]; then
        [[ $index_words -eq 16384 && $index_hwm -gt 0 && $index_hwm -le 64 ]] || {
            echo "invalid bounded index evidence: $index_words/$index_hwm" >&2
            exit 1
        }
        [[ $indirect_spd_reads -eq 0 ]] || {
            echo "direct-index gather used $indirect_spd_reads SPD read cycles" >&2
            exit 1
        }
    else
        [[ $index_words -eq 0 && $indirect_spd_reads -gt 0 ]] || {
            echo "staged-index gather did not use the expected SPD path" >&2
            exit 1
        }
    fi
else
    [[ $index_words -eq 0 && $write_issues -eq 0 && \
       $write_completions -eq 0 && $pages_ready -eq 0 ]] || {
        echo "native case activated virtual machinery" >&2
        exit 1
    }
fi

{
    printf 'case\toutput_hash\tsimTicks\tsimInsts\tindex_words\tindex_hwm'
    printf '\twrite_issues\twrite_completions\tindirect_spd_reads'
    printf '\tpages_ready\tpages_ready_before_source_drain'
    printf '\tfirst_page_ready_cycles\tall_pages_ready_cycles'
    printf '\tpage_ready_span_cycles'
    printf '\tstream_spd_reads\tstream_writes\talu_compute_cycles'
    printf '\tl3_read_hits_maa\tl3_read_misses_maa\tmemory_bytes_read_maa'
    printf '\tcpu_cycles\n'
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$case_name" "$output_hash" "$ticks" "$insts" \
        "$index_words" "$index_hwm" "$write_issues" \
        "$write_completions" "$indirect_spd_reads" "$pages_ready" \
        "$pages_ready_early" "$first_page_cycles" "$all_page_cycles" \
        "$page_span_cycles" \
        "$stream_spd_reads" "$stream_writes" "$alu_compute" \
        "$l3_read_hits" "$l3_read_misses" "$memory_bytes_read" \
        "$cpu_cycles"
} > "$out/result.tsv"
touch "$out/virtual_tile_consumer_case.pass"
cat "$out/result.tsv"
