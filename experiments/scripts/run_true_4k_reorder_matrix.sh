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
checkpoint="$out/shared_checkpoint"
selector="$out/shared_treatment.txt"
LD_LIBRARY_PATH="$out/input${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$gem5" --listener-mode=off --outdir="$checkpoint" "$config" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1 \
    --cmd "$workload" --options "deferred $selector" \
    > "$out/shared-checkpoint.log" 2>&1
grep -Fqx \
    'VIRTUAL_TILE_CONSUMER_LAYOUT mode=deferred page_elements=0 logical_elements=16384 mem_size=2147483648' \
    "$out/shared-checkpoint.log"
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
    "$out/shared-checkpoint.log") -eq 1 ]]

common=(
    DX100_SHARED_CHECKPOINT_DIR="$checkpoint"
    DX100_SHARED_TREATMENT_FILE="$selector"
    DX100_FROZEN_RAMULATOR_LIBRARY="$ramulator"
    DX100_RAMULATOR_PROVENANCE_FILE="$out/input/ramulator.sha256"
    MAA_DEBUG_FLAGS=MAAVirtualTrace
)
run_arm() {
    local label=$1
    local case_name=$2
    shift 2
    env "${common[@]}" "$@" \
        "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" \
        "$gem5" "$workload" "$case_name" "$out/$label" \
        > "$out/$label.launch.log" 2>&1
}

run_arm native16 native_direct_16k \
    MAA_ROW_TABLE_SLICES=16 MAA_ROW_TABLE_ROWS_PER_SLICE=64 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=16384 MAA_OFFSET_TABLE_EPOCH_ENTRIES=16384
run_arm native4 native_direct_4k \
    MAA_ROW_TABLE_SLICES=16 MAA_ROW_TABLE_ROWS_PER_SLICE=32 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=4096 MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096
run_arm physical_grow_4k paged_4k \
    MAA_ROW_TABLE_SLICES=16 MAA_ROW_TABLE_ROWS_PER_SLICE=32 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=4096 MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096 \
    MAA_VIRTUAL_INDEX_PARTITIONS=64 MAA_VIRTUAL_INDEX_RANGE_PASSES=1 \
    MAA_VIRTUAL_INDEX_RANGE_POLICY=3 MAA_VIRTUAL_INDEX_FORCE_CACHE=1 \
    MAA_VIRTUAL_PARTITION_KEEP_COMBINER=1 MAA_VIRTUAL_GROW_ORDER=1 \
    MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE=16 \
    MAA_REQUIRE_INDEX_FILTER_WAIT=1

field() {
    local name=$1
    local file=$2
    awk -F '\t' -v name="$name" '
        NR == 1 { for (i = 1; i <= NF; ++i) column[$i] = i; next }
        NR == 2 { print $column[name] }
    ' "$file"
}
reference_hash=$(field output_hash "$out/native16/result.tsv")
printf 'arm\toutput_hash\tsimTicks\ta_line_requests\trow_groups' \
    > "$out/matrix.tsv"
printf '\trow_drains\toffset_drains\tdram_activates\tsummary_bytes' \
    >> "$out/matrix.tsv"
printf '\treplay_bytes\treplay_passes\treplay_drains\tmax_epoch' \
    >> "$out/matrix.tsv"
printf '\tword_entries\toffset_entries\trow_directory_entries' \
    >> "$out/matrix.tsv"
printf '\trow_line_entries\treorder_metadata_bytes\n' >> "$out/matrix.tsv"
for arm in native16 native4 physical_grow_4k; do
    result="$out/$arm/result.tsv"
    hash=$(field output_hash "$result")
    [[ $hash == "$reference_hash" ]]
    ticks=$(field simTicks "$result")
    lines=$(field row_table_unique_cache_lines "$result")
    groups=$(field row_table_rows_inserted "$result")
    row_drains=$(field row_table_full_events "$result")
    offset_drains=$(field offset_epoch_drains "$result")
    activates=$(field dram_activates "$result")
    summary_words=$(field bounded_summary_words "$result")
    passes=$(field bounded_replay_passes "$result")
    replay_drains=$(field bounded_replay_drains "$result")
    max_epoch=$(field bounded_replay_max_epoch_admissions "$result")
    replay_words=$(field bounded_replay_words "$result")
    word_entries=$(field bounded_word_entries "$result")
    offset_entries=$(field bounded_offset_entries "$result")
    row_directories=$(field bounded_row_directory_entries "$result")
    row_lines=$(field bounded_row_line_entries "$result")
    metadata_bytes=$(field bounded_reorder_metadata_bytes "$result")
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' \
        "$arm" "$hash" "$ticks" "$lines" "$groups" "$row_drains" \
        "$offset_drains" "$activates" "$((summary_words * 4))" \
        "$((replay_words * 4))" "$passes" "$replay_drains" \
        >> "$out/matrix.tsv"
    printf '\t%s\t%s\t%s\t%s\t%s\t%s\n' "$max_epoch" "$word_entries" \
        "$offset_entries" "$row_directories" "$row_lines" \
        "$metadata_bytes" >> "$out/matrix.tsv"
done
[[ $(field bounded_word_entries \
        "$out/physical_grow_4k/result.tsv") -le 4096 ]]
[[ $(field bounded_offset_entries \
        "$out/physical_grow_4k/result.tsv") -le 4096 ]]
[[ $(field bounded_row_line_entries \
        "$out/physical_grow_4k/result.tsv") -le 4096 ]]
touch "$out/matrix.complete"
cat "$out/matrix.tsv"
