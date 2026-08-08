#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
    echo "usage: $0 OUTDIR GEM5_BIN WORKLOAD_BIN RAMULATOR_LIB RAMULATOR_PROVENANCE" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5_source=$(realpath "$2")
workload_source=$(realpath "$3")
ramulator_source=$(realpath "$4")
provenance_source=$(realpath "$5")
status="$out/pair.exit"

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
trap 'rc=$?; printf "%s\n" "$rc" > "$status"' EXIT
cp --reflink=auto "$gem5_source" "$out/input/gem5.opt"
cp --reflink=auto "$workload_source" "$out/input/workload"
cp --reflink=auto "$ramulator_source" "$out/input/libramulator.so"
cp -- "$provenance_source" "$out/input/ramulator_provenance.json"
chmod 0555 "$out/input/gem5.opt" "$out/input/workload"

gem5="$out/input/gem5.opt"
workload="$out/input/workload"
ramulator="$out/input/libramulator.so"
provenance="$out/input/ramulator_provenance.json"
config="$root/configs/deprecated/example/se.py"
selector="$out/shared_treatment.txt"
checkpoint="$out/shared_checkpoint"

git -C "$root" rev-parse HEAD > "$out/input/source_commit"
sha256sum "$gem5" "$workload" "$ramulator" "$provenance" \
    > "$out/input/artifact_sha256.txt"
sha256sum \
    "$root/src/mem/MAA/IndirectAccess.cc" \
    "$root/src/mem/MAA/IndirectAccess.hh" \
    "$root/src/mem/MAA/MAA.cc" \
    "$root/src/mem/MAA/MAA.hh" \
    "$root/src/mem/MAA/MAA.py" \
    "$root/src/mem/MAA/Port.cc" \
    "$root/src/mem/MAA/TransparentSPDController.hh" \
    "$root/src/mem/MAA/StreamAccess.cc" \
    "$root/configs/common/Options.py" \
    "$root/configs/common/MAAConfig.py" \
    "$root/experiments/scripts/run_hybrid_tail_issue_ready_pair.sh" \
    "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" \
    > "$out/input/source_snapshot.sha256"
LD_LIBRARY_PATH="$out/input${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    ldd "$gem5" > "$out/input/gem5.ldd.txt"
loaded_ramulator=$(awk '$1 == "libramulator.so" { print $3 }' \
    "$out/input/gem5.ldd.txt")
[[ -n $loaded_ramulator && $(realpath "$loaded_ramulator") == "$ramulator" ]] || {
    echo "frozen gem5 did not resolve the frozen Ramulator library" >&2
    exit 1
}

[[ ! -e $selector ]]
set +e
MAA_OFFSET_TABLE_ENTRIES=16384 \
MAA_OFFSET_TABLE_EPOCH_ENTRIES=16384 \
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

printf 'ordinal\tarm\tselector_absent_before\tselector_absent_after\n' \
    > "$out/unlike_arms.serialized.tsv"
ordinal=0
for arm in transparent_4k transparent_issue_ready_4k; do
    ordinal=$((ordinal + 1))
    [[ ! -e $selector ]] || {
        echo "shared treatment selector remained before serialized arm $arm" >&2
        exit 1
    }
    DX100_SHARED_CHECKPOINT_DIR="$checkpoint" \
    DX100_SHARED_TREATMENT_FILE="$selector" \
    DX100_FROZEN_RAMULATOR_LIBRARY="$ramulator" \
    DX100_RAMULATOR_PROVENANCE_FILE="$provenance" \
    MAA_REQUIRE_PHYSICAL_RECORD_TRACE=1 \
    MAA_DEBUG_FLAGS=MAAVirtualTrace,MAAPhysicalRecordTrace \
    MAA_OFFSET_TABLE_ENTRIES=16384 \
    MAA_OFFSET_TABLE_EPOCH_ENTRIES=16384 \
    "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" \
        "$gem5" "$workload" "$arm" "$out/$arm"
    cmp -s "$out/$arm/shared_checkpoint_files.sha256" \
        "$out/checkpoint_files.pre_treatment.sha256"
    [[ ! -e $selector ]] || {
        echo "serialized arm $arm did not consume the shared selector" >&2
        exit 1
    }
    printf '%s\t%s\t1\t1\n' "$ordinal" "$arm" \
        >> "$out/unlike_arms.serialized.tsv"
done

control_hash=$(awk -F '\t' 'NR == 2 { print $2 }' \
    "$out/transparent_4k/result.tsv")
candidate_hash=$(awk -F '\t' 'NR == 2 { print $2 }' \
    "$out/transparent_issue_ready_4k/result.tsv")
control_records=$(awk -F '\t' 'NR == 2 { print $12 }' \
    "$out/transparent_4k/result.tsv")
candidate_records=$(awk -F '\t' 'NR == 2 { print $12 }' \
    "$out/transparent_issue_ready_4k/result.tsv")
[[ -n $control_hash && $control_hash == "$candidate_hash" ]]
[[ -n $control_records && $control_records == "$candidate_records" ]]
[[ $(<"$out/transparent_4k/restore.exit") == 0 ]]
[[ $(<"$out/transparent_issue_ready_4k/restore.exit") == 0 ]]
touch "$out/pair.complete"
