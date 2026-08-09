#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 8 ]]; then
    echo "usage: $0 OUTDIR GEM5 GEM5_SHA256 SOURCE_COMMIT WORKLOAD WORKLOAD_SHA256 RAMULATOR_LIB RAMULATOR_SHA256" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5_source=$(realpath "$2")
expected_gem5_sha=$3
source_commit=$4
workload_source=$(realpath "$5")
expected_workload_sha=$6
ramulator_source=$(realpath "$7")
expected_ramulator_sha=$8
config="$root/configs/deprecated/example/se.py"
case_runner="$root/experiments/scripts/run_virtual_tile_consumer_case.sh"
validator="$root/experiments/scripts/validate_descriptor_spool_read_ahead.py"
extra_a_args=${DX100_A_SOURCE_ROUTING_ARGS_FILE:-}
base_arms=(native16 native4 resident_control_4k overlap_treatment_4k)

[[ ! -e $out ]] || { echo "refusing to overwrite $out" >&2; exit 2; }
[[ $expected_gem5_sha =~ ^[0-9a-f]{64}$ &&
   $expected_workload_sha =~ ^[0-9a-f]{64}$ &&
   $expected_ramulator_sha =~ ^[0-9a-f]{64}$ ]] || {
    echo "all expected SHA-256 values must be lowercase 64-hex strings" >&2
    exit 2
}
[[ $source_commit =~ ^[0-9a-f]{40}$ ]] || {
    echo "SOURCE_COMMIT must be a full 40-hex commit" >&2
    exit 2
}
git -C "$root" cat-file -e "$source_commit^{commit}"
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence run from a dirty worktree" >&2
    exit 1
}
check_sha() {
    local path=$1
    local expected=$2
    local label=$3
    local actual
    actual=$(sha256sum "$path" | awk '{ print $1 }')
    [[ $actual == "$expected" ]] || {
        echo "$label SHA-256 mismatch: $actual/$expected" >&2
        exit 1
    }
}
check_sha "$gem5_source" "$expected_gem5_sha" gem5
check_sha "$workload_source" "$expected_workload_sha" workload
check_sha "$ramulator_source" "$expected_ramulator_sha" Ramulator

config_rel=configs/deprecated/example/se.py
config_sha=$(sha256sum "$config" | awk '{ print $1 }')
commit_config_sha=$(git -C "$root" show "$source_commit:$config_rel" | \
    sha256sum | awk '{ print $1 }')
[[ $config_sha == "$commit_config_sha" ]] || {
    echo "live se.py does not match simulator source commit" >&2
    exit 1
}

mkdir -p "$out/input" "$out/checkpoints"
trap 'rc=$?; printf "%s\n" "$rc" > "$out/matrix.exit"' EXIT
cp --reflink=auto "$gem5_source" "$out/input/gem5.opt"
cp --reflink=auto "$workload_source" "$out/input/workload"
cp --reflink=auto "$ramulator_source" "$out/input/libramulator.so"
chmod 0555 "$out/input/gem5.opt" "$out/input/workload"
gem5="$out/input/gem5.opt"
workload="$out/input/workload"
ramulator="$out/input/libramulator.so"
git -C "$root" archive --format=tar "$source_commit" -- \
    src/mem/MAA configs/common "$config_rel" \
    > "$out/input/simulator_source.tar"
simulator_source_sha=$(sha256sum "$out/input/simulator_source.tar" | \
    awk '{ print $1 }')
printf '%s  %s\n' "$expected_ramulator_sha" "$ramulator" \
    > "$out/input/ramulator.sha256"
{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'gem5_sha256=%s\n' "$expected_gem5_sha"
    printf 'simulator_source_archive_sha256=%s\n' "$simulator_source_sha"
} > "$out/input/gem5.provenance.txt"
sha256sum "$gem5" "$workload" "$ramulator" "$config" \
    "$case_runner" "$validator" "$out/input/simulator_source.tar" \
    "$out/input/gem5.provenance.txt" > "$out/input/artifact_sha256.txt"

checkpoint_identity() {
    local checkpoint=$1
    local output=$2
    (
        cd "$checkpoint"
        find . -type f -print0 | sort -z | xargs -0 sha256sum
    ) > "$output.files.sha256"
    sha256sum "$output.files.sha256" | awk '{ print $1 }' > "$output"
}
create_checkpoint() {
    local label=$1
    local checkpoint="$out/checkpoints/$label"
    local selector="$out/${label}.treatment.txt"
    local log="$out/checkpoints/${label}.log"
    LD_LIBRARY_PATH="$out/input${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        "$gem5" --listener-mode=off --outdir="$checkpoint" \
        "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
        --max-checkpoints=1 --cmd "$workload" \
        --options "deferred $selector" > "$log" 2>&1
    grep -Fqx \
        'VIRTUAL_TILE_CONSUMER_LAYOUT mode=deferred page_elements=0 logical_elements=16384 mem_size=2147483648' \
        "$log"
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' "$log") -eq 1 ]]
    checkpoint_identity "$checkpoint" "$out/checkpoints/${label}.identity.sha256"
}
for checkpoint in native16 native4 virtual4; do
    create_checkpoint "$checkpoint"
done

common=(
    DX100_FROZEN_RAMULATOR_LIBRARY="$ramulator"
    DX100_RAMULATOR_PROVENANCE_FILE="$out/input/ramulator.sha256"
    DX100_GEM5_SOURCE_COMMIT="$source_commit"
    DX100_GEM5_PROVENANCE_FILE="$out/input/gem5.provenance.txt"
    MAA_DEBUG_FLAGS=MAAVirtualTrace,MAAPhysicalRecordTrace,MAAIssueDigest
    MAA_REQUIRE_SOURCE_ISSUE_DIGEST=1
)
run_arm() {
    local label=$1
    local case_name=$2
    local checkpoint=$3
    local read_ahead=$4
    local require_physical=$5
    shift 5
    env "${common[@]}" \
        DX100_SHARED_CHECKPOINT_DIR="$out/checkpoints/$checkpoint" \
        DX100_SHARED_TREATMENT_FILE="$out/${checkpoint}.treatment.txt" \
        DX100_SHARED_CHECKPOINT_LOG="$out/checkpoints/${checkpoint}.log" \
        MAA_REQUIRE_PHYSICAL_RECORD_TRACE="$require_physical" \
        MAA_DESCRIPTOR_SPOOL_VARIANT=resident_first \
        MAA_VIRTUAL_DESCRIPTOR_SPOOL_READ_AHEAD="$read_ahead" \
        "$@" "$case_runner" "$gem5" "$workload" "$case_name" \
        "$out/$label" > "$out/$label.launch.log" 2>&1
}

native_geometry=(
    MAA_ROW_TABLE_SLICES=16
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8
)
virtual_geometry=(
    MAA_ROW_TABLE_SLICES=16
    MAA_ROW_TABLE_ROWS_PER_SLICE=32
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8
    MAA_OFFSET_TABLE_ENTRIES=4096
    MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096
    MAA_VIRTUAL_INDEX_PARTITIONS=64
    MAA_VIRTUAL_INDEX_RANGE_PASSES=1
    MAA_VIRTUAL_INDEX_RANGE_POLICY=3
    MAA_VIRTUAL_INDEX_FORCE_CACHE=1
    MAA_VIRTUAL_INDEX_DESCRIPTOR_SPOOL=1
    MAA_VIRTUAL_PARTITION_KEEP_COMBINER=1
    MAA_VIRTUAL_GROW_ORDER=1
    MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE=16
    MAA_REQUIRE_INDEX_FILTER_WAIT=1
)
run_arm native16 native_direct_16k native16 0 0 \
    "${native_geometry[@]}" MAA_ROW_TABLE_ROWS_PER_SLICE=64 \
    MAA_OFFSET_TABLE_ENTRIES=16384 MAA_OFFSET_TABLE_EPOCH_ENTRIES=16384
run_arm native4 native_direct_4k native4 0 0 \
    "${native_geometry[@]}" MAA_ROW_TABLE_ROWS_PER_SLICE=32 \
    MAA_OFFSET_TABLE_ENTRIES=4096 MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096
run_arm resident_control_4k paged_4k virtual4 0 1 \
    "${virtual_geometry[@]}"
run_arm overlap_treatment_4k paged_4k virtual4 1 1 \
    "${virtual_geometry[@]}"

arms=("${base_arms[@]}")
if [[ -n $extra_a_args ]]; then
    extra_a_args=$(realpath "$extra_a_args")
    [[ -f $extra_a_args ]] || {
        echo "missing A-source routing args file: $extra_a_args" >&2
        exit 2
    }
    cp -- "$extra_a_args" "$out/input/a_source_routing.args"
    run_arm a_source_routing_4k paged_4k virtual4 1 1 \
        DX100_EXTRA_MAA_ARGS_FILE="$out/input/a_source_routing.args" \
        "${virtual_geometry[@]}"
    arms+=(a_source_routing_4k)
fi

python3 "$validator" --mode control \
    --manifest "$out/resident_control_4k/manifest.txt" \
    --result "$out/resident_control_4k/result.tsv" \
    --trace "$out/resident_control_4k/run/virtual_trace.log" \
    --output-dir "$out/resident_control_4k/read_ahead_validation"
python3 "$validator" --mode treatment \
    --manifest "$out/overlap_treatment_4k/manifest.txt" \
    --result "$out/overlap_treatment_4k/result.tsv" \
    --trace "$out/overlap_treatment_4k/run/virtual_trace.log" \
    --output-dir "$out/overlap_treatment_4k/read_ahead_validation"
if [[ -n $extra_a_args ]]; then
    python3 "$validator" --mode treatment \
        --manifest "$out/a_source_routing_4k/manifest.txt" \
        --result "$out/a_source_routing_4k/result.tsv" \
        --trace "$out/a_source_routing_4k/run/virtual_trace.log" \
        --output-dir "$out/a_source_routing_4k/read_ahead_validation"
fi

field() {
    local name=$1
    local file=$2
    awk -F '\t' -v name="$name" '
        NR == 1 { for (i = 1; i <= NF; ++i) column[$i] = i; next }
        NR == 2 { if (!(name in column)) exit 2; print $column[name] }
    ' "$file"
}
reference_hash=$(field output_hash "$out/native16/result.tsv")
for arm in "${arms[@]}"; do
    [[ -f $out/$arm/virtual_tile_consumer_case.pass ]]
    [[ $(field output_hash "$out/$arm/result.tsv") == "$reference_hash" ]]
    [[ $(field simTicks "$out/$arm/result.tsv") -gt 0 ]]
done
control="$out/resident_control_4k/result.tsv"
treatment="$out/overlap_treatment_4k/result.tsv"
for hash_field in physical_record_sha256 bounded_summary_histogram_sha256 \
    source_issue_sha256; do
    [[ $(field "$hash_field" "$control") == \
       $(field "$hash_field" "$treatment") ]]
done
for traffic_field in source_reads descriptor_spool_line_writes \
    descriptor_spool_write_bytes descriptor_spool_write_acks \
    descriptor_spool_line_reads descriptor_spool_read_bytes; do
    [[ $(field "$traffic_field" "$control") == \
       $(field "$traffic_field" "$treatment") ]]
done
for bounded_field in bounded_word_entries bounded_offset_entries \
    bounded_row_directory_entries bounded_row_line_entries; do
    [[ $(field "$bounded_field" "$control") -le 4096 ]]
    [[ $(field "$bounded_field" "$treatment") -le 4096 ]]
done

checkpoint_identity "$out/checkpoints/virtual4" \
    "$out/checkpoints/virtual4.post.identity.sha256"
cmp -s "$out/checkpoints/virtual4.identity.sha256" \
    "$out/checkpoints/virtual4.post.identity.sha256"

fields=(output_hash simTicks physical_record_sha256 source_issue_sha256
    descriptor_spool_overlap_opportunities
    descriptor_spool_next_pass_read_issues
    descriptor_spool_next_pass_read_responses
    descriptor_spool_useful_prefetched_lines
    descriptor_spool_prefetch_occupancy_high_water
    descriptor_spool_wasted_prefetched_lines)
{
    printf 'arm'
    printf '\t%s' "${fields[@]}"
    printf '\n'
    for arm in "${arms[@]}"; do
        printf '%s' "$arm"
        for name in "${fields[@]}"; do
            printf '\t%s' "$(field "$name" "$out/$arm/result.tsv")"
        done
        printf '\n'
    done
} > "$out/matrix.tsv"

runner_commit=$(git -C "$root" rev-parse HEAD)
runner_sha=$(sha256sum "$0" | awk '{ print $1 }')
{
    printf 'arm\tsource_commit\tgem5_sha256\trunner_commit\trunner_sha256'
    printf '\tconfig_sha256\tresolved_config_sha256\tworkload_sha256'
    printf '\tramulator_sha256\tcheckpoint_sha256\ttreatment_sha256'
    printf '\tsimulator_source_archive_sha256\n'
    for arm in "${arms[@]}"; do
        checkpoint=native16
        [[ $arm == native4 ]] && checkpoint=native4
        [[ $arm == resident_control_4k || $arm == overlap_treatment_4k ||
           $arm == a_source_routing_4k ]] && checkpoint=virtual4
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$arm" "$source_commit" "$expected_gem5_sha" \
            "$runner_commit" "$runner_sha" "$config_sha" \
            "$(sha256sum "$out/$arm/run/config.ini" | awk '{ print $1 }')" \
            "$expected_workload_sha" "$expected_ramulator_sha" \
            "$(<"$out/checkpoints/${checkpoint}.identity.sha256")" \
            "$(sha256sum "$out/$arm/treatment.txt" | awk '{ print $1 }')" \
            "$simulator_source_sha"
    done
} > "$out/provenance.tsv"
sha256sum "$out/matrix.tsv" "$out/provenance.tsv" \
    > "$out/matrix_artifact_sha256.txt"
touch "$out/matrix.complete"
cat "$out/matrix.tsv"
