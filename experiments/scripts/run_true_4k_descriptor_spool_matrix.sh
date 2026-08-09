#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "usage: $0 OUTDIR GEM5_BIN WORKLOAD_BIN RAMULATOR_LIB" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5_source=$(realpath "$2")
workload_source=$(realpath "$3")
ramulator_source=$(realpath "$4")
[[ ! -e $out ]] || { echo "refusing to overwrite $out" >&2; exit 2; }
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence run from a dirty worktree" >&2
    exit 1
}
canonical_ramulator_sha=76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753
ramulator_sha=$(sha256sum "$ramulator_source" | awk '{ print $1 }')
[[ $ramulator_sha == $canonical_ramulator_sha ]] || {
    echo "Ramulator SHA-256 is not canonical: $ramulator_sha" >&2
    exit 1
}

mkdir -p "$out/input"
trap 'rc=$?; printf "%s\n" "$rc" > "$out/matrix.exit"' EXIT
cp --reflink=auto "$gem5_source" "$out/input/gem5.opt"
cp --reflink=auto "$workload_source" "$out/input/workload"
cp --reflink=auto "$ramulator_source" "$out/input/libramulator.so"
chmod 0555 "$out/input/gem5.opt" "$out/input/workload"
gem5="$out/input/gem5.opt"
workload="$out/input/workload"
ramulator="$out/input/libramulator.so"
sha256sum "$gem5" "$workload" "$ramulator" > \
    "$out/input/artifact_sha256.txt"
git -C "$root" rev-parse HEAD > "$out/input/source_commit"
sha256sum "$ramulator" > "$out/input/ramulator.sha256"

config="$root/configs/deprecated/example/se.py"
mkdir -p "$out/checkpoints"
create_checkpoint() {
    local label=$1
    local checkpoint="$out/checkpoints/$label"
    local selector="$out/${label}.treatment.txt"
    local log="$out/checkpoints/${label}.log"
    LD_LIBRARY_PATH="$out/input${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        "$gem5" --listener-mode=off --outdir="$checkpoint" "$config" \
        --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1 \
        --cmd "$workload" --options "deferred $selector" \
        > "$log" 2>&1
    grep -Fqx \
        'VIRTUAL_TILE_CONSUMER_LAYOUT mode=deferred page_elements=0 logical_elements=16384 mem_size=2147483648' \
        "$log"
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' "$log") -eq 1 ]]
}
wait_all() {
    local rc=0
    local pid
    for pid in "$@"; do
        wait "$pid" || rc=1
    done
    return "$rc"
}

checkpoint_pids=()
for arm in native16 native4 base_replay_4k descriptor_spool_4k; do
    create_checkpoint "$arm" &
    checkpoint_pids+=("$!")
done
wait_all "${checkpoint_pids[@]}"

common=(
    DX100_FROZEN_RAMULATOR_LIBRARY="$ramulator"
    DX100_RAMULATOR_PROVENANCE_FILE="$out/input/ramulator.sha256"
    MAA_DEBUG_FLAGS=MAAVirtualTrace
)
run_arm() {
    local label=$1
    local case_name=$2
    shift 2
    env "${common[@]}" \
        DX100_SHARED_CHECKPOINT_DIR="$out/checkpoints/$label" \
        DX100_SHARED_TREATMENT_FILE="$out/${label}.treatment.txt" \
        DX100_SHARED_CHECKPOINT_LOG="$out/checkpoints/${label}.log" \
        "$@" \
        "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" \
        "$gem5" "$workload" "$case_name" "$out/$label" \
        > "$out/$label.launch.log" 2>&1
}

arm_pids=()
run_arm native16 native_direct_16k \
    MAA_ROW_TABLE_SLICES=16 MAA_ROW_TABLE_ROWS_PER_SLICE=64 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=16384 MAA_OFFSET_TABLE_EPOCH_ENTRIES=16384 &
arm_pids+=("$!")
run_arm native4 native_direct_4k \
    MAA_ROW_TABLE_SLICES=16 MAA_ROW_TABLE_ROWS_PER_SLICE=32 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=4096 MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096 &
arm_pids+=("$!")
run_arm base_replay_4k paged_4k \
    MAA_DEBUG_FLAGS=MAAVirtualTrace,MAAPhysicalRecordTrace \
    MAA_REQUIRE_PHYSICAL_RECORD_TRACE=1 \
    MAA_ROW_TABLE_SLICES=16 MAA_ROW_TABLE_ROWS_PER_SLICE=32 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=4096 MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096 \
    MAA_VIRTUAL_INDEX_PARTITIONS=64 MAA_VIRTUAL_INDEX_RANGE_PASSES=1 \
    MAA_VIRTUAL_INDEX_RANGE_POLICY=3 MAA_VIRTUAL_INDEX_FORCE_CACHE=1 \
    MAA_VIRTUAL_PARTITION_KEEP_COMBINER=1 MAA_VIRTUAL_GROW_ORDER=1 \
    MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE=16 \
    MAA_REQUIRE_INDEX_FILTER_WAIT=1 &
arm_pids+=("$!")
run_arm descriptor_spool_4k paged_4k \
    MAA_DEBUG_FLAGS=MAAVirtualTrace,MAAPhysicalRecordTrace \
    MAA_REQUIRE_PHYSICAL_RECORD_TRACE=1 \
    MAA_ROW_TABLE_SLICES=16 MAA_ROW_TABLE_ROWS_PER_SLICE=32 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=4096 MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096 \
    MAA_VIRTUAL_INDEX_PARTITIONS=64 MAA_VIRTUAL_INDEX_RANGE_PASSES=1 \
    MAA_VIRTUAL_INDEX_RANGE_POLICY=3 MAA_VIRTUAL_INDEX_FORCE_CACHE=1 \
    MAA_VIRTUAL_INDEX_DESCRIPTOR_SPOOL=1 \
    MAA_VIRTUAL_PARTITION_KEEP_COMBINER=1 MAA_VIRTUAL_GROW_ORDER=1 \
    MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE=16 \
    MAA_REQUIRE_INDEX_FILTER_WAIT=1 &
arm_pids+=("$!")
wait_all "${arm_pids[@]}"

field() {
    local name=$1
    local file=$2
    awk -F '\t' -v name="$name" '
        NR == 1 { for (i = 1; i <= NF; ++i) column[$i] = i; next }
        NR == 2 { if (!(name in column)) exit 2; print $column[name] }
    ' "$file"
}

fields=(
    output_hash physical_records physical_record_sha256
    bounded_summary_histogram_sha256 simTicks fill_sim_ticks
    request_sim_ticks fill_cycles request_cycles index_line_reads index_words
    index_filter_words descriptor_spool_filter_retry_inspections
    descriptor_spool_filter_predicate_retries
    descriptor_spool_filter_grow_retries
    bounded_summary_line_reads bounded_summary_words bounded_bucket_line_reads
    bounded_bucket_words bounded_replay_line_reads bounded_replay_words
    bounded_replay_passes descriptor_spool_line_writes
    descriptor_spool_write_bytes descriptor_spool_write_acks
    descriptor_spool_line_reads descriptor_spool_read_bytes
    descriptor_spool_write_credit_stalls descriptor_spool_read_credit_stalls
    descriptor_spool_write_high_water descriptor_spool_staging_entries
    descriptor_spool_control_bytes descriptor_spool_backing_bytes
    row_table_cache_lines row_table_unique_cache_lines row_table_rows_inserted
    row_table_unique_rows source_reads row_table_full_events
    offset_epoch_drains bounded_replay_max_epoch_admissions
    bounded_word_entries bounded_offset_entries bounded_row_directory_entries
    bounded_row_line_entries bounded_reorder_metadata_bytes write_issues
    write_completions l3_read_hits_maa l3_read_misses_maa
    l3_write_requests_maa memory_bytes_read_maa memory_bytes_written_maa
    dram_reads dram_writes dram_activates dram_precharges
)
{
    printf 'arm'
    printf '\t%s' "${fields[@]}"
    printf '\n'
} > "$out/matrix.tsv"
reference_hash=$(field output_hash "$out/native16/result.tsv")
for arm in native16 native4 base_replay_4k descriptor_spool_4k; do
    result="$out/$arm/result.tsv"
    [[ -f $out/$arm/virtual_tile_consumer_case.pass ]]
    [[ $(field output_hash "$result") == "$reference_hash" ]]
    {
        printf '%s' "$arm"
        for name in "${fields[@]}"; do
            printf '\t%s' "$(field "$name" "$result")"
        done
        printf '\n'
    } >> "$out/matrix.tsv"
done

base="$out/base_replay_4k/result.tsv"
candidate="$out/descriptor_spool_4k/result.tsv"
[[ $(field physical_records "$base") -eq 16384 ]]
[[ $(field physical_records "$candidate") -eq 16384 ]]
[[ $(field physical_record_sha256 "$base") == \
   $(field physical_record_sha256 "$candidate") ]]
[[ $(field bounded_summary_histogram_sha256 "$base") == \
   $(field bounded_summary_histogram_sha256 "$candidate") ]]
[[ $(field bounded_summary_words "$base") -eq 16384 ]]
[[ $(field bounded_replay_passes "$base") -eq 4 ]]
[[ $(field bounded_replay_words "$base") -eq 65536 ]]
[[ $(field bounded_bucket_words "$base") -eq 0 ]]
[[ $(field descriptor_spool_line_writes "$base") -eq 0 ]]
[[ $(field bounded_summary_words "$candidate") -eq 16384 ]]
[[ $(field bounded_bucket_words "$candidate") -eq 16384 ]]
[[ $(field descriptor_spool_filter_retry_inspections "$candidate") -eq \
   $(( $(field descriptor_spool_filter_predicate_retries "$candidate") + \
       $(field descriptor_spool_filter_grow_retries "$candidate") )) ]]
[[ $(field index_filter_words "$candidate") -eq \
   $(( $(field bounded_summary_words "$candidate") + \
       $(field bounded_bucket_words "$candidate") + \
       $(field descriptor_spool_filter_retry_inspections "$candidate") )) ]]
[[ $(field bounded_replay_passes "$candidate") -eq 4 ]]
[[ $(field bounded_replay_words "$candidate") -eq 0 ]]
[[ $(field descriptor_spool_line_writes "$candidate") -eq 2048 ]]
[[ $(field descriptor_spool_write_acks "$candidate") -eq 2048 ]]
[[ $(field descriptor_spool_line_reads "$candidate") -eq 2048 ]]
[[ $(field descriptor_spool_write_bytes "$candidate") -eq 131072 ]]
[[ $(field descriptor_spool_read_bytes "$candidate") -eq 131072 ]]
[[ $(field descriptor_spool_backing_bytes "$candidate") -eq 131328 ]]
[[ $(field descriptor_spool_staging_entries "$candidate") -eq 32 ]]
[[ $(field descriptor_spool_write_high_water "$candidate") -le 16 ]]
[[ $(field descriptor_spool_control_bytes "$candidate") -le 4096 ]]
[[ $(field descriptor_spool_filter_retry_inspections "$base") -eq 0 ]]
for capacity in bounded_word_entries bounded_offset_entries \
    bounded_row_directory_entries bounded_row_line_entries; do
    [[ $(field "$capacity" "$candidate") -le 4096 ]]
done
for arm in native16 native4 base_replay_4k descriptor_spool_4k; do
    [[ $(field simTicks "$out/$arm/result.tsv") -gt 0 ]]
    [[ $(field fill_sim_ticks "$out/$arm/result.tsv") -gt 0 ]]
    [[ $(field request_sim_ticks "$out/$arm/result.tsv") -gt 0 ]]
done

base_scan_bytes=$((
    ($(field bounded_summary_line_reads "$base") +
     $(field bounded_replay_line_reads "$base")) * 64
))
candidate_b_bytes=$((
    ($(field bounded_summary_line_reads "$candidate") +
     $(field bounded_bucket_line_reads "$candidate")) * 64
))
candidate_spool_bytes=$((
    $(field descriptor_spool_write_bytes "$candidate") +
    $(field descriptor_spool_read_bytes "$candidate")
))
candidate_total_bytes=$((candidate_b_bytes + candidate_spool_bytes))
naive_16byte_total_bytes=$((candidate_b_bytes + 2 * 262144))
[[ $candidate_total_bytes -lt $naive_16byte_total_bytes ]]
{
    printf 'base_scan_bytes\tcandidate_b_bytes\tcandidate_spool_bytes'
    printf '\tcandidate_total_bytes\tnaive_16byte_total_bytes\n'
    printf '%s\t%s\t%s\t%s\t%s\n' "$base_scan_bytes" \
        "$candidate_b_bytes" "$candidate_spool_bytes" \
        "$candidate_total_bytes" "$naive_16byte_total_bytes"
} > "$out/traffic.tsv"

touch "$out/matrix.complete"
cat "$out/matrix.tsv"
