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
parser="$root/experiments/scripts/parse_hybrid_macro_profile.py"

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
[[ $(git -C "$root" rev-parse HEAD) == "$source_commit" ]] || {
    echo "source commit is not the checked-out HEAD" >&2
    exit 1
}
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence run from a dirty worktree" >&2
    exit 1
}

check_sha() {
    local path=$1 expected=$2 label=$3 actual
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
commit_config_sha=$(git -C "$root" show "$source_commit:$config_rel" |
    sha256sum | awk '{ print $1 }')
[[ $config_sha == "$commit_config_sha" ]] || {
    echo "live se.py does not match simulator source commit" >&2
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
printf '%s  %s\n' "$expected_ramulator_sha" "$ramulator" \
    > "$out/input/ramulator.sha256"
git -C "$root" archive --format=tar "$source_commit" -- \
    src/mem/MAA configs/common "$config_rel" \
    experiments/scripts/run_virtual_tile_consumer_case.sh \
    experiments/scripts/run_hybrid_macro_profile_matrix.sh \
    experiments/scripts/parse_hybrid_macro_profile.py \
    > "$out/input/simulator_source.tar"
simulator_source_sha=$(sha256sum "$out/input/simulator_source.tar" |
    awk '{ print $1 }')
{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'gem5_sha256=%s\n' "$expected_gem5_sha"
    printf 'simulator_source_archive_sha256=%s\n' "$simulator_source_sha"
} > "$out/input/gem5.provenance.txt"

selector="$out/treatment.txt"
LD_LIBRARY_PATH="$out/input${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" \
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
    --max-checkpoints=1 --cmd "$workload" \
    --options "deferred $selector" > "$out/checkpoint.log" 2>&1
grep -Fqx \
    'VIRTUAL_TILE_CONSUMER_LAYOUT mode=deferred page_elements=0 logical_elements=16384 mem_size=2147483648' \
    "$out/checkpoint.log"
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
    "$out/checkpoint.log") -eq 1 ]]
(
    cd "$out/checkpoint"
    find . -type f -print0 | sort -z | xargs -0 sha256sum
) > "$out/checkpoint.files.sha256"
checkpoint_identity=$(sha256sum "$out/checkpoint.files.sha256" |
    awk '{ print $1 }')
printf '%s\n' "$checkpoint_identity" > "$out/checkpoint.identity.sha256"

printf '%s\n' \
    $'label\tcase\twords_per_cycle\twrite_credits\trole' \
    $'native16\tnative_direct_16k\t4\t64\tnative_reference' \
    $'hybrid_base\ttransparent_4k\t4\t64\thybrid_baseline' \
    $'native4\tnative_direct_4k\t4\t64\tnative_reference' \
    $'hybrid_w2_c64\ttransparent_4k\t2\t64\twidth_sensitivity' \
    $'hybrid_w8_c64\ttransparent_4k\t8\t64\twidth_sensitivity' \
    $'hybrid_w4_c16\ttransparent_4k\t4\t16\tcredit_sensitivity' \
    $'hybrid_w4_c32\ttransparent_4k\t4\t32\tcredit_sensitivity' \
    $'hybrid_w4_c128\ttransparent_4k\t4\t128\tcredit_sensitivity' \
    $'hybrid_transport_upper\ttransparent_4k\t0\t512\ttransport_throughput_upper_bound' \
    > "$out/arms.tsv"

common=(
    DX100_SHARED_CHECKPOINT_DIR="$out/checkpoint"
    DX100_SHARED_TREATMENT_FILE="$selector"
    DX100_SHARED_CHECKPOINT_LOG="$out/checkpoint.log"
    DX100_FROZEN_RAMULATOR_LIBRARY="$ramulator"
    DX100_RAMULATOR_PROVENANCE_FILE="$out/input/ramulator.sha256"
    DX100_GEM5_SOURCE_COMMIT="$source_commit"
    DX100_GEM5_PROVENANCE_FILE="$out/input/gem5.provenance.txt"
    MAA_DEBUG_FLAGS=MAAVirtualTrace,MAAMacroEvent
    MAA_ROW_TABLE_SLICES=16
    MAA_ROW_TABLE_ROWS_PER_SLICE=64
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8
    MAA_OFFSET_TABLE_ENTRIES=16384
    MAA_OFFSET_TABLE_EPOCH_ENTRIES=16384
    MAA_VIRTUAL_GROW_ORDER=0
)

run_arm() {
    local label=$1 case_name=$2 words=$3 credits=$4
    # The restored guest opens one absolute selector path. Arms must remain
    # serial so all treatments consume byte-identical checkpoint state.
    env "${common[@]}" \
        MAA_VIRTUAL_WORDS_PER_CYCLE="$words" \
        MAA_VIRTUAL_MAX_OUTSTANDING_WRITES="$credits" \
        "$case_runner" "$gem5" "$workload" "$case_name" \
        "$out/$label" > "$out/$label.launch.log" 2>&1
}

while IFS=$'\t' read -r label case_name words credits role; do
    [[ $label != label ]] || continue
    run_arm "$label" "$case_name" "$words" "$credits"
done < "$out/arms.tsv"

python3 - "$out/matrix.provenance.json" "$source_commit" \
    "$expected_gem5_sha" "$expected_workload_sha" \
    "$expected_ramulator_sha" "$simulator_source_sha" \
    "$checkpoint_identity" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "source_commit": sys.argv[2],
    "gem5_sha256": sys.argv[3],
    "workload_sha256": sys.argv[4],
    "ramulator_sha256": sys.argv[5],
    "simulator_source_archive_sha256": sys.argv[6],
    "checkpoint_identity_sha256": sys.argv[7],
    "ack_intervention": "not_performed",
    "throughput_upper_bound": {
        "arm": "hybrid_transport_upper",
        "virtual_words_per_cycle": 0,
        "write_credits": 512,
        "meaning": "unlimited local retirement rate plus nonbinding credits; real backing ACKs and later consumer visibility are preserved",
    },
}
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

python3 "$parser" --root "$out" --arms "$out/arms.tsv" \
    --output-json "$out/macro_profile.json" \
    --output-tsv "$out/macro_profile.tsv"

{
    printf 'artifact\tsha256\n'
    for path in "$gem5" "$workload" "$ramulator" \
        "$out/input/simulator_source.tar" "$out/checkpoint.files.sha256" \
        "$out/arms.tsv" "$out/macro_profile.json" \
        "$out/macro_profile.tsv"; do
        printf '%s\t%s\n' "$path" \
            "$(sha256sum "$path" | awk '{ print $1 }')"
    done
} > "$out/artifact_sha256.tsv"

cat "$out/macro_profile.tsv"
