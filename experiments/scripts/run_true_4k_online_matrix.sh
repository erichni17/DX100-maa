#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
    echo "usage: $0 OUTDIR GEM5_BIN WORKLOAD_BIN RAMULATOR_LIB REPLAY_9DDF_GEM5" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5_source=$(realpath "$2")
workload_source=$(realpath "$3")
ramulator_source=$(realpath "$4")
replay_gem5_source=$(realpath "$5")
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
cp --reflink=auto "$replay_gem5_source" "$out/input/gem5.replay9dd.opt"
chmod 0555 "$out/input/gem5.opt" "$out/input/gem5.replay9dd.opt" \
    "$out/input/workload"
gem5="$out/input/gem5.opt"
replay_gem5="$out/input/gem5.replay9dd.opt"
workload="$out/input/workload"
ramulator="$out/input/libramulator.so"
sha256sum "$gem5" "$replay_gem5" "$workload" "$ramulator" > \
    "$out/input/artifact_sha256.txt"
git -C "$root" rev-parse HEAD > "$out/input/source_commit"
git -C "$root" rev-parse 9ddf1ad3 > "$out/input/replay_reference_commit"
current_commit=$(<"$out/input/source_commit")
replay_commit=$(<"$out/input/replay_reference_commit")
replay_expected_sha=64980714a719621fe061aa7d7d3a7f14a4b70950cff1acd63f1a94b175064f1e
replay_actual_sha=$(sha256sum "$replay_gem5" | awk '{print $1}')
[[ $replay_actual_sha == "$replay_expected_sha" ]] || {
    echo "replay gem5 does not match authenticated 9ddf1ad3 artifact" >&2
    exit 1
}
mkdir -p "$out/input/replay_source_snapshot"
git -C "$root" show \
    "$replay_commit:src/mem/MAA/SPD.cc" > \
    "$out/input/replay_source_snapshot/SPD.cc"
git -C "$root" show \
    "$replay_commit:src/mem/MAA/SPD.hh" > \
    "$out/input/replay_source_snapshot/SPD.hh"
cmp -s "$out/input/replay_source_snapshot/SPD.cc" \
    "$root/src/mem/MAA/SPD.cc"
cmp -s "$out/input/replay_source_snapshot/SPD.hh" \
    "$root/src/mem/MAA/SPD.hh"
sha256sum "$out/input/replay_source_snapshot/SPD.cc" \
    "$out/input/replay_source_snapshot/SPD.hh" > \
    "$out/input/replay_source_snapshot/spd_sha256.txt"
sha256sum "$ramulator" > "$out/input/ramulator.sha256"

config="$root/configs/deprecated/example/se.py"
selector="$out/shared_treatment.txt"
checkpoint="$out/shared_checkpoint"
create_checkpoint() {
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
}
create_checkpoint
(
    cd "$checkpoint"
    find . -type f -print0 | sort -z | xargs -0 sha256sum
) > "$out/checkpoint_files.pre_treatment.sha256"
sha256sum "$out/checkpoint_files.pre_treatment.sha256" > \
    "$out/checkpoint_identity.sha256"

common=(
    DX100_SHARED_CHECKPOINT_DIR="$checkpoint"
    DX100_SHARED_TREATMENT_FILE="$selector"
    DX100_SHARED_CHECKPOINT_LOG="$out/shared-checkpoint.log"
    DX100_FROZEN_RAMULATOR_LIBRARY="$ramulator"
    DX100_RAMULATOR_PROVENANCE_FILE="$out/input/ramulator.sha256"
    MAA_DEBUG_FLAGS=MAAVirtualTrace
)
run_arm() {
    local label=$1
    local case_name=$2
    local arm_gem5=$3
    local arm_commit=$4
    shift 4
    env "${common[@]}" DX100_BINARY_SOURCE_COMMIT="$arm_commit" "$@" \
        "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" \
        "$arm_gem5" "$workload" "$case_name" "$out/$label" \
        > "$out/$label.launch.log" 2>&1
    cmp -s "$out/$label/shared_checkpoint_files.sha256" \
        "$out/checkpoint_files.pre_treatment.sha256"
    cmp -s "$out/$label/source_snapshot/SPD.cc" \
        "$root/src/mem/MAA/SPD.cc"
    cmp -s "$out/$label/source_snapshot/SPD.hh" \
        "$root/src/mem/MAA/SPD.hh"
}

# Serial restores are intentional: the checkpointed workload owns one deferred
# treatment-file pathname.  Serial selector updates preserve one byte-identical
# checkpoint lineage without the selector race that concurrent restores create.
run_arm native16 native_direct_16k "$gem5" "$current_commit" \
    MAA_ROW_TABLE_SLICES=16 MAA_ROW_TABLE_ROWS_PER_SLICE=64 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=16384 MAA_OFFSET_TABLE_EPOCH_ENTRIES=16384
run_arm native4 native_direct_4k "$gem5" "$current_commit" \
    MAA_ROW_TABLE_SLICES=16 MAA_ROW_TABLE_ROWS_PER_SLICE=32 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=4096 MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096
run_arm replay9dd paged_4k "$replay_gem5" "$replay_commit" \
    MAA_DEBUG_FLAGS=MAAVirtualTrace,MAAPhysicalRecordTrace \
    MAA_REQUIRE_PHYSICAL_RECORD_TRACE=1 \
    MAA_ROW_TABLE_SLICES=16 MAA_ROW_TABLE_ROWS_PER_SLICE=32 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=4096 MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096 \
    MAA_VIRTUAL_INDEX_PARTITIONS=64 MAA_VIRTUAL_INDEX_RANGE_PASSES=1 \
    MAA_VIRTUAL_INDEX_RANGE_POLICY=3 MAA_VIRTUAL_INDEX_FORCE_CACHE=1 \
    MAA_VIRTUAL_PARTITION_KEEP_COMBINER=1 MAA_VIRTUAL_GROW_ORDER=1 \
    MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE=16 \
    MAA_REQUIRE_INDEX_FILTER_WAIT=1
run_arm online_oldest paged_4k "$gem5" "$current_commit" \
    MAA_DEBUG_FLAGS=MAAVirtualTrace,MAAPhysicalRecordTrace \
    MAA_REQUIRE_PHYSICAL_RECORD_TRACE=1 \
    MAA_ROW_TABLE_SLICES=16 MAA_ROW_TABLE_ROWS_PER_SLICE=32 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=4096 MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096 \
    MAA_VIRTUAL_INDEX_PARTITIONS=1 MAA_VIRTUAL_INDEX_FORCE_CACHE=1 \
    MAA_VIRTUAL_GROW_ORDER=1 MAA_VIRTUAL_ONLINE_ROW_WINDOW=1

field() {
    local name=$1
    local file=$2
    awk -F '\t' -v name="$name" '
        NR == 1 { for (i = 1; i <= NF; ++i) column[$i] = i; next }
        NR == 2 { print $column[name] }
    ' "$file"
}

reference_hash=$(field output_hash "$out/native16/result.tsv")
printf 'arm\tbinary_source_commit\tbinary_sha256\toutput_hash' > \
    "$out/matrix.tsv"
printf '\tphysical_record_sha256\tsimTicks' >> "$out/matrix.tsv"
printf '\tindex_words\tindex_line_reads\ta_line_requests\trow_insertions' >> \
    "$out/matrix.tsv"
printf '\trow_drains\toffset_drains\tdram_reads\tdram_activates' >> \
    "$out/matrix.tsv"
printf '\tonline_victims\tonline_reopens\tonline_max_descriptors' >> \
    "$out/matrix.tsv"
printf '\tonline_max_lines\tonline_max_rows\n' >> "$out/matrix.tsv"
for arm in native16 native4 replay9dd online_oldest; do
    result="$out/$arm/result.tsv"
    hash=$(field output_hash "$result")
    [[ $hash == "$reference_hash" ]]
    arm_binary="$gem5"
    arm_commit="$current_commit"
    if [[ $arm == replay9dd ]]; then
        arm_binary="$replay_gem5"
        arm_commit="$replay_commit"
    fi
    arm_sha=$(sha256sum "$arm_binary" | awk '{print $1}')
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' \
        "$arm" "$arm_commit" "$arm_sha" "$hash" \
        "$(field physical_record_sha256 "$result")" \
        "$(field simTicks "$result")" "$(field index_words "$result")" \
        "$(field index_line_reads "$result")" \
        "$(field row_table_cache_lines "$result")" \
        "$(field row_table_rows_inserted "$result")" \
        "$(field row_table_full_events "$result")" \
        "$(field offset_epoch_drains "$result")" \
        "$(field dram_reads "$result")" "$(field dram_activates "$result")" \
        >> "$out/matrix.tsv"
    printf '\t%s\t%s\t%s\t%s\t%s\n' \
        "$(field online_victims "$result")" \
        "$(field online_reopens "$result")" \
        "$(field online_max_descriptors "$result")" \
        "$(field online_max_lines "$result")" \
        "$(field online_max_rows "$result")" >> "$out/matrix.tsv"
done

[[ $(field index_words "$out/replay9dd/result.tsv") -eq 81920 ]]
[[ $(field index_words "$out/online_oldest/result.tsv") -eq 16384 ]]
[[ $(field online_admissions "$out/online_oldest/result.tsv") -eq 16384 ]]
[[ $(field online_retirements "$out/online_oldest/result.tsv") -eq 16384 ]]
[[ $(field online_max_descriptors "$out/online_oldest/result.tsv") -le 4096 ]]
[[ $(field online_max_lines "$out/online_oldest/result.tsv") -le 4096 ]]
[[ $(field online_max_rows "$out/online_oldest/result.tsv") -le 512 ]]
sha256sum "$root/src/mem/MAA/SPD.cc" "$root/src/mem/MAA/SPD.hh" > \
    "$out/spd_source_sha256.txt"
touch "$out/matrix.complete"
cat "$out/matrix.tsv"
