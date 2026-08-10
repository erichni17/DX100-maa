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
mkdir -p "$out/input" "$out/checkpoint"
trap 'rc=$?; printf "%s\n" "$rc" > "$out/matrix.exit"' EXIT
cp --reflink=auto "$gem5_source" "$out/input/gem5.opt"
cp --reflink=auto "$workload_source" "$out/input/workload"
cp --reflink=auto "$ramulator_source" "$out/input/libramulator.so"
chmod 0555 "$out/input/gem5.opt" "$out/input/workload"
gem5="$out/input/gem5.opt"
workload="$out/input/workload"
ramulator="$out/input/libramulator.so"
source_commit=$(git -C "$root" rev-parse HEAD)
printf 'source_commit=%s\nvariant=bounded_four_run_global_merge\n' \
    "$source_commit" > "$out/input/gem5.provenance.txt"
sha256sum "$ramulator" > "$out/input/ramulator.sha256"
sha256sum "$gem5" "$workload" "$ramulator" \
    "$out/input/gem5.provenance.txt" > "$out/input/artifact_sha256.txt"

config="$root/configs/deprecated/example/se.py"
checkpoint_selector="$out/checkpoint-treatment.txt"
LD_LIBRARY_PATH="$out/input${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" \
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
    --max-checkpoints=1 --cmd "$workload" \
    --options "deferred $checkpoint_selector" \
    > "$out/checkpoint.log" 2>&1
grep -Fqx \
    'VIRTUAL_TILE_CONSUMER_LAYOUT mode=deferred page_elements=0 logical_elements=16384 mem_size=2147483648' \
    "$out/checkpoint.log"
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
        "$out/checkpoint.log") -eq 1 ]]

printf '%s\n' '--maa_virtual_descriptor_spool_source_bypass_cache' \
    > "$out/input/a-source-route.args"

common=(
    DX100_FROZEN_RAMULATOR_LIBRARY="$ramulator"
    DX100_RAMULATOR_PROVENANCE_FILE="$out/input/ramulator.sha256"
    DX100_SHARED_CHECKPOINT_DIR="$out/checkpoint"
    DX100_SHARED_CHECKPOINT_LOG="$out/checkpoint.log"
    DX100_GEM5_SOURCE_COMMIT="$source_commit"
    DX100_GEM5_PROVENANCE_FILE="$out/input/gem5.provenance.txt"
    MAA_DEBUG_FLAGS=MAAVirtualTrace,MAAPhysicalRecordTrace,MAAIssueDigest,MAAReorderTrace
    MAA_REQUIRE_SOURCE_ISSUE_DIGEST=1
    MAA_ROW_TABLE_SLICES=16
    MAA_ROW_TABLE_ROWS_PER_SLICE=32
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8
    MAA_OFFSET_TABLE_ENTRIES=4096
    MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096
)

run_arm() {
    local label=$1
    local case_name=$2
    shift 2
    env "${common[@]}" \
        DX100_SHARED_TREATMENT_FILE="$checkpoint_selector" \
        "$@" \
        "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" \
        "$gem5" "$workload" "$case_name" "$out/$label" \
        > "$out/${label}.launch.log" 2>&1
}

run_arm native4 native_direct_4k
run_arm current_paged4 paged_4k \
    MAA_REQUIRE_PHYSICAL_RECORD_TRACE=1 \
    DX100_EXTRA_MAA_ARGS_FILE="$out/input/a-source-route.args" \
    MAA_VIRTUAL_INDEX_PARTITIONS=64 \
    MAA_VIRTUAL_INDEX_RANGE_PASSES=1 \
    MAA_VIRTUAL_INDEX_RANGE_POLICY=3 \
    MAA_VIRTUAL_INDEX_FORCE_CACHE=1 \
    MAA_VIRTUAL_INDEX_DESCRIPTOR_SPOOL=1 \
    MAA_VIRTUAL_DESCRIPTOR_SPOOL_READ_AHEAD=1 \
    MAA_VIRTUAL_PARTITION_KEEP_COMBINER=1 \
    MAA_VIRTUAL_GROW_ORDER=1 \
    MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE=16 \
    MAA_REQUIRE_INDEX_FILTER_WAIT=1
run_arm candidate paged_4k \
    MAA_REQUIRE_PHYSICAL_RECORD_TRACE=1 \
    DX100_EXTRA_MAA_ARGS_FILE="$out/input/a-source-route.args" \
    MAA_VIRTUAL_INDEX_PARTITIONS=64 \
    MAA_VIRTUAL_INDEX_RANGE_PASSES=1 \
    MAA_VIRTUAL_INDEX_RANGE_POLICY=3 \
    MAA_VIRTUAL_INDEX_FORCE_CACHE=1 \
    MAA_VIRTUAL_INDEX_DESCRIPTOR_SPOOL=1 \
    MAA_VIRTUAL_BOUNDED_GLOBAL_MERGE=1 \
    MAA_VIRTUAL_PARTITION_KEEP_COMBINER=1 \
    MAA_VIRTUAL_GROW_ORDER=1 \
    MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE=16 \
    MAA_REQUIRE_INDEX_FILTER_WAIT=1

python3 "$root/experiments/analysis/analyze_bounded_global_merge.py" \
    "$out" "$out/live_results.json"
touch "$out/matrix.complete"
cat "$out/summary.tsv"
