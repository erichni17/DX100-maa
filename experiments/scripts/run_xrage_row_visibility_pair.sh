#!/usr/bin/env bash
# Run a serialized, fail-closed XRAGE row64/row128 visibility diagnostic.
set -euo pipefail

if [[ $# -ne 8 ]]; then
    echo "usage: $0 OUTDIR GEM5 XRAGE_VERIFY INPUT_JSON RAMULATOR_LIB RAMULATOR_PROVENANCE SIMULATOR_PROVENANCE CHECKPOINT_RUN" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5=$(realpath "$2")
workload=$(realpath "$3")
supplied_input=$(realpath "$4")
ramulator=$(realpath "$5")
ramulator_provenance=$(realpath "$6")
simulator_provenance=$(realpath "$7")
checkpoint=$(realpath "$8")
recover="$root/experiments/scripts/recover_xrage_checkpoint.sh"
analyzer="$root/experiments/analysis/analyze_xrage_row_visibility.py"
expected_source=f60a5b8da5cbb1a355dbca99b1cb721b3980953a
expected_hash=11014995430510232451
expected_elements=2097152

[[ -x $gem5 && -x $workload && -f $supplied_input && -f $ramulator &&
   -f $ramulator_provenance && -f $simulator_provenance &&
   -f $checkpoint/manifest.txt && -f $checkpoint/checkpoint.command &&
   -d $checkpoint/checkpoint && -x $recover && -x $analyzer ]] || {
    echo "missing executable, provenance, analyzer, or checkpoint input" >&2
    exit 2
}
[[ ! -e $out ]] || {
    echo "refusing to overwrite evidence root: $out" >&2
    exit 2
}
[[ -z $(git -C "$root" status --short) ]] || {
    echo "XRAGE row visibility requires a clean source worktree" >&2
    git -C "$root" status --short >&2
    exit 1
}

checkpoint_command=$(<"$checkpoint/checkpoint.command")
[[ $checkpoint_command == *"--cpu-type AtomicSimpleCPU"* &&
   ! $checkpoint_command =~ (^|[[:space:]])--maa($|[[:space:]]) &&
   $checkpoint_command != *"--maa_num_"* &&
   $checkpoint_command != *"--maa_physical_"* ]] || {
    echo "checkpoint is not treatment-neutral pre-MAA state" >&2
    exit 1
}
runtime_input=$(sed -n 's/^input=//p' "$checkpoint/manifest.txt")
runtime_input_hash=$(sha256sum "$runtime_input" 2>/dev/null | awk '{print $1}')
supplied_input_hash=$(sha256sum "$supplied_input" | awk '{print $1}')
[[ -f $runtime_input && -n $runtime_input_hash &&
   $runtime_input_hash == "$supplied_input_hash" ]] || {
    echo "supplied XRAGE input differs from checkpoint input bytes" >&2
    exit 1
}
workload_hash=$(sha256sum "$workload" | awk '{print $1}')
grep -Eq "^${workload_hash}  " "$checkpoint/artifact_sha256.txt" || {
    echo "XRAGE verifier does not match checkpoint guest ABI" >&2
    exit 1
}

mapfile -t simulator_identity < <(python3 - "$simulator_provenance" <<'PY'
import json, sys
record = json.load(open(sys.argv[1], encoding="utf-8"))
print(record.get("schema", ""))
print(record.get("simulator_source_commit", ""))
print(record.get("frozen_binary", {}).get("sha256", ""))
print(record.get("build", {}).get("exit_code", ""))
print(record.get("source_status", {}).get("sha256", ""))
PY
)
[[ ${simulator_identity[0]:-} == dx100.simulator_provenance.v1 &&
   ${simulator_identity[1]:-} == "$expected_source" &&
   ${simulator_identity[2]:-} == $(sha256sum "$gem5" | awk '{print $1}') &&
   ${simulator_identity[3]:-} == 0 &&
   ${simulator_identity[4]:-} == e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 ]] || {
    echo "simulator provenance does not authenticate a clean f60a5b8d build" >&2
    exit 1
}

mapfile -t ramulator_identity < <(python3 - "$ramulator_provenance" <<'PY'
import json, sys
record = json.load(open(sys.argv[1], encoding="utf-8"))
print(record.get("schema", ""))
print(record.get("frozen_library", {}).get("sha256", ""))
print(record.get("elf_build_id", ""))
PY
)
ramulator_build_id=$(readelf -n "$ramulator" | sed -n 's/.*Build ID: //p' | head -1)
[[ ${ramulator_identity[0]:-} == dx100.ramulator_provenance.v1 &&
   ${ramulator_identity[1]:-} == $(sha256sum "$ramulator" | awk '{print $1}') &&
   -n $ramulator_build_id && $ramulator_build_id == "${ramulator_identity[2]:-}" ]] || {
    echo "Ramulator provenance does not authenticate the requested library" >&2
    exit 1
}

library_path=$(dirname "$ramulator")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
loaded_ramulator=$(LD_LIBRARY_PATH="$library_path" ldd "$gem5" |
    awk '$1 == "libramulator.so" { print $3 }')
[[ -n $loaded_ramulator && $(realpath "$loaded_ramulator") == "$ramulator" ]] || {
    echo "gem5 does not resolve the authenticated Ramulator library" >&2
    exit 1
}

mkdir -p "$out"
status="$out/campaign.exit"
trap 'rc=$?; printf "%s\n" "$rc" > "$status"' EXIT
checkpoint_files="$out/checkpoint_files.sha256"
(
    cd "$checkpoint/checkpoint"
    find . -maxdepth 2 -type f \( -name m5.cpt -o -name '*.pmem' -o -name config.ini \) -print0 |
        sort -z | xargs -0 sha256sum
) > "$checkpoint_files"

python3 - "$out/manifest.json" "$gem5" "$workload" "$runtime_input" \
    "$supplied_input" "$ramulator" "$checkpoint" "$checkpoint_files" \
    "$simulator_provenance" "$ramulator_provenance" "$(git -C "$root" rev-parse HEAD)" <<'PY'
import hashlib, json, pathlib, sys

def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

(output, gem5, workload, runtime_input, supplied_input, ramulator, checkpoint,
 checkpoint_files, simulator_provenance, ramulator_provenance, runner_commit) = sys.argv[1:]
record = {
    "schema": "dx100.xrage_row_visibility_run.v1",
    "simulator_source_commit": "f60a5b8da5cbb1a355dbca99b1cb721b3980953a",
    "runner_source_commit": runner_commit,
    "execution": "serialized",
    "serialization_reason": "Conservative isolation of restore/output state for the shared pre-MAA checkpoint.",
    "treatment": "maa_num_row_table_rows_per_slice only: 64 versus 128",
    "row128_label": "high-cost diagnostic; never baseline",
    "expected_output": {"elements": 2097152, "hash": 11014995430510232451},
    "frozen_artifacts": {
        "gem5": {"path": str(pathlib.Path(gem5).resolve()), "sha256": digest(gem5)},
        "workload": {"path": str(pathlib.Path(workload).resolve()), "sha256": digest(workload)},
        "input": {"path": str(pathlib.Path(runtime_input).resolve()), "sha256": digest(runtime_input)},
        "supplied_input": {"path": str(pathlib.Path(supplied_input).resolve()), "sha256": digest(supplied_input)},
        "ramulator": {"path": str(pathlib.Path(ramulator).resolve()), "sha256": digest(ramulator)},
        "simulator_provenance": {"path": str(pathlib.Path(simulator_provenance).resolve()), "sha256": digest(simulator_provenance)},
        "ramulator_provenance": {"path": str(pathlib.Path(ramulator_provenance).resolve()), "sha256": digest(ramulator_provenance)},
    },
    "checkpoint": {
        "path": str(pathlib.Path(checkpoint).resolve()),
        "manifest_sha256": digest(pathlib.Path(checkpoint) / "manifest.txt"),
        "command_sha256": digest(pathlib.Path(checkpoint) / "checkpoint.command"),
        "files_manifest_sha256": digest(checkpoint_files),
        "treatment_neutral": True,
    },
    "common_configuration": {
        "logical_tile_elements": 16384,
        "physical_tile_elements": 4096,
        "offset_table_entries": 16384,
        "offset_table_epoch_entries": 16384,
        "virtual_index_partitions": 1,
        "index_passes": 1,
        "debug_flags": ["MAAReorderTrace", "MAAIssueDigest"],
    },
}
pathlib.Path(output).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
PY

common=(
    XRAGE_SIMULATOR_SOURCE_COMMIT="$expected_source"
    XRAGE_ALLOW_PRE_MAA_RETARGET=1
    XRAGE_ARM=direct_index_4k
    XRAGE_GUEST_ARM=direct4
    XRAGE_DEBUG_FLAGS=MAAReorderTrace,MAAIssueDigest
    MAA_PHYSICAL_TILE_ELEMENTS=4096
    MAA_VIRTUAL_GROW_ORDER=1
    MAA_VIRTUAL_NATIVE_ISSUE_ORDER=0
    MAA_VIRTUAL_INDEX_FORCE_CACHE=1
    MAA_VIRTUAL_INDEX_BUFFER_LINES=128
    MAA_VIRTUAL_INDEX_PARTITIONS=1
    MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE=0
    MAA_VIRTUAL_PARTITION_KEEP_COMBINER=0
    MAA_NUM_INITIAL_ROW_TABLE_SLICES=16
    MAA_NUM_OFFSET_TABLE_ENTRIES=16384
    MAA_NUM_OFFSET_TABLE_EPOCH_ENTRIES=16384
    MAA_NUM_INDIRECT_UNITS_PER_MAA=1
    MAA_RETIREMENT_CACHE_SIZE=1kB
)

run_rep() {
    local label=$1
    local rows=$2
    local ordinal=$3
    local rep="$out/$label/rep$ordinal"
    mkdir -p "$out/$label"
    set +e
    env LD_LIBRARY_PATH="$library_path" "${common[@]}" \
        MAA_ROW_TABLE_ROWS_PER_SLICE="$rows" \
        "$recover" "$gem5" "$workload" "$runtime_input" "$checkpoint" "$rep" \
        > "$out/$label/rep$ordinal.launch.log" 2>&1
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$out/$label/rep$ordinal.launch.exit"
    [[ $rc -eq 0 ]] || {
        echo "$label rep$ordinal failed with rc=$rc" >&2
        return 1
    }
    grep -Fqx "MAA_GATHER_VERIFY_PASS length=$expected_elements hash=$expected_hash" \
        "$rep/restore.log" || {
        echo "$label rep$ordinal failed exact XRAGE output" >&2
        return 1
    }
}

# Deliberately serialized: row128 is a high-cost diagnostic, never baseline.
run_rep row64 64 1
run_rep row128 128 1

set +e
python3 "$analyzer" "$out" --output "$out/preliminary_rep1.json"
preliminary_rc=$?
set -e
[[ $preliminary_rc -eq 0 || $preliminary_rc -eq 3 ]] || exit "$preliminary_rc"
under_two=$(python3 - "$out/preliminary_rep1.json" <<'PY'
import json, sys
print(1 if json.load(open(sys.argv[1]))["comparison"]["under_2_percent"] else 0)
PY
)

if [[ $under_two == 1 ]]; then
    run_rep row64 64 2
    run_rep row128 128 2
    deterministic=1
    for label in row64 row128; do
        cmp -s "$out/$label/rep1/result.tsv" "$out/$label/rep2/result.tsv" || deterministic=0
        tick1=$(awk 'NR == 2 { print $2 }' "$out/$label/rep1/result.tsv")
        tick2=$(awk 'NR == 2 { print $2 }' "$out/$label/rep2/result.tsv")
        [[ -n $tick1 && $tick1 == "$tick2" ]] || deterministic=0
    done
    if [[ $deterministic == 0 ]]; then
        run_rep row64 64 3
        run_rep row128 128 3
    fi
fi

python3 "$analyzer" "$out" --output "$out/analysis.json"
python3 - "$out/analysis.json" <<'PY'
import json, sys
record = json.load(open(sys.argv[1]))
if record["status"] != "PASS":
    raise SystemExit("final XRAGE row-visibility analysis is incomplete")
PY
find "$out" -type f ! -name evidence_sha256.txt -print0 | sort -z | xargs -0 sha256sum \
    > "$out/evidence_sha256.txt"
touch "$out/xrage_row_visibility.pass"
echo "PASS XRAGE row visibility: $out"
