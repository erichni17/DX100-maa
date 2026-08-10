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

mkdir -p "$out/input" "$out/checkpoint"
trap 'rc=$?; printf "%s\n" "$rc" > "$out/matrix.exit"' EXIT
cp --reflink=auto "$gem5_source" "$out/input/gem5.opt"
cp --reflink=auto "$workload_source" "$out/input/workload"
cp --reflink=auto "$ramulator_source" "$out/input/libramulator.so"
chmod 0555 "$out/input/gem5.opt" "$out/input/workload"
gem5="$out/input/gem5.opt"
workload="$out/input/workload"
ramulator="$out/input/libramulator.so"
printf '%s  libramulator.so\n' "$expected_ramulator_sha" \
    > "$out/input/ramulator.sha256"

git -C "$root" archive --format=tar "$source_commit" -- \
    src/mem/MAA configs/common configs/deprecated/example/se.py \
    experiments/scripts/run_virtual_tile_consumer_case.sh \
    experiments/scripts/run_hybrid_idealized_ack_matrix.sh \
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

printf '%s\n' '--maa_virtual_idealized_write_ack' \
    > "$out/idealized.args"
printf '%s\n' \
    $'label\tidealized_ack' \
    $'baseline_r1\t0' \
    $'idealized_r1\t1' \
    $'baseline_r2\t0' \
    $'idealized_r2\t1' > "$out/arms.tsv"

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
    MAA_VIRTUAL_WORDS_PER_CYCLE=4
    MAA_VIRTUAL_MAX_OUTSTANDING_WRITES=64
)

while IFS=$'\t' read -r label idealized; do
    [[ $label != label ]] || continue
    extra=()
    if [[ $idealized == 1 ]]; then
        extra+=(DX100_EXTRA_MAA_ARGS_FILE="$out/idealized.args")
    fi
    env "${common[@]}" "${extra[@]}" \
        "$case_runner" "$gem5" "$workload" transparent_4k \
        "$out/$label" > "$out/$label.launch.log" 2>&1
done < "$out/arms.tsv"

python3 - "$out" "$source_commit" "$expected_gem5_sha" \
    "$expected_workload_sha" "$expected_ramulator_sha" \
    "$simulator_source_sha" "$checkpoint_identity" <<'PY'
import csv
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
labels = ("baseline_r1", "idealized_r1", "baseline_r2", "idealized_r2")


def first_roi_stat(path: Path, suffix: str) -> int:
    section = 0
    total = 0
    found = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            section += 1
            continue
        if line.startswith("---------- End Simulation Statistics") and section == 1:
            break
        if section != 1:
            continue
        fields = line.split()
        if len(fields) >= 2 and fields[0].endswith(suffix):
            total += int(float(fields[1]))
            found = True
    if not found:
        raise ValueError(f"missing first-ROI stat {suffix} in {path}")
    return total


records = []
for label in labels:
    arm = root / label
    if not (arm / "virtual_tile_consumer_case.pass").is_file():
        raise ValueError(f"{label} lacks terminal pass marker")
    with (arm / "result.tsv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle, delimiter="\t"))
    config = (arm / "run/config.ini").read_text(encoding="utf-8")
    expected_flag = label.startswith("idealized")
    needle = f"virtual_idealized_write_ack={'true' if expected_flag else 'false'}"
    if needle not in config:
        raise ValueError(f"{label} config lacks {needle}")
    stats = arm / "run/stats.txt"
    idealized_pages = first_roi_stat(stats, "IND_VirtIdealizedAckPages")
    issues = int(row["write_issues"])
    completions = int(row["write_completions"])
    pages_ready = int(row["pages_ready"])
    if issues <= 0 or issues != completions:
        raise ValueError(f"{label} write closure {issues}/{completions}")
    if pages_ready != 4:
        raise ValueError(f"{label} exposed {pages_ready}/4 pages")
    if idealized_pages != (4 if expected_flag else 0):
        raise ValueError(
            f"{label} idealized intervention count {idealized_pages}"
        )
    trace = (arm / "run/virtual_trace.log").read_text(encoding="utf-8")
    event_count = trace.count("event=idealized_ack_page_ready ")
    if event_count != idealized_pages:
        raise ValueError(
            f"{label} trace/stat intervention mismatch "
            f"{event_count}/{idealized_pages}"
        )
    identity = (arm / "shared_checkpoint_identity.sha256").read_text(
        encoding="utf-8"
    ).split()[0]
    if identity != sys.argv[7]:
        raise ValueError(f"{label} checkpoint identity mismatch")
    records.append(
        {
            "label": label,
            "idealized_ack": expected_flag,
            "simTicks": int(row["simTicks"]),
            "output_hash": row["output_hash"],
            "write_issues": issues,
            "write_completions": completions,
            "pages_ready": pages_ready,
            "idealized_ack_pages": idealized_pages,
            "first_page_ready_cycles": int(row["first_page_ready_cycles"]),
            "all_pages_ready_cycles": int(row["all_pages_ready_cycles"]),
            "page_ready_span_cycles": int(row["page_ready_span_cycles"]),
        }
    )

if len({record["output_hash"] for record in records}) != 1:
    raise ValueError("exact output hashes differ")
ticks = {record["label"]: record["simTicks"] for record in records}
if ticks["baseline_r1"] != ticks["baseline_r2"]:
    raise ValueError("baseline replicas differ")
if ticks["idealized_r1"] != ticks["idealized_r2"]:
    raise ValueError("idealized replicas differ")

baseline = ticks["baseline_r1"]
idealized = ticks["idealized_r1"]
summary = {
    "schema": 1,
    "intervention": (
        "output pages become consumer-visible when their final backing write "
        "issues; all real WriteResp events still fence producer completion"
    ),
    "candidate_architecture": False,
    "provenance": {
        "source_commit": sys.argv[2],
        "gem5_sha256": sys.argv[3],
        "workload_sha256": sys.argv[4],
        "ramulator_sha256": sys.argv[5],
        "simulator_source_archive_sha256": sys.argv[6],
        "checkpoint_identity_sha256": sys.argv[7],
    },
    "records": records,
    "comparison": {
        "baseline_ticks": baseline,
        "idealized_ticks": idealized,
        "latency_change_pct": (idealized / baseline - 1.0) * 100.0,
        "speedup": baseline / idealized,
    },
}
(root / "summary.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
with (root / "summary.tsv").open("w", newline="", encoding="utf-8") as handle:
    fields = list(records[0])
    writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
    writer.writeheader()
    writer.writerows(records)

with (root / "artifact_sha256.tsv").open("w", encoding="utf-8") as handle:
    handle.write("artifact\tsha256\n")
    for path in (
        root / "input/gem5.opt",
        root / "input/workload",
        root / "input/libramulator.so",
        root / "input/simulator_source.tar",
        root / "checkpoint.files.sha256",
        root / "arms.tsv",
        root / "idealized.args",
        root / "summary.json",
        root / "summary.tsv",
    ):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        handle.write(f"{path}\t{digest}\n")
PY

touch "$out/hybrid_idealized_ack_matrix.pass"
cat "$out/summary.tsv"
