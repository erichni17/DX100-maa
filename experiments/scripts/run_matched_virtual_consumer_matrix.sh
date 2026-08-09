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
    > "$out/input/simulator_source.tar"
simulator_source_sha=$(sha256sum "$out/input/simulator_source.tar" |
    awk '{ print $1 }')
{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'gem5_sha256=%s\n' "$expected_gem5_sha"
    printf 'simulator_source_archive_sha256=%s\n' "$simulator_source_sha"
} > "$out/input/gem5.provenance.txt"
printf '%s\n' --maa_virtual_descriptor_spool_source_bypass_cache \
    > "$out/input/a_source_bypass.args"

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
sha256sum "$out/checkpoint.files.sha256" | awk '{ print $1 }' \
    > "$out/checkpoint.identity.sha256"

common=(
    DX100_SHARED_CHECKPOINT_DIR="$out/checkpoint"
    DX100_SHARED_TREATMENT_FILE="$selector"
    DX100_SHARED_CHECKPOINT_LOG="$out/checkpoint.log"
    DX100_FROZEN_RAMULATOR_LIBRARY="$ramulator"
    DX100_RAMULATOR_PROVENANCE_FILE="$out/input/ramulator.sha256"
    DX100_GEM5_SOURCE_COMMIT="$source_commit"
    DX100_GEM5_PROVENANCE_FILE="$out/input/gem5.provenance.txt"
    MAA_DEBUG_FLAGS=MAAVirtualTrace,MAAPhysicalRecordTrace,MAAIssueDigest
    MAA_DESCRIPTOR_SPOOL_VARIANT=resident_first
)
native_geometry=(
    MAA_ROW_TABLE_SLICES=16
    MAA_ROW_TABLE_ROWS_PER_SLICE=32
    MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8
    MAA_OFFSET_TABLE_ENTRIES=4096
    MAA_OFFSET_TABLE_EPOCH_ENTRIES=4096
)
virtual_geometry=(
    "${native_geometry[@]}"
    MAA_VIRTUAL_INDEX_PARTITIONS=64
    MAA_VIRTUAL_INDEX_RANGE_PASSES=1
    MAA_VIRTUAL_INDEX_RANGE_POLICY=3
    MAA_VIRTUAL_INDEX_FORCE_CACHE=1
    MAA_VIRTUAL_INDEX_DESCRIPTOR_SPOOL=1
    MAA_VIRTUAL_PARTITION_KEEP_COMBINER=1
    MAA_VIRTUAL_GROW_ORDER=1
    MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE=16
    MAA_REQUIRE_INDEX_FILTER_WAIT=1
    MAA_VIRTUAL_DESCRIPTOR_SPOOL_READ_AHEAD=1
    DX100_EXTRA_MAA_ARGS_FILE="$out/input/a_source_bypass.args"
)

run_arm() {
    local label=$1 case_name=$2 require_physical=$3
    shift 3
    # The restored guest opens one absolute selector path. Run arms serially so
    # each observes its own treatment while retaining byte-identical checkpoint
    # state. Parallel mutation would make the comparison nondeterministic.
    env "${common[@]}" MAA_REQUIRE_PHYSICAL_RECORD_TRACE="$require_physical" \
        "$@" "$case_runner" "$gem5" "$workload" "$case_name" \
        "$out/$label" > "$out/$label.launch.log" 2>&1
}

run_arm native4 native_direct_4k 0 \
    MAA_REQUIRE_SOURCE_ISSUE_DIGEST=0 "${native_geometry[@]}"
run_arm paged4 paged_4k 1 \
    MAA_REQUIRE_SOURCE_ISSUE_DIGEST=1 "${virtual_geometry[@]}"
run_arm transparent4 transparent_4k 1 \
    MAA_REQUIRE_SOURCE_ISSUE_DIGEST=1 "${virtual_geometry[@]}"

for arm in paged4 transparent4; do
    python3 "$validator" --mode treatment \
        --manifest "$out/$arm/manifest.txt" \
        --result "$out/$arm/result.tsv" \
        --trace "$out/$arm/run/virtual_trace.log" \
        --output-dir "$out/$arm/read_ahead_validation"
done

python3 - "$out" <<'PY'
import csv
import sys
from pathlib import Path

out = Path(sys.argv[1])
arms = ("native4", "paged4", "transparent4")

def read_result(arm):
    with (out / arm / "result.tsv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    if len(rows) != 1:
        raise SystemExit(f"{arm}: expected exactly one result row")
    return rows[0]

rows = {arm: read_result(arm) for arm in arms}
if len({row["output_hash"] for row in rows.values()}) != 1:
    raise SystemExit("exact output hashes differ")

producer_fields = (
    "physical_record_sha256",
    "bounded_summary_histogram_sha256",
    "source_issue_sha256",
    "source_reads",
    "descriptor_spool_line_writes",
    "descriptor_spool_write_bytes",
    "descriptor_spool_write_acks",
    "descriptor_spool_line_reads",
    "descriptor_spool_read_bytes",
    "write_issues",
    "write_completions",
)
for field in producer_fields:
    if rows["paged4"][field] != rows["transparent4"][field]:
        raise SystemExit(
            f"producer mismatch for {field}: "
            f"{rows['paged4'][field]}/{rows['transparent4'][field]}"
        )

for arm in ("paged4", "transparent4"):
    row = rows[arm]
    if row["write_issues"] != row["write_completions"]:
        raise SystemExit(f"{arm}: retirement traffic did not close")
    if row["descriptor_spool_line_writes"] != row["descriptor_spool_write_acks"]:
        raise SystemExit(f"{arm}: descriptor writes did not close")
    if row["descriptor_spool_line_reads"] != row["descriptor_spool_write_acks"]:
        raise SystemExit(f"{arm}: descriptor replay did not close")
    for field in (
        "bounded_word_entries",
        "bounded_offset_entries",
        "bounded_row_directory_entries",
        "bounded_row_line_entries",
    ):
        if int(row[field]) > 4096:
            raise SystemExit(f"{arm}: {field} exceeds 4K: {row[field]}")
    if row["pages_ready"] != "4" or row["page_ready_signals"] != "4":
        raise SystemExit(f"{arm}: page readiness did not close")

checkpoint_ids = {}
for arm in arms:
    digest_file = out / arm / "shared_checkpoint_identity.sha256"
    checkpoint_ids[arm] = digest_file.read_text(encoding="utf-8").split()[0]
if len(set(checkpoint_ids.values())) != 1:
    raise SystemExit(f"checkpoint identities differ: {checkpoint_ids}")

fields = (
    "case",
    "output_hash",
    "simTicks",
    "fill_sim_ticks",
    "request_sim_ticks",
    "cpu_cycles",
    "physical_record_sha256",
    "source_issue_sha256",
    "first_page_ready_cycles",
    "all_pages_ready_cycles",
    "page_ready_span_cycles",
    "stream_spd_reads",
    "stream_writes",
    "alu_compute_cycles",
)
with (out / "matrix.tsv").open("w", encoding="utf-8", newline="") as f:
    writer = csv.writer(f, delimiter="\t", lineterminator="\n")
    writer.writerow(("arm",) + fields)
    for arm in arms:
        writer.writerow((arm,) + tuple(rows[arm][field] for field in fields))

ticks = {arm: int(rows[arm]["simTicks"]) for arm in arms}
comparisons = {
    "paged4_vs_native4_latency_pct":
        (ticks["paged4"] / ticks["native4"] - 1.0) * 100.0,
    "transparent4_vs_native4_latency_pct":
        (ticks["transparent4"] / ticks["native4"] - 1.0) * 100.0,
    "transparent4_vs_paged4_latency_pct":
        (ticks["transparent4"] / ticks["paged4"] - 1.0) * 100.0,
}
(out / "comparisons.tsv").write_text(
    "metric\tvalue\n" + "".join(
        f"{key}\t{value:.9f}\n" for key, value in comparisons.items()
    ),
    encoding="utf-8",
)

with (out / "producer_equivalence.tsv").open(
    "w", encoding="utf-8", newline=""
) as f:
    writer = csv.writer(f, delimiter="\t", lineterminator="\n")
    writer.writerow(("field", "paged4", "transparent4"))
    for field in producer_fields:
        writer.writerow((field, rows["paged4"][field], rows["transparent4"][field]))

(out / "matrix.complete").touch()
PY

{
    printf 'artifact\tsha256\n'
    for path in "$gem5" "$workload" "$ramulator" \
        "$out/input/simulator_source.tar" "$out/checkpoint.files.sha256" \
        "$out/matrix.tsv" "$out/comparisons.tsv" \
        "$out/producer_equivalence.tsv"; do
        printf '%s\t%s\n' "$path" "$(sha256sum "$path" | awk '{ print $1 }')"
    done
} > "$out/provenance.tsv"

cat "$out/matrix.tsv"
cat "$out/comparisons.tsv"
