#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
    echo "usage: $0 GEM5_BIN XRAGE_BIN INPUT_JSON CHECKPOINT_RUN OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
binary=$(realpath "$2")
input=$(realpath "$3")
checkpoint_run=$(realpath "$4")
out=$(realpath -m "$5")
physical=${MAA_PHYSICAL_TILE_ELEMENTS:-4096}
arm=${XRAGE_ARM:-direct_index_4k}
grow_order=${MAA_VIRTUAL_GROW_ORDER:-0}
index_buffer_lines=${MAA_VIRTUAL_INDEX_BUFFER_LINES:-1}
runner_source_commit=$(git -C "$root" rev-parse HEAD)
checkpoint_manifest="$checkpoint_run/manifest.txt"
checkpoint_artifacts="$checkpoint_run/artifact_sha256.txt"
[[ -f $checkpoint_manifest && -f $checkpoint_artifacts ]] || {
    echo "source checkpoint is missing provenance files" >&2
    exit 1
}
checkpoint_source_commit=$(sed -n 's/^source_commit=//p' "$checkpoint_manifest")
simulator_source_commit=${XRAGE_SIMULATOR_SOURCE_COMMIT:-$checkpoint_source_commit}

[[ $physical -gt 0 && $physical -le 16384 ]] || {
    echo "MAA_PHYSICAL_TILE_ELEMENTS must be in [1,16384]" >&2
    exit 2
}
[[ $grow_order == 0 || $grow_order == 1 ]] || {
    echo "MAA_VIRTUAL_GROW_ORDER must be 0 or 1" >&2
    exit 2
}
[[ $index_buffer_lines -gt 0 && $index_buffer_lines -le 64 ]] || {
    echo "MAA_VIRTUAL_INDEX_BUFFER_LINES must be in [1,64]" >&2
    exit 2
}
[[ $simulator_source_commit =~ ^[0-9a-f]{40}$ ]] || {
    echo "XRAGE_SIMULATOR_SOURCE_COMMIT must be a full Git commit" >&2
    exit 2
}
case "$arm" in
    native|fused|compact|direct_index_16k|direct_index_4k)
        maa_logical_tile_elements=16384
        workload_chunk_elements=16384
        ;;
    fused_4k)
        maa_logical_tile_elements=4096
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
[[ ! -e $out ]] || {
    echo "refusing to overwrite existing recovery output: $out" >&2
    exit 2
}
checkpoint_exit=$(cat "$checkpoint_run/checkpoint.exit" 2>/dev/null || true)
checkpoint_attestation="$checkpoint_run/checkpoint_recovery_attestation.tsv"
if [[ $checkpoint_exit != 0 ]]; then
    [[ -f $checkpoint_attestation ]] &&
        grep -Fqx $'status\tpass' "$checkpoint_attestation" || {
        echo "source checkpoint lacks a zero exit or pass attestation" >&2
        exit 1
    }
fi
checkpoint_dir="$checkpoint_run/checkpoint"
compgen -G "$checkpoint_dir/cpt.*" >/dev/null || {
    echo "source checkpoint is missing cpt.*" >&2
    exit 1
}
checkpoint_arm=$(sed -n 's/^arm=//p' "$checkpoint_manifest")
checkpoint_physical=$(
    sed -n 's/^physical_tile_elements=//p' "$checkpoint_manifest"
)
checkpoint_input=$(sed -n 's/^input=//p' "$checkpoint_manifest")
[[ $checkpoint_arm == "$arm" && $checkpoint_physical == "$physical" &&
   $checkpoint_input == "$input" ]] || {
    echo "recovery configuration does not match checkpoint manifest" >&2
    exit 1
}
sha256sum --status -c "$checkpoint_artifacts" || {
    echo "source checkpoint artifact verification failed" >&2
    exit 1
}

mkdir -p "$out"
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
options="-f $input"

{
    printf 'source_commit=%s\n' "$simulator_source_commit"
    printf 'runner_source_commit=%s\n' "$runner_source_commit"
    printf 'checkpoint_run=%s\n' "$checkpoint_run"
    printf 'checkpoint_manifest_sha256=%s\n' \
        "$(sha256sum "$checkpoint_manifest" | awk '{print $1}')"
    printf 'arm=%s\n' "$arm"
    printf 'physical_tile_elements=%s\n' "$physical"
    printf 'maa_logical_tile_elements=%s\n' "$maa_logical_tile_elements"
    printf 'workload_chunk_elements=%s\n' "$workload_chunk_elements"
    printf 'virtual_grow_order=%s\n' "$grow_order"
    printf 'virtual_index_buffer_lines=%s\n' "$index_buffer_lines"
    printf 'input=%s\n' "$input"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'timeout=none\n'
} > "$out/manifest.txt"
git -C "$root" status --short > "$out/source_status.txt"
git -C "$root" diff --binary > "$out/source.diff"
sha256sum "$gem5" "$binary" "$input" "$config" "$ramulator" "$0" \
    "$checkpoint_manifest" "$checkpoint_artifacts" \
    "$checkpoint_run/checkpoint.command" \
    > "$out/artifact_sha256.txt"
if [[ -f $checkpoint_attestation ]]; then
    sha256sum "$checkpoint_attestation" >> "$out/artifact_sha256.txt"
fi
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
    --maa --maa_num_maas=1
    --maa_num_tile_elements="$maa_logical_tile_elements"
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
if [[ $grow_order == 1 ]]; then
    restore_cmd+=(--maa_virtual_grow_order)
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
    echo "XRAGE recovery restore failed with rc=$restore_rc" >&2
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
stats_blocks=$(awk '$1 == "simTicks" { count++ } END { print count + 0 }' "$stats")
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
indirect_spd_reads=$(first_stat_or_zero system.maa.I0_IND_CyclesSPDReadAccess)
for value in "$write_issues" "$write_completions" "$pages_ready" \
    "$index_words" "$indirect_spd_reads"; do
    [[ -n $value ]] || {
        echo "XRAGE mechanism-counter extraction failed" >&2
        exit 1
    }
done
{
    printf 'output_hash\troi_simTicks\tfinal_simTicks\tstats_blocks'
    printf '\tvirtual_write_issues\tvirtual_write_completions'
    printf '\tvirtual_pages_ready\tdirect_index_words'
    printf '\tindirect_spd_read_cycles\n'
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$hash" "$roi_ticks" "$final_ticks" "$stats_blocks" \
        "$write_issues" "$write_completions" "$pages_ready" \
        "$index_words" "$indirect_spd_reads"
} > "$out/result.tsv"
read -r dram_reads dram_activates dram_precharges < <(
    awk '
        $1 == "CH0_num_RD_commands_T:" { rd = $2 }
        $1 == "CH0_num_ACT_commands_T:" { act = $2 }
        $1 == "CH0_num_PRE_commands_T:" { pre = $2 }
        END { print rd + 0, act + 0, pre + 0 }
    ' "$log"
)
[[ $dram_reads -gt 0 && $dram_activates -gt 0 && $dram_precharges -gt 0 ]] || {
    echo "XRAGE DRAM command extraction failed" >&2
    exit 1
}
{
    printf 'dram_reads\tdram_activates\tdram_precharges\n'
    printf '%s\t%s\t%s\n' "$dram_reads" "$dram_activates" "$dram_precharges"
} > "$out/dram_commands.tsv"
touch "$out/xrage_checkpoint_recovery.pass"
echo "PASS recovered XRAGE $arm: hash=$hash roi_simTicks=$roi_ticks"
