#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "usage: $0 GEM5_BIN XRAGE_BIN INPUT_JSON OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
binary=$(realpath "$2")
input=$(realpath "$3")
out=$(realpath -m "$4")
physical=${MAA_PHYSICAL_TILE_ELEMENTS:-4096}
arm=${XRAGE_ARM:-direct_index_4k}
index_buffer_lines=${MAA_VIRTUAL_INDEX_BUFFER_LINES:-1}
direct_index_force_cache=${MAA_DIRECT_INDEX_FORCE_CACHE:-0}
reuse_checkpoint_dir=${XRAGE_REUSE_CHECKPOINT_DIR:-}
reuse_checkpoint_run=${XRAGE_REUSE_CHECKPOINT_RUN:-}

[[ $physical -gt 0 && $physical -le 16384 ]] || {
    echo "MAA_PHYSICAL_TILE_ELEMENTS must be in [1,16384]" >&2
    exit 2
}
[[ $index_buffer_lines -gt 0 && $index_buffer_lines -le 64 ]] || {
    echo "MAA_VIRTUAL_INDEX_BUFFER_LINES must be in [1,64]" >&2
    exit 2
}
[[ $direct_index_force_cache == 0 || $direct_index_force_cache == 1 ]] || {
    echo "MAA_DIRECT_INDEX_FORCE_CACHE must be 0 or 1" >&2
    exit 2
}
case "$arm" in
    native|fused|compact|direct_index_16k|direct_index_4k)
        workload_chunk_elements=16384
        ;;
    fused_4k)
        workload_chunk_elements=4096
        ;;
    *)
        echo "unsupported XRAGE_ARM: $arm" >&2
        exit 2
        ;;
esac
[[ -x $gem5 && -x $binary && -f $input ]] || {
    echo "missing gem5, XRAGE binary, or input" >&2
    exit 2
}
if [[ -n $reuse_checkpoint_dir && ! -d $reuse_checkpoint_dir ]]; then
    echo "XRAGE_REUSE_CHECKPOINT_DIR does not name a directory" >&2
    exit 2
fi
if [[ -n $reuse_checkpoint_run && ! -d $reuse_checkpoint_run ]]; then
    echo "XRAGE_REUSE_CHECKPOINT_RUN does not name a directory" >&2
    exit 2
fi
if [[ -n $reuse_checkpoint_dir && -n $reuse_checkpoint_run ]]; then
    echo "specify only one XRAGE checkpoint reuse source" >&2
    exit 2
fi
[[ ! -e $out ]] || {
    echo "refusing to overwrite existing output: $out" >&2
    exit 2
}

mkdir -p "$out"
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
options="-f $input"

{
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'arm=%s\n' "$arm"
    printf 'physical_tile_elements=%s\n' "$physical"
    printf 'virtual_index_buffer_lines=%s\n' "$index_buffer_lines"
    printf 'direct_index_force_cache=%s\n' "$direct_index_force_cache"
    printf 'reuse_checkpoint_dir=%s\n' "$reuse_checkpoint_dir"
    printf 'reuse_checkpoint_run=%s\n' "$reuse_checkpoint_run"
    printf 'maa_logical_tile_elements=16384\n'
    printf 'workload_chunk_elements=%s\n' "$workload_chunk_elements"
    printf 'input=%s\n' "$input"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'timeout=none\n'
} > "$out/manifest.txt"
git -C "$root" status --short > "$out/source_status.txt"
git -C "$root" diff --binary > "$out/source.diff"
sha256sum "$gem5" "$binary" "$input" "$config" "$ramulator" "$0" \
    > "$out/artifact_sha256.txt"

if [[ -n $reuse_checkpoint_run ]]; then
    checkpoint_run=$(realpath "$reuse_checkpoint_run")
    checkpoint_dir="$checkpoint_run/checkpoint"
    checkpoint_manifest="$checkpoint_run/manifest.txt"
    checkpoint_artifacts="$checkpoint_run/artifact_sha256.txt"
    checkpoint_attestation="$checkpoint_run/checkpoint_recovery_attestation.tsv"
    [[ -f $checkpoint_manifest && -f $checkpoint_artifacts ]] || {
        echo "reused XRAGE checkpoint lacks provenance manifests" >&2
        exit 1
    }
    [[ -f $checkpoint_attestation ]] &&
        grep -Fqx $'status\tpass' "$checkpoint_attestation" || {
        echo "reused XRAGE checkpoint lacks a pass attestation" >&2
        exit 1
    }
    sha256sum --status -c "$checkpoint_artifacts" || {
        echo "reused XRAGE checkpoint artifact verification failed" >&2
        exit 1
    }
    checkpoint_input=$(sed -n 's/^input=//p' "$checkpoint_manifest")
    [[ $checkpoint_input == "$input" ]] || {
        echo "reused XRAGE checkpoint input does not match" >&2
        exit 1
    }
    binary_sha256=$(sha256sum "$binary" | awk '{print $1}')
    grep -q "^$binary_sha256  " "$checkpoint_artifacts" || {
        echo "reused XRAGE checkpoint used a different benchmark binary" >&2
        exit 1
    }
    printf 'reused %s\n' "$checkpoint_dir" > "$out/checkpoint.command"
    printf 'reused-attested\n' > "$out/checkpoint.exit"
    sha256sum "$checkpoint_manifest" "$checkpoint_artifacts" \
        "$checkpoint_attestation" >> "$out/artifact_sha256.txt"
elif [[ -n $reuse_checkpoint_dir ]]; then
    checkpoint_dir=$(realpath "$reuse_checkpoint_dir")
    printf 'reused %s\n' "$checkpoint_dir" > "$out/checkpoint.command"
    printf 'reused\n' > "$out/checkpoint.exit"
else
    checkpoint_dir="$out/checkpoint"
    checkpoint_cmd=(
        "$gem5" --listener-mode=off --outdir="$checkpoint_dir"
        "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB
        --max-checkpoints=1 --cmd "$binary" --options "$options"
    )
    printf '%q ' "${checkpoint_cmd[@]}" > "$out/checkpoint.command"
    printf '\n' >> "$out/checkpoint.command"
    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
        /usr/bin/time -f 'wall=%e rss_kb=%M' "${checkpoint_cmd[@]}" \
        > "$out/checkpoint.log" 2>&1
    checkpoint_rc=$?
    set -e
    printf '%s\n' "$checkpoint_rc" > "$out/checkpoint.exit"
    [[ $checkpoint_rc -eq 0 ]] || {
        echo "XRAGE checkpoint failed with rc=$checkpoint_rc" >&2
        exit 1
    }
fi
ls "$checkpoint_dir"/cpt.* >/dev/null 2>&1 || {
    echo "XRAGE checkpoint missing" >&2
    exit 1
}
find "$checkpoint_dir" -maxdepth 2 -type f \
    \( -name m5.cpt -o -name '*.pmem' -o -name config.ini \) -print0 |
    sort -z | xargs -0 sha256sum > "$out/checkpoint_sha256.txt"

restore_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/run"
    "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --checkpoint-dir="$checkpoint_dir"
    --sys-clock 3.2GHz --cpu-clock 3.2GHz
    --caches --l1d_size=32kB --l1d_assoc=8
    --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16
    --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8
    --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16
    --l1i_write_buffers=8 --l2cache --l2_size=256kB --l2_assoc=4
    --l2-hwp-type=StridePrefetcher --l2_mshrs=32
    --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
    --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
    --cacheline_size=64 --mem-type Ramulator2
    --ramulator-config "$ramulator" --mem-channels=2 --maa_ncbus_width=32
    --maa --maa_num_maas=1 --maa_num_tile_elements=16384
    --maa_physical_tile_elements="$physical"
    --maa_l2_uncacheable --maa_l3_uncacheable
    --maa_num_initial_row_table_slices=32
    --maa_virtual_combine_slots=384 --maa_virtual_combine_words=4096
    --maa_virtual_combine_ways=4 --maa_virtual_combine_banks=0
    --maa_virtual_response_slots=128 --maa_virtual_response_word_pool=480
    --maa_virtual_words_per_cycle=4 --maa_virtual_max_outstanding_writes=64
    --maa_virtual_masked_writes
    --maa_virtual_index_buffer_lines="$index_buffer_lines"
    --cmd "$binary" --options "$options"
)
if [[ $direct_index_force_cache == 1 ]]; then
    restore_cmd+=(--maa_direct_index_force_cache)
fi
printf '%q ' "${restore_cmd[@]}" > "$out/restore.command"
printf '\n' >> "$out/restore.command"
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
    /usr/bin/time -f 'wall=%e rss_kb=%M' "${restore_cmd[@]}" \
    > "$out/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" > "$out/restore.exit"
[[ $restore_rc -eq 0 ]] || {
    echo "XRAGE restore failed with rc=$restore_rc" >&2
    exit 1
}

log="$out/restore.log"
stats="$out/run/stats.txt"
grep -q '^MAA_GATHER_VERIFY_PASS ' "$log" || {
    echo "XRAGE exact gather verifier did not pass" >&2
    exit 1
}
grep -q 'Exiting @ tick .* because m5_exit instruction encountered' "$log" || {
    echo "XRAGE restore lacks terminal m5_exit" >&2
    exit 1
}
if grep -Eqi 'panic|fatal|segmentation fault|MAA_GATHER_VERIFY_FAIL' "$log"; then
    echo "XRAGE restore contains a fatal marker" >&2
    exit 1
fi
[[ -s $stats ]] || {
    echo "XRAGE restore produced no final stats" >&2
    exit 1
}

hash=$(sed -n 's/^MAA_GATHER_VERIFY_PASS .* hash=\([0-9]*\)$/\1/p' "$log" | tail -1)
stats_blocks=$(awk '$1 == "simTicks" { count++ } END { print count + 0 }' \
    "$stats")
roi_ticks=$(awk '$1 == "simTicks" { print $2; exit }' "$stats")
final_ticks=$(awk '$1 == "simTicks" { value=$2 } END { print value }' "$stats")
[[ $stats_blocks -eq 2 && -n $hash && -n $roi_ticks &&
   -n $final_ticks && $final_ticks -ge $roi_ticks ]] || {
    echo "XRAGE result extraction failed" >&2
    exit 1
}
first_stat() {
    awk -v key="$1" '$1 == key { print $2; exit }' "$stats"
}
first_stat_or_zero() {
    awk -v key="$1" \
        '$1 == key { print $2; found=1; exit } END { if (!found) print 0 }' \
        "$stats"
}
write_issues=$(first_stat system.maa.I0_IND_VirtWriteIssues)
write_completions=$(first_stat system.maa.I0_IND_VirtWriteCompletions)
pages_ready=$(first_stat system.maa.I0_IND_VirtPagesReady)
index_words=$(first_stat system.maa.I0_IND_VirtIndexWords)
index_cache_responses=$(first_stat_or_zero system.maa.I0_IND_VirtIndexCacheResponses)
index_mem_responses=$(first_stat_or_zero system.maa.I0_IND_VirtIndexMemResponses)
indirect_spd_reads=$(
    first_stat_or_zero system.maa.I0_IND_CyclesSPDReadAccess
)
for value in "$write_issues" "$write_completions" "$pages_ready" \
    "$index_words" "$index_cache_responses" "$index_mem_responses" \
    "$indirect_spd_reads"; do
    [[ -n $value ]] || {
        echo "XRAGE mechanism-counter extraction failed" >&2
        exit 1
    }
done
index_line_reads=$(first_stat_or_zero system.maa.I0_IND_VirtIndexLineReads)
if [[ $arm == direct_index_16k || $arm == direct_index_4k ]]; then
    if [[ $direct_index_force_cache == 1 ]]; then
        expected_cache=$index_line_reads
        expected_mem=0
    else
        expected_cache=0
        expected_mem=$index_line_reads
    fi
    [[ $index_cache_responses -eq $expected_cache &&
       $index_mem_responses -eq $expected_mem ]] || {
        echo "XRAGE direct-index route provenance failed: lines=$index_line_reads cache=$index_cache_responses memory=$index_mem_responses expected_cache=$expected_cache expected_memory=$expected_mem" >&2
        exit 1
    }
else
    [[ $index_cache_responses -eq 0 && $index_mem_responses -eq 0 ]] || {
        echo "XRAGE non-direct arm unexpectedly recorded direct-index responses" >&2
        exit 1
    }
fi
dram_total() {
    awk -v key="$1" '$1 == key ":" { value=$2 } END { if (value == "") exit 1; print value }' "$log"
}
ch0_rd=$(dram_total CH0_num_RD_commands_T)
ch0_act=$(dram_total CH0_num_ACT_commands_T)
ch0_pre=$(dram_total CH0_num_PRE_commands_T)
ch1_rd=$(dram_total CH1_num_RD_commands_T)
ch1_act=$(dram_total CH1_num_ACT_commands_T)
ch1_pre=$(dram_total CH1_num_PRE_commands_T)
for value in "$ch0_rd" "$ch0_act" "$ch0_pre" "$ch1_rd" "$ch1_act" "$ch1_pre"; do
    [[ $value =~ ^[0-9]+$ ]] || {
        echo "XRAGE DRAM command extraction failed" >&2
        exit 1
    }
done
{
    printf 'channel\tRD\tACT\tPRE\n'
    printf 'CH0\t%s\t%s\t%s\n' "$ch0_rd" "$ch0_act" "$ch0_pre"
    printf 'CH1\t%s\t%s\t%s\n' "$ch1_rd" "$ch1_act" "$ch1_pre"
    printf 'sum\t%s\t%s\t%s\n' \
        "$((ch0_rd + ch1_rd))" "$((ch0_act + ch1_act))" "$((ch0_pre + ch1_pre))"
} > "$out/dram_commands.tsv"
{
    printf 'output_hash\troi_simTicks\tfinal_simTicks\tstats_blocks'
    printf '\tvirtual_write_issues\tvirtual_write_completions'
    printf '\tvirtual_pages_ready\tdirect_index_words'
    printf '\tdirect_index_cache_responses\tdirect_index_mem_responses'
    printf '\tindirect_spd_read_cycles\n'
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$hash" "$roi_ticks" "$final_ticks" "$stats_blocks" \
        "$write_issues" "$write_completions" "$pages_ready" \
        "$index_words" "$index_cache_responses" "$index_mem_responses" \
        "$indirect_spd_reads"
} > "$out/result.tsv"
touch "$out/xrage_attribution_smoke.pass"
echo "PASS XRAGE $arm: hash=$hash roi_simTicks=$roi_ticks"
