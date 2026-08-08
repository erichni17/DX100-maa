#!/usr/bin/env bash
# Run one exact XRAGE smoke and, by default, its matched bounded-4K arm.
set -euo pipefail

if [[ $# -ne 5 ]]; then
    echo "usage: $0 OUTDIR GEM5_BIN XRAGE_VERIFY_BIN INPUT_JSON RAMULATOR_LIB" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5_source=$(realpath "$2")
workload_source=$(realpath "$3")
input_source=$(realpath "$4")
ramulator_source=$(realpath "$5")
run_mode=${XRAGE_RUN_MODE:-pair}

case "$run_mode" in
    smoke|pair) ;;
    *)
        echo "XRAGE_RUN_MODE must be smoke or pair" >&2
        exit 2
        ;;
esac
[[ -x $gem5_source && -x $workload_source && -f $input_source &&
   -f $ramulator_source ]] || {
    echo "missing gem5, XRAGE verifier, input, or Ramulator library" >&2
    exit 2
}
[[ ! -e $out ]] || {
    echo "refusing to overwrite evidence root: $out" >&2
    exit 2
}
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing XRAGE evidence run from a dirty worktree" >&2
    git -C "$root" status --short >&2
    exit 1
}

mkdir -p "$out/input"
status="$out/campaign.exit"
trap 'rc=$?; printf "%s\n" "$rc" > "$status"' EXIT

cp --reflink=auto "$gem5_source" "$out/input/gem5.opt"
cp --reflink=auto "$workload_source" "$out/input/xrage_verify"
cp --reflink=auto "$input_source" "$out/input/xrage.json"
cp --reflink=auto "$ramulator_source" "$out/input/libramulator.so"
chmod 0555 "$out/input/gem5.opt" "$out/input/xrage_verify"

gem5="$out/input/gem5.opt"
workload="$out/input/xrage_verify"
input="$out/input/xrage.json"
ramulator="$out/input/libramulator.so"
source_commit=$(git -C "$root" rev-parse HEAD)
library_path="$out/input${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

git -C "$root" status --short > "$out/input/source_status.txt"
git -C "$root" rev-parse HEAD > "$out/input/source_commit"
sha256sum "$gem5" "$workload" "$input" "$ramulator" \
    > "$out/input/artifact_sha256.txt"
LD_LIBRARY_PATH="$library_path" ldd "$gem5" > "$out/input/gem5.ldd.txt"
loaded_ramulator=$(awk '$1 == "libramulator.so" { print $3 }' \
    "$out/input/gem5.ldd.txt")
[[ -n $loaded_ramulator && $(realpath "$loaded_ramulator") == \
    "$ramulator" ]] || {
    echo "frozen gem5 did not resolve the frozen Ramulator library" >&2
    exit 1
}

{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'run_mode=%s\n' "$run_mode"
    printf 'workload=xrage_gather0\n'
    printf 'correctness=exact_integer_output_hash\n'
    printf 'logical_tile_elements=16384\n'
    printf 'physical_tile_elements=4096\n'
    printf 'shared_checkpoint=pre_maa_atomic\n'
    printf 'full_metadata=row_16x64_offset_16384_epoch_16384\n'
    printf 'bounded_metadata=row_16x32_offset_4096_epoch_4096\n'
    printf 'bounded_schedule=four_modulo_passes_finite_filter_16_words_per_cycle\n'
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$out/manifest.txt"
sha256sum \
    "$root/experiments/scripts/run_xrage_virtual_case.sh" \
    "$root/experiments/scripts/run_xrage_direct_index_smoke.sh" \
    "$root/experiments/scripts/recover_xrage_checkpoint.sh" \
    "$root/experiments/scripts/verify_xrage_checkpoint_attestation.py" \
    "$root/configs/deprecated/example/se.py" \
    "$root/ext/ramulator2/ramulator2/example_gem5_config.yaml" \
    > "$out/runner_sha256.txt"

common=(
    XRAGE_SIMULATOR_SOURCE_COMMIT="$source_commit"
    XRAGE_ARM=direct_index_4k
    XRAGE_GUEST_ARM=direct4
    MAA_PHYSICAL_TILE_ELEMENTS=4096
    MAA_VIRTUAL_GROW_ORDER=1
    MAA_VIRTUAL_INDEX_FORCE_CACHE=1
    MAA_VIRTUAL_INDEX_BUFFER_LINES=128
    MAA_NUM_INITIAL_ROW_TABLE_SLICES=16
    MAA_NUM_INDIRECT_UNITS_PER_MAA=1
    MAA_RETIREMENT_CACHE_SIZE=1kB
)

# The first exact verifier restore is both the smoke and the full-metadata arm.
env LD_LIBRARY_PATH="$library_path" "${common[@]}" \
    MAA_ROW_TABLE_ROWS_PER_SLICE=64 \
    MAA_NUM_OFFSET_TABLE_ENTRIES=16384 \
    MAA_NUM_OFFSET_TABLE_EPOCH_ENTRIES=16384 \
    MAA_VIRTUAL_INDEX_PARTITIONS=1 \
    MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE=0 \
    MAA_VIRTUAL_PARTITION_KEEP_COMBINER=0 \
    "$root/experiments/scripts/run_xrage_direct_index_smoke.sh" \
    "$gem5" "$workload" "$input" "$out/full_metadata" \
    > "$out/full_metadata.launch.log" 2>&1
printf '0\n' > "$out/full_metadata.launch.exit"

checkpoint_command=$(<"$out/full_metadata/checkpoint.command")
[[ $checkpoint_command == *"--cpu-type AtomicSimpleCPU"* &&
   $checkpoint_command != *"--maa"* &&
   $checkpoint_command != *"--maa_num_"* &&
   $checkpoint_command != *"--maa_physical_"* ]] || {
    echo "XRAGE checkpoint is not treatment-neutral" >&2
    exit 1
}
(
    cd "$out/full_metadata/checkpoint"
    find . -type f -print0 | sort -z | xargs -0 sha256sum
) > "$out/checkpoint_files.pre_treatment.sha256"
sha256sum "$out/checkpoint_files.pre_treatment.sha256" \
    > "$out/checkpoint_identity.sha256"

if [[ $run_mode == pair ]]; then
    # Retarget the pre-MAA checkpoint to the bounded metadata/schedule arm.
    env LD_LIBRARY_PATH="$library_path" "${common[@]}" \
        XRAGE_ALLOW_PRE_MAA_RETARGET=1 \
        MAA_ROW_TABLE_ROWS_PER_SLICE=32 \
        MAA_NUM_OFFSET_TABLE_ENTRIES=4096 \
        MAA_NUM_OFFSET_TABLE_EPOCH_ENTRIES=4096 \
        MAA_VIRTUAL_INDEX_PARTITIONS=4 \
        MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE=16 \
        MAA_VIRTUAL_PARTITION_KEEP_COMBINER=1 \
        "$root/experiments/scripts/recover_xrage_checkpoint.sh" \
        "$gem5" "$workload" "$input" "$out/full_metadata" \
        "$out/bounded_4k" > "$out/bounded_4k.launch.log" 2>&1
    printf '0\n' > "$out/bounded_4k.launch.exit"
fi

field_value() {
    local result=$1
    local wanted=$2
    awk -F '\t' -v wanted="$wanted" '
        NR == 1 {
            for (field = 1; field <= NF; field++)
                if ($field == wanted) column = field
            next
        }
        NR == 2 && column { print $column }
    ' "$result"
}

full_hash=$(field_value "$out/full_metadata/result.tsv" output_hash)
full_ticks=$(field_value "$out/full_metadata/result.tsv" roi_simTicks)
[[ -n $full_hash && $full_ticks -gt 0 ]] || {
    echo "full-metadata XRAGE result is incomplete" >&2
    exit 1
}

printf 'arm\toutput_hash\troi_simTicks\tdelta_vs_full_pct\n' \
    > "$out/matrix.tsv"
printf 'full_metadata\t%s\t%s\t0.000000\n' "$full_hash" "$full_ticks" \
    >> "$out/matrix.tsv"

if [[ $run_mode == pair ]]; then
    bounded_hash=$(field_value "$out/bounded_4k/result.tsv" output_hash)
    bounded_ticks=$(field_value "$out/bounded_4k/result.tsv" roi_simTicks)
    [[ $bounded_hash == "$full_hash" && $bounded_ticks -gt 0 ]] || {
        echo "bounded XRAGE output differs from the exact full-metadata arm" >&2
        exit 1
    }
    grep -Fqx 'checkpoint_retargeted=1' \
        "$out/bounded_4k/manifest.txt" || {
        echo "bounded arm did not attest checkpoint retargeting" >&2
        exit 1
    }
    bounded_delta=$(awk -v value="$bounded_ticks" -v reference="$full_ticks" \
        'BEGIN { printf "%.6f", 100.0 * (value / reference - 1.0) }')
    printf 'bounded_4k\t%s\t%s\t%s\n' \
        "$bounded_hash" "$bounded_ticks" "$bounded_delta" \
        >> "$out/matrix.tsv"
    touch "$out/matrix.complete"
else
    touch "$out/smoke.complete"
fi

evidence_paths=("$out/input" "$out/full_metadata")
if [[ $run_mode == pair ]]; then
    evidence_paths+=("$out/bounded_4k")
fi
find "${evidence_paths[@]}" -type f \
    ! -name '*.dot' ! -name '*.dot.pdf' ! -name '*.dot.svg' -print0 2>/dev/null |
    sort -z | xargs -0 sha256sum > "$out/evidence_sha256.txt"
cat "$out/matrix.tsv"
