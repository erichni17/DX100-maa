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

[[ ! -e $out ]] || {
    echo "refusing to overwrite evidence root: $out" >&2
    exit 2
}
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence run from a dirty worktree" >&2
    git -C "$root" status --short >&2
    exit 1
}

mkdir -p "$out/input"
status="$out/matrix.exit"
trap 'rc=$?; printf "%s\n" "$rc" > "$status"' EXIT

cp --reflink=auto "$gem5_source" "$out/input/gem5.opt"
cp --reflink=auto "$workload_source" "$out/input/workload"
cp --reflink=auto "$ramulator_source" "$out/input/libramulator.so"
chmod 0555 "$out/input/gem5.opt" "$out/input/workload"

gem5="$out/input/gem5.opt"
workload="$out/input/workload"
ramulator="$out/input/libramulator.so"
provenance="$out/input/ramulator.sha256"
config="$root/configs/deprecated/example/se.py"
selector="$out/shared_treatment.txt"
checkpoint="$out/shared_checkpoint"

sha256sum "$ramulator" > "$provenance"
git -C "$root" rev-parse HEAD > "$out/input/source_commit"
sha256sum "$gem5" "$workload" "$ramulator" "$provenance" \
    > "$out/input/artifact_sha256.txt"
LD_LIBRARY_PATH="$out/input${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    ldd "$gem5" > "$out/input/gem5.ldd.txt"
loaded_ramulator=$(awk '$1 == "libramulator.so" { print $3 }' \
    "$out/input/gem5.ldd.txt")
[[ -n $loaded_ramulator && $(realpath "$loaded_ramulator") == \
    "$ramulator" ]] || {
    echo "frozen gem5 did not resolve the frozen Ramulator library" >&2
    exit 1
}

[[ ! -e $selector ]]
set +e
LD_LIBRARY_PATH="$out/input${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    /usr/bin/time -f 'checkpoint_wall=%e checkpoint_rss_kb=%M' \
    "$gem5" --listener-mode=off --outdir="$checkpoint" \
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
    --max-checkpoints=1 --cmd "$workload" \
    --options "deferred $selector" > "$out/shared-checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/shared-checkpoint.exit"
[[ $checkpoint_rc -eq 0 && ! -e $selector ]] || {
    echo "treatment-neutral shared checkpoint failed" >&2
    exit 1
}
grep -Fqx \
    'VIRTUAL_TILE_CONSUMER_LAYOUT mode=deferred page_elements=0 logical_elements=16384 mem_size=2147483648' \
    "$out/shared-checkpoint.log"
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
    "$out/shared-checkpoint.log") -eq 1 ]]
(
    cd "$checkpoint"
    find . -type f -print0 | sort -z | xargs -0 sha256sum
) > "$out/checkpoint_files.pre_treatment.sha256"
sha256sum "$out/checkpoint_files.pre_treatment.sha256" \
    > "$out/checkpoint_identity.sha256"

common=(
    DX100_SHARED_CHECKPOINT_DIR="$checkpoint"
    DX100_SHARED_TREATMENT_FILE="$selector"
    DX100_FROZEN_RAMULATOR_LIBRARY="$ramulator"
    DX100_RAMULATOR_PROVENANCE_FILE="$provenance"
    MAA_DEBUG_FLAGS=MAAVirtualTrace
    MAA_VIRTUAL_GROW_ORDER=1
    MAA_VIRTUAL_INDEX_FORCE_CACHE=1
    MAA_VIRTUAL_PARTITION_KEEP_COMBINER=1
    MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE=16
)

run_arm() {
    local label=$1
    shift
    env "${common[@]}" "$@" \
        "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" \
        "$gem5" "$workload" paged_4k "$out/$label"
    cmp -s "$out/$label/shared_checkpoint_files.sha256" \
        "$out/checkpoint_files.pre_treatment.sha256"
}

# Established hybrid: 4K payload pages, existing row metadata, 16K OffsetTable.
run_arm hybrid_full_metadata \
    MAA_ROW_TABLE_SLICES=16 \
    MAA_ROW_TABLE_ROWS_PER_SLICE=64 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=16384 \
    MAA_OFFSET_TABLE_EPOCH_ENTRIES=16384 \
    MAA_VIRTUAL_INDEX_PARTITIONS=1

# Same 4K metadata bounds, but assign rows to four passes by grow-address modulo.
run_arm bounded_modulo_4k \
    MAA_ROW_TABLE_SLICES=16 \
    MAA_ROW_TABLE_ROWS_PER_SLICE=32 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=4096 \
    MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096 \
    MAA_VIRTUAL_INDEX_PARTITIONS=4 \
    MAA_REQUIRE_INDEX_FILTER_WAIT=1

# Collaborative range-pass candidate: rescan cached B values for four fixed ranges.
run_arm bounded_range_4k \
    MAA_ROW_TABLE_SLICES=16 \
    MAA_ROW_TABLE_ROWS_PER_SLICE=32 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=4096 \
    MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096 \
    MAA_VIRTUAL_INDEX_PARTITIONS=4 \
    MAA_VIRTUAL_INDEX_RANGE_PASSES=1 \
    MAA_REQUIRE_INDEX_FILTER_WAIT=1

# Same range-pass mechanism, but bound ranges to the instruction's A interval.
run_arm bounded_source_range_4k \
    MAA_ROW_TABLE_SLICES=16 \
    MAA_ROW_TABLE_ROWS_PER_SLICE=32 \
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8 \
    MAA_OFFSET_TABLE_ENTRIES=4096 \
    MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096 \
    MAA_VIRTUAL_INDEX_PARTITIONS=4 \
    MAA_VIRTUAL_INDEX_RANGE_PASSES=1 \
    MAA_VIRTUAL_INDEX_RANGE_POLICY=1 \
    MAA_REQUIRE_INDEX_FILTER_WAIT=1

reference_hash=$(awk -F '\t' 'NR == 2 { print $2 }' \
    "$out/hybrid_full_metadata/result.tsv")
[[ -n $reference_hash ]]
arms=(hybrid_full_metadata bounded_modulo_4k bounded_range_4k
      bounded_source_range_4k)
for arm in "${arms[@]}"; do
    hash=$(awk -F '\t' 'NR == 2 { print $2 }' "$out/$arm/result.tsv")
    [[ $hash == "$reference_hash" ]]
    [[ $(<"$out/$arm/restore.exit") == 0 ]]
done

printf 'arm\toutput_hash\tsimTicks\tdelta_vs_hybrid_pct\n' > "$out/matrix.tsv"
reference_ticks=$(awk -F '\t' 'NR == 2 { print $3 }' \
    "$out/hybrid_full_metadata/result.tsv")
for arm in "${arms[@]}"; do
    read -r hash ticks < <(awk -F '\t' 'NR == 2 { print $2, $3 }' \
        "$out/$arm/result.tsv")
    delta=$(awk -v value="$ticks" -v reference="$reference_ticks" \
        'BEGIN { printf "%.6f", 100.0 * (value / reference - 1.0) }')
    printf '%s\t%s\t%s\t%s\n' "$arm" "$hash" "$ticks" "$delta" \
        >> "$out/matrix.tsv"
done

touch "$out/matrix.complete"
cat "$out/matrix.tsv"
