#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
    echo "usage: $0 GEM5_BIN XRAGE_PERF_BIN INPUT_JSON EXACT_RUN OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
binary=$(realpath "$2")
input=$(realpath "$3")
exact_run=$(realpath "$4")
out=$(realpath -m "$5")
arm=${XRAGE_ARM:?XRAGE_ARM must identify the performance arm}
physical=${MAA_PHYSICAL_TILE_ELEMENTS:-4096}
index_buffer_lines=${MAA_VIRTUAL_INDEX_BUFFER_LINES:-8}
replicas=${XRAGE_REPLICAS:-3}
runner_source_commit=$(git -C "$root" rev-parse HEAD)
simulator_source_commit=${XRAGE_SIMULATOR_SOURCE_COMMIT:-$runner_source_commit}

case "$arm" in
    native|fused|compact|direct_index_16k|direct_index_4k)
        logical=16384
        chunk=16384
        ;;
    fused_4k)
        logical=4096
        chunk=4096
        ;;
    *)
        echo "unsupported XRAGE_ARM: $arm" >&2
        exit 2
        ;;
esac
[[ $physical -gt 0 && $physical -le 16384 ]] || {
    echo "MAA_PHYSICAL_TILE_ELEMENTS must be in [1,16384]" >&2
    exit 2
}
[[ $index_buffer_lines -gt 0 && $index_buffer_lines -le 64 ]] || {
    echo "MAA_VIRTUAL_INDEX_BUFFER_LINES must be in [1,64]" >&2
    exit 2
}
[[ $replicas -ge 1 && $replicas -le 10 ]] || {
    echo "XRAGE_REPLICAS must be in [1,10]" >&2
    exit 2
}
[[ $simulator_source_commit =~ ^[0-9a-f]{40}$ ]] || {
    echo "XRAGE_SIMULATOR_SOURCE_COMMIT must be a full Git commit" >&2
    exit 2
}
[[ -x $gem5 && -x $binary && -f $input && -d $exact_run ]] || {
    echo "missing gem5, performance binary, input, or exact run" >&2
    exit 2
}
[[ ! -e $out ]] || {
    echo "refusing to overwrite existing output: $out" >&2
    exit 2
}

for path in manifest.txt artifact_sha256.txt restore.log result.tsv; do
    [[ -f $exact_run/$path ]] || {
        echo "exact reference lacks $path: $exact_run" >&2
        exit 1
    }
done
if [[ ! -f $exact_run/xrage_attribution_smoke.pass &&
      ! -f $exact_run/xrage_checkpoint_recovery.pass ]]; then
    echo "exact reference lacks a pass marker: $exact_run" >&2
    exit 1
fi
sha256sum --status -c "$exact_run/artifact_sha256.txt" || {
    echo "exact reference artifacts changed: $exact_run" >&2
    exit 1
}
exact_input=$(sed -n 's/^input=//p' "$exact_run/manifest.txt")
[[ -f $exact_input ]] || {
    echo "exact reference input is missing" >&2
    exit 1
}
[[ $(sha256sum "$exact_input" | cut -d' ' -f1) == \
   $(sha256sum "$input" | cut -d' ' -f1) ]] || {
    echo "performance and exact-reference inputs differ" >&2
    exit 1
}
exact_length=$(sed -n \
    's/^MAA_GATHER_VERIFY_PASS length=\([0-9][0-9]*\) hash=[0-9][0-9]*$/\1/p' \
    "$exact_run/restore.log" | tail -1)
exact_hash=$(sed -n \
    's/^MAA_GATHER_VERIFY_PASS length=[0-9][0-9]* hash=\([0-9][0-9]*\)$/\1/p' \
    "$exact_run/restore.log" | tail -1)
[[ -n $exact_length && -n $exact_hash ]] || {
    echo "exact reference lacks an exact-output marker" >&2
    exit 1
}
if grep -aFq 'MAA_GATHER_VERIFY_PASS length=' "$binary"; then
    echo "performance binary contains the exact-verifier marker" >&2
    exit 1
fi

mkdir -p "$out"
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
options="-f $input"
{
    printf 'source_commit=%s\n' "$simulator_source_commit"
    printf 'runner_source_commit=%s\n' "$runner_source_commit"
    printf 'arm=%s\n' "$arm"
    printf 'physical_tile_elements=%s\n' "$physical"
    printf 'maa_logical_tile_elements=%s\n' "$logical"
    printf 'workload_chunk_elements=%s\n' "$chunk"
    printf 'virtual_index_buffer_lines=%s\n' "$index_buffer_lines"
    printf 'replicas=%s\n' "$replicas"
    printf 'binary=%s\n' "$binary"
    printf 'input=%s\n' "$input"
    printf 'exact_reference=%s\n' "$exact_run"
    printf 'exact_length=%s\n' "$exact_length"
    printf 'exact_hash=%s\n' "$exact_hash"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'timeout=none\n'
} > "$out/manifest.txt"
git -C "$root" status --short > "$out/source_status.txt"
git -C "$root" diff --binary > "$out/source.diff"
sha256sum "$gem5" "$binary" "$input" "$config" "$ramulator" \
    "$exact_run/manifest.txt" "$exact_run/artifact_sha256.txt" \
    "$exact_run/result.tsv" > "$out/artifact_sha256.txt"

checkpoint_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint"
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
mapfile -t checkpoint_dirs < <(find "$out/checkpoint" -mindepth 1 \
    -maxdepth 1 -type d -name 'cpt.*' -print | sort)
[[ ${#checkpoint_dirs[@]} -eq 1 ]] || {
    echo "expected exactly one XRAGE checkpoint" >&2
    exit 1
}
checkpoint_dir=${checkpoint_dirs[0]}
for path in "$checkpoint_dir/m5.cpt" \
    "$checkpoint_dir/system.physmem.store0.pmem"; do
    [[ -s $path ]] || {
        echo "XRAGE checkpoint image is missing: $path" >&2
        exit 1
    }
done
sha256sum "$checkpoint_dir/m5.cpt" \
    "$checkpoint_dir/system.physmem.store0.pmem" \
    "$out/checkpoint/config.ini" > "$out/checkpoint_sha256.txt"

restore_base=(
    "$gem5" --listener-mode=off
    "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --checkpoint-dir="$out/checkpoint"
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
    --maa --maa_num_maas=1 --maa_num_tile_elements="$logical"
    --maa_physical_tile_elements="$physical"
    --maa_l2_uncacheable --maa_l3_uncacheable
    --maa_num_initial_row_table_slices=32
    --maa_virtual_combine_slots=384 --maa_virtual_combine_words=4096
    --maa_virtual_combine_ways=4 --maa_virtual_combine_banks=0
    --maa_virtual_response_slots=128 --maa_virtual_response_word_pool=480
    --maa_virtual_words_per_cycle=4 --maa_virtual_max_outstanding_writes=64
    --maa_virtual_index_buffer_lines="$index_buffer_lines"
    --maa_virtual_masked_writes --cmd "$binary" --options "$options"
)

printf 'replica\troi_simTicks\tfinal_simTicks\tstats_blocks' \
    > "$out/results.tsv"
printf '\tvirtual_write_issues\tvirtual_write_completions' \
    >> "$out/results.tsv"
printf '\tvirtual_pages_ready\tdirect_index_words' \
    >> "$out/results.tsv"
printf '\tindirect_spd_read_cycles\n' >> "$out/results.tsv"

first_stat_or_zero() {
    local stats=$1
    local key=$2
    awk -v key="$key" \
        '$1 == key { print $2; found=1; exit } END { if (!found) print 0 }' \
        "$stats"
}

for ((replica = 1; replica <= replicas; replica++)); do
    run="$out/replica_$replica"
    mkdir -p "$run"
    restore_cmd=("${restore_base[@]}")
    restore_cmd=("${restore_cmd[@]:0:2}" --outdir="$run" \
        "${restore_cmd[@]:2}")
    printf '%q ' "${restore_cmd[@]}" > "$run/restore.command"
    printf '\n' >> "$run/restore.command"
    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
        /usr/bin/time -f 'wall=%e rss_kb=%M' "${restore_cmd[@]}" \
        > "$run/restore.log" 2>&1
    restore_rc=$?
    set -e
    printf '%s\n' "$restore_rc" > "$run/restore.exit"
    [[ $restore_rc -eq 0 ]] || {
        echo "XRAGE replica $replica failed with rc=$restore_rc" >&2
        exit 1
    }
    log="$run/restore.log"
    stats="$run/stats.txt"
    [[ $(grep -Fxc 'ROI End!!!' "$log" || true) -eq 1 ]] || {
        echo "XRAGE replica $replica lacks one ROI completion" >&2
        exit 1
    }
    [[ $(grep -c 'because m5_exit instruction encountered' "$log" || true) \
        -eq 1 ]] || {
        echo "XRAGE replica $replica lacks one terminal m5_exit" >&2
        exit 1
    }
    if grep -Eqi 'panic|fatal|segmentation fault|MAA_GATHER_VERIFY_(PASS|FAIL)' \
        "$log"; then
        echo "XRAGE replica $replica has a fatal or verifier marker" >&2
        exit 1
    fi
    grep -Fq "MAA gather execution $exact_length/$chunk" "$log" || {
        echo "XRAGE replica $replica did not execute the expected range" >&2
        exit 1
    }
    [[ -s $stats ]] || {
        echo "XRAGE replica $replica produced no stats" >&2
        exit 1
    }
    mapfile -t ticks < <(awk '$1 == "simTicks" { print $2 }' "$stats")
    [[ ${#ticks[@]} -eq 2 && ${ticks[0]} -gt 0 && \
       ${ticks[1]} -ge ${ticks[0]} ]] || {
        echo "XRAGE replica $replica has invalid stats blocks" >&2
        exit 1
    }
    writes=$(first_stat_or_zero "$stats" system.maa.I0_IND_VirtWriteIssues)
    completions=$(first_stat_or_zero "$stats" \
        system.maa.I0_IND_VirtWriteCompletions)
    pages=$(first_stat_or_zero "$stats" system.maa.I0_IND_VirtPagesReady)
    index_words=$(first_stat_or_zero "$stats" system.maa.I0_IND_VirtIndexWords)
    spd_reads=$(first_stat_or_zero "$stats" \
        system.maa.I0_IND_CyclesSPDReadAccess)
    [[ $writes -eq $completions ]] || {
        echo "XRAGE replica $replica has incomplete virtual writes" >&2
        exit 1
    }
    printf '%s\t%s\t%s\t2\t%s\t%s\t%s\t%s\t%s\n' \
        "$replica" "${ticks[0]}" "${ticks[1]}" "$writes" \
        "$completions" "$pages" "$index_words" "$spd_reads" \
        >> "$out/results.tsv"
done

touch "$out/xrage_performance.pass"
echo "PASS XRAGE performance $arm: replicas=$replicas exact_hash=$exact_hash"
