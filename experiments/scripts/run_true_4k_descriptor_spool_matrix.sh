#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
    echo "usage: $0 OUTDIR RESIDENT_GEM5 AB_REFERENCE_GEM5 WORKLOAD_BIN RAMULATOR_LIB" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
resident_gem5_source=$(realpath "$2")
ab_gem5_source=$(realpath "$3")
workload_source=$(realpath "$4")
ramulator_source=$(realpath "$5")
[[ ! -e $out ]] || { echo "refusing to overwrite $out" >&2; exit 2; }
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence run from a dirty worktree" >&2
    exit 1
}

canonical_ramulator_sha=76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753
canonical_ab_gem5_sha=328a38f70b759ccf9585a60bae3aa6e5a5c77c1f0f1ebfb013cbd068a43d1056
canonical_workload_sha=96d274918b1164ed692f452d78761ea96f79c117d35176fb2df0e62453c3e066
accepted_ab_commit=59ad3fbbfc8ba7e3dda9e1be30d3d31f00dba9c5
ramulator_sha=$(sha256sum "$ramulator_source" | awk '{ print $1 }')
ab_gem5_sha=$(sha256sum "$ab_gem5_source" | awk '{ print $1 }')
workload_sha=$(sha256sum "$workload_source" | awk '{ print $1 }')
[[ $ramulator_sha == $canonical_ramulator_sha ]] || {
    echo "Ramulator SHA-256 is not canonical: $ramulator_sha" >&2
    exit 1
}
[[ $ab_gem5_sha == $canonical_ab_gem5_sha ]] || {
    echo "ab reference gem5 is not the accepted 59ad3fbb binary: $ab_gem5_sha" >&2
    exit 1
}
[[ $workload_sha == $canonical_workload_sha ]] || {
    echo "workload is not the accepted matched input: $workload_sha" >&2
    exit 1
}

mkdir -p "$out/input"
trap 'rc=$?; printf "%s\n" "$rc" > "$out/matrix.exit"' EXIT
cp --reflink=auto "$resident_gem5_source" "$out/input/resident.gem5.opt"
cp --reflink=auto "$ab_gem5_source" "$out/input/ab-reference.gem5.opt"
cp --reflink=auto "$workload_source" "$out/input/workload"
cp --reflink=auto "$ramulator_source" "$out/input/libramulator.so"
chmod 0555 "$out/input/resident.gem5.opt" \
    "$out/input/ab-reference.gem5.opt" "$out/input/workload"
resident_gem5="$out/input/resident.gem5.opt"
ab_gem5="$out/input/ab-reference.gem5.opt"
workload="$out/input/workload"
ramulator="$out/input/libramulator.so"
resident_commit=$(git -C "$root" rev-parse HEAD)
printf 'source_commit=%s\nvariant=resident_first\n' "$resident_commit" \
    > "$out/input/resident.provenance.txt"
printf 'source_commit=%s\nvariant=ab_spool_reference\naccepted_matrix=true4k_descriptor_filter_accounting_59ad3fbb\n' \
    "$accepted_ab_commit" > "$out/input/ab-reference.provenance.txt"
sha256sum "$resident_gem5" "$ab_gem5" "$workload" "$ramulator" \
    "$out/input/resident.provenance.txt" \
    "$out/input/ab-reference.provenance.txt" \
    > "$out/input/artifact_sha256.txt"
printf '%s\n' "$resident_commit" > "$out/input/resident.source_commit"
printf '%s\n' "$accepted_ab_commit" > "$out/input/ab-reference.source_commit"
sha256sum "$ramulator" > "$out/input/ramulator.sha256"

config="$root/configs/deprecated/example/se.py"
mkdir -p "$out/checkpoints"
create_checkpoint() {
    local label=$1
    local checkpoint="$out/checkpoints/$label"
    local selector="$out/${label}.treatment.txt"
    local log="$out/checkpoints/${label}.log"
    LD_LIBRARY_PATH="$out/input${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        "$resident_gem5" --listener-mode=off --outdir="$checkpoint" \
        "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
        --max-checkpoints=1 --cmd "$workload" \
        --options "deferred $selector" > "$log" 2>&1
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
for arm in native16 native4 virtual_4k; do
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
    local checkpoint_label=$3
    local selector_label=$4
    local arm_gem5=$5
    local source_commit=$6
    local spool_variant=$7
    local provenance=$8
    shift 8
    env "${common[@]}" \
        DX100_SHARED_CHECKPOINT_DIR="$out/checkpoints/$checkpoint_label" \
        DX100_SHARED_TREATMENT_FILE="$out/${selector_label}.treatment.txt" \
        DX100_SHARED_CHECKPOINT_LOG="$out/checkpoints/${checkpoint_label}.log" \
        DX100_GEM5_SOURCE_COMMIT="$source_commit" \
        DX100_GEM5_PROVENANCE_FILE="$provenance" \
        MAA_DESCRIPTOR_SPOOL_VARIANT="$spool_variant" \
        "$@" \
        "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" \
        "$arm_gem5" "$workload" "$case_name" "$out/$label" \
        > "$out/$label.launch.log" 2>&1
}

arm_pids=()
run_arm native16 native_direct_16k native16 native16 \
    "$resident_gem5" "$resident_commit" resident_first \
    "$out/input/resident.provenance.txt" \
    MAA_ROW_TABLE_SLICES=16 MAA_ROW_TABLE_ROWS_PER_SLICE=64 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=16384 MAA_OFFSET_TABLE_EPOCH_ENTRIES=16384 &
arm_pids+=("$!")
run_arm native4 native_direct_4k native4 native4 \
    "$resident_gem5" "$resident_commit" resident_first \
    "$out/input/resident.provenance.txt" \
    MAA_ROW_TABLE_SLICES=16 MAA_ROW_TABLE_ROWS_PER_SLICE=32 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=4096 MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096 &
arm_pids+=("$!")
run_arm ab_spool_reference_4k paged_4k virtual_4k virtual_4k \
    "$ab_gem5" "$accepted_ab_commit" ab_reference \
    "$out/input/ab-reference.provenance.txt" \
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
run_arm resident_first_4k paged_4k virtual_4k virtual_4k \
    "$resident_gem5" "$resident_commit" resident_first \
    "$out/input/resident.provenance.txt" \
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
    descriptor_spool_filter_grow_retries descriptor_spool_final_flush_stalls
    descriptor_spool_unclassified_write_stalls descriptor_spool_b_scans
    descriptor_spool_resident_populations descriptor_spool_resident_descriptors
    descriptor_spool_external_descriptors descriptor_spool_external_segments
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
for arm in native16 native4 ab_spool_reference_4k resident_first_4k; do
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

reference="$out/ab_spool_reference_4k/result.tsv"
candidate="$out/resident_first_4k/result.tsv"
base_checkpoint=$(cat "$out/ab_spool_reference_4k/checkpoint.path")
candidate_checkpoint=$(cat "$out/resident_first_4k/checkpoint.path")
base_checkpoint_identity=$(awk '{ print $1 }' \
    "$out/ab_spool_reference_4k/shared_checkpoint_identity.sha256")
candidate_checkpoint_identity=$(awk '{ print $1 }' \
    "$out/resident_first_4k/shared_checkpoint_identity.sha256")
[[ $base_checkpoint == "$out/checkpoints/virtual_4k" ]]
[[ $candidate_checkpoint == "$out/checkpoints/virtual_4k" ]]
[[ $base_checkpoint_identity == $candidate_checkpoint_identity ]]
[[ $(sha256sum "$out/virtual_4k.treatment.txt" | awk '{ print $1 }') == \
   $(sha256sum "$out/ab_spool_reference_4k/treatment.txt" | awk '{ print $1 }') ]]
[[ $(field physical_records "$reference") -eq 16384 ]]
[[ $(field physical_records "$candidate") -eq 16384 ]]
[[ $(field physical_record_sha256 "$reference") == \
   $(field physical_record_sha256 "$candidate") ]]
[[ $(field bounded_summary_histogram_sha256 "$reference") == \
   $(field bounded_summary_histogram_sha256 "$candidate") ]]

for result in "$reference" "$candidate"; do
    [[ $(field bounded_summary_words "$result") -eq 16384 ]]
    [[ $(field bounded_bucket_words "$result") -eq 16384 ]]
    [[ $(field bounded_replay_words "$result") -eq 0 ]]
    [[ $(field bounded_replay_passes "$result") -eq 4 ]]
    [[ $(field descriptor_spool_filter_retry_inspections "$result") -eq \
       $(( $(field descriptor_spool_filter_predicate_retries "$result") + \
           $(field descriptor_spool_filter_grow_retries "$result") )) ]]
    [[ $(field index_filter_words "$result") -eq \
       $(( $(field bounded_summary_words "$result") + \
           $(field bounded_bucket_words "$result") + \
           $(field descriptor_spool_filter_retry_inspections "$result") )) ]]
done

[[ $(field descriptor_spool_line_writes "$reference") -eq 2048 ]]
[[ $(field descriptor_spool_write_acks "$reference") -eq 2048 ]]
[[ $(field descriptor_spool_line_reads "$reference") -eq 2048 ]]
[[ $(field descriptor_spool_write_bytes "$reference") -eq 131072 ]]
[[ $(field descriptor_spool_read_bytes "$reference") -eq 131072 ]]
[[ $(field descriptor_spool_backing_bytes "$reference") -eq 131328 ]]
[[ $(field descriptor_spool_staging_entries "$reference") -eq 32 ]]
[[ $(field descriptor_spool_unclassified_write_stalls "$reference") -ge 0 ]]

[[ $(field descriptor_spool_b_scans "$candidate") -eq 2 ]]
[[ $(field descriptor_spool_resident_populations "$candidate") -eq 1 ]]
[[ $(field descriptor_spool_resident_descriptors "$candidate") -eq 4096 ]]
[[ $(field descriptor_spool_external_descriptors "$candidate") -eq 12288 ]]
[[ $(field descriptor_spool_external_segments "$candidate") -eq 3 ]]
[[ $(field descriptor_spool_line_writes "$candidate") -eq 1152 ]]
[[ $(field descriptor_spool_write_acks "$candidate") -eq 1152 ]]
[[ $(field descriptor_spool_line_reads "$candidate") -eq 1152 ]]
[[ $(field descriptor_spool_write_bytes "$candidate") -eq 73728 ]]
[[ $(field descriptor_spool_read_bytes "$candidate") -eq 73728 ]]
[[ $(field descriptor_spool_backing_bytes "$candidate") -eq 73728 ]]
[[ $(field descriptor_spool_staging_entries "$candidate") -eq 35 ]]
[[ $(field descriptor_spool_unclassified_write_stalls "$candidate") -eq 0 ]]
[[ $(field descriptor_spool_write_high_water "$candidate") -le 16 ]]
[[ $(field descriptor_spool_control_bytes "$candidate") -le 4096 ]]
for capacity in bounded_word_entries bounded_offset_entries \
    bounded_row_directory_entries bounded_row_line_entries; do
    [[ $(field "$capacity" "$candidate") -le 4096 ]]
done
for arm in native16 native4 ab_spool_reference_4k resident_first_4k; do
    [[ $(field simTicks "$out/$arm/result.tsv") -gt 0 ]]
    [[ $(field fill_sim_ticks "$out/$arm/result.tsv") -gt 0 ]]
    [[ $(field request_sim_ticks "$out/$arm/result.tsv") -gt 0 ]]
done

resident_gem5_sha=$(sha256sum "$resident_gem5" | awk '{ print $1 }')
config_sha=$(sha256sum "$config" | awk '{ print $1 }')
treatment_sha=$(sha256sum "$out/virtual_4k.treatment.txt" | awk '{ print $1 }')
{
    printf 'arm\tgem5_sha256\tworkload_sha256\tramulator_sha256'
    printf '\tcheckpoint_sha256\ttreatment_sha256\tconfig_sha256'
    printf '\tsource_commit\n'
    printf 'native16\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$resident_gem5_sha" "$workload_sha" "$ramulator_sha" \
        "$(awk '{ print $1 }' "$out/native16/shared_checkpoint_identity.sha256")" \
        "$(sha256sum "$out/native16.treatment.txt" | awk '{ print $1 }')" \
        "$config_sha" "$resident_commit"
    printf 'native4\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$resident_gem5_sha" "$workload_sha" "$ramulator_sha" \
        "$(awk '{ print $1 }' "$out/native4/shared_checkpoint_identity.sha256")" \
        "$(sha256sum "$out/native4.treatment.txt" | awk '{ print $1 }')" \
        "$config_sha" "$resident_commit"
    printf 'ab_spool_reference_4k\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$ab_gem5_sha" "$workload_sha" "$ramulator_sha" \
        "$base_checkpoint_identity" "$treatment_sha" "$config_sha" \
        "$accepted_ab_commit"
    printf 'resident_first_4k\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$resident_gem5_sha" "$workload_sha" "$ramulator_sha" \
        "$candidate_checkpoint_identity" "$treatment_sha" "$config_sha" \
        "$resident_commit"
} > "$out/provenance.tsv"

reference_b_bytes=$((
    ($(field bounded_summary_line_reads "$reference") +
     $(field bounded_bucket_line_reads "$reference")) * 64
))
reference_spool_bytes=$((
    $(field descriptor_spool_write_bytes "$reference") +
    $(field descriptor_spool_read_bytes "$reference")
))
candidate_b_bytes=$((
    ($(field bounded_summary_line_reads "$candidate") +
     $(field bounded_bucket_line_reads "$candidate")) * 64
))
candidate_spool_bytes=$((
    $(field descriptor_spool_write_bytes "$candidate") +
    $(field descriptor_spool_read_bytes "$candidate")
))
reference_total_bytes=$((reference_b_bytes + reference_spool_bytes))
candidate_total_bytes=$((candidate_b_bytes + candidate_spool_bytes))
[[ $candidate_total_bytes -lt $reference_total_bytes ]]
{
    printf 'reference_b_bytes\treference_spool_bytes\treference_total_bytes'
    printf '\tcandidate_b_bytes\tcandidate_spool_bytes'
    printf '\tcandidate_total_bytes\n'
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$reference_b_bytes" \
        "$reference_spool_bytes" "$reference_total_bytes" \
        "$candidate_b_bytes" "$candidate_spool_bytes" \
        "$candidate_total_bytes"
} > "$out/traffic.tsv"

touch "$out/matrix.complete"
cat "$out/matrix.tsv"
