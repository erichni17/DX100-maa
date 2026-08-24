#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 ROW64_EVIDENCE_ROOT OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
base=$(realpath "$1")
out=$(realpath -m "$2")
case_runner="$root/experiments/scripts/run_virtual_tile_consumer_case.sh"

[[ ! -e $out ]] || {
    echo "refusing to overwrite output: $out" >&2
    exit 2
}
[[ -z $(git -C "$root" status --short) ]] || {
    echo "capacity sensitivity requires a clean committed source tree" >&2
    exit 1
}

frozen_gem5="$base/inputs/gem5.opt"
frozen_binary="$base/inputs/api_binary"
frozen_ramulator="$base/inputs/libramulator.so"
ramulator_provenance="$base/inputs/ramulator.sha256"
selector="$base/inputs/treatment.txt"
checkpoint="$base/checkpoint/gem5"
checkpoint_log="$base/checkpoint/checkpoint.log"
base_current="$base/current"

for required in \
    "$frozen_gem5" "$frozen_binary" "$frozen_ramulator" \
    "$ramulator_provenance" "$selector" "$checkpoint_log" \
    "$base_current/result.tsv" "$base_current/manifest.txt" \
    "$base_current/restore.exit" \
    "$base_current/virtual_tile_consumer_case.pass" \
    "$base_current/source_snapshot/run_virtual_tile_consumer_case.sh"; do
    [[ -f $required ]] || {
        echo "missing frozen row64 evidence file: $required" >&2
        exit 2
    }
done
[[ -d $checkpoint && -x $frozen_gem5 && -x $frozen_binary ]] || {
    echo "missing frozen row64 checkpoint or executable" >&2
    exit 2
}

expected_api=963940eeaface13cb53f73b565a88b2994922c2ff3ef55f167d9577df210c559
expected_ramulator=76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753
campaign_commit=$(git -C "$root" rev-parse HEAD)
gem5_source_commit=$(sed -n 's/^gem5_source_commit=//p' \
    "$base_current/manifest.txt")
[[ $gem5_source_commit == "$campaign_commit" ]] || {
    echo "row64 gem5 source $gem5_source_commit != campaign $campaign_commit" \
        >&2
    exit 1
}

check_hash() {
    local expected=$1
    local path=$2
    local actual
    actual=$(sha256sum "$path" | awk '{print $1}')
    [[ $actual == "$expected" ]] || {
        echo "frozen evidence hash mismatch: $path $actual != $expected" >&2
        exit 1
    }
}

check_hash "$expected_api" "$frozen_binary"
check_hash "$expected_ramulator" "$frozen_ramulator"
grep -Fqx '0' "$base/checkpoint/checkpoint.exit"
grep -Fqx '0' "$base_current/restore.exit"
grep -Fq "source_commit=$campaign_commit" \
    "$base_current/manifest.txt"
grep -Fq "shared_checkpoint=$checkpoint" "$base_current/manifest.txt"
grep -Fq -- "--options 'deferred $selector'" "$checkpoint_log"
[[ -s $base_current/shared_checkpoint_identity.sha256 ]] || {
    echo "row64 arm lacks shared checkpoint identity" >&2
    exit 1
}
cmp -s "$case_runner" \
    "$base_current/source_snapshot/run_virtual_tile_consumer_case.sh" || {
    echo "current case runner differs from the frozen row64 runner" >&2
    exit 1
}
cmp -s "$root/src/mem/MAA/IndirectAccess.cc" \
    "$base_current/source_snapshot/IndirectAccess.cc" || {
    echo "current strict source differs from the frozen row64 source" >&2
    exit 1
}

mkdir -p "$out/inputs"
printf '%s\n' '--maa_virtual_strict_two_phase' \
    > "$out/inputs/strict.args"
{
    printf 'base_evidence=%s\n' "$base"
    printf 'base_current_result=%s\n' "$base_current/result.tsv"
    printf 'frozen_gem5=%s\n' "$frozen_gem5"
    printf 'frozen_api_binary=%s\n' "$frozen_binary"
    printf 'frozen_ramulator=%s\n' "$frozen_ramulator"
    printf 'shared_checkpoint=%s\n' "$checkpoint"
    printf 'shared_selector=%s\n' "$selector"
    printf 'gem5_source_commit=%s\n' "$gem5_source_commit"
    printf 'campaign_source_commit=%s\n' "$campaign_commit"
    printf 'row64_active_line_slots=8192\n'
    printf 'row128_active_line_slots=16384\n'
} > "$out/inputs/provenance.txt"
sha256sum "$frozen_gem5" "$frozen_binary" "$frozen_ramulator" \
    "$selector" "$base_current/result.tsv" \
    > "$out/inputs/frozen_sha256.txt"

common_env=(
    "DX100_SHARED_CHECKPOINT_DIR=$checkpoint"
    "DX100_SHARED_TREATMENT_FILE=$selector"
    "DX100_SHARED_CHECKPOINT_LOG=$checkpoint_log"
    "DX100_FROZEN_RAMULATOR_LIBRARY=$frozen_ramulator"
    "DX100_RAMULATOR_PROVENANCE_FILE=$ramulator_provenance"
    "DX100_GEM5_SOURCE_COMMIT=$gem5_source_commit"
    "MAA_DEBUG_FLAGS=MAAVirtualTrace,MAAMacroEvent,MAAIssueDigest"
    "MAA_REQUIRE_SOURCE_ISSUE_DIGEST=1"
    "MAA_OFFSET_TABLE_ENTRIES=16384"
    "MAA_OFFSET_TABLE_EPOCH_ENTRIES=16384"
    "MAA_ROW_TABLE_SLICES=16"
    "MAA_ROW_TABLE_ROWS_PER_SLICE=128"
    "MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8"
    "MAA_VIRTUAL_INDEX_PARTITIONS=1"
    "MAA_VIRTUAL_INDEX_RANGE_PASSES=0"
    "MAA_VIRTUAL_INDEX_DESCRIPTOR_SPOOL=0"
    "MAA_VIRTUAL_DESCRIPTOR_SPOOL_READ_AHEAD=0"
    "MAA_VIRTUAL_BOUNDED_GLOBAL_MERGE=0"
)

env "${common_env[@]}" "$case_runner" "$frozen_gem5" \
    "$frozen_binary" transparent_4k "$out/current_row128"
env "${common_env[@]}" \
    "DX100_EXTRA_MAA_ARGS_FILE=$out/inputs/strict.args" \
    "$case_runner" "$frozen_gem5" "$frozen_binary" \
    transparent_4k "$out/strict_row128"

python3 - "$base_current" "$out" <<'PY'
import csv
import pathlib
import re
import sys

base = pathlib.Path(sys.argv[1])
root = pathlib.Path(sys.argv[2])


def result(path: pathlib.Path) -> dict[str, str]:
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1:
        raise SystemExit(f"expected one result row in {path}, got {len(rows)}")
    return rows[0]


def require_terminal(case: pathlib.Path) -> None:
    if (case / "restore.exit").read_text().strip() != "0":
        raise SystemExit(f"nonzero restore exit: {case}")
    if not (case / "virtual_tile_consumer_case.pass").is_file():
        raise SystemExit(f"missing case pass marker: {case}")
    log = (case / "restore.log").read_text()
    if log.count("because m5_exit instruction encountered") != 1:
        raise SystemExit(f"expected one m5_exit marker: {case}")
    if log.count("ROI Ended") != 1:
        raise SystemExit(f"expected one ROI end marker: {case}")
    if "VIRTUAL_TILE_CONSUMER_RESULT" not in log or "errors=0" not in log:
        raise SystemExit(f"missing exact benchmark success marker: {case}")
    stats = case / "run" / "stats.txt"
    if not stats.is_file() or stats.stat().st_size == 0:
        raise SystemExit(f"missing nonempty final stats: {case}")


require_terminal(base)
require_terminal(root / "current_row128")
require_terminal(root / "strict_row128")
row64 = result(base / "result.tsv")
current = result(root / "current_row128" / "result.tsv")
strict = result(root / "strict_row128" / "result.tsv")

for name, row, rows, slots in (
    ("current_row64", row64, 64, 8192),
    ("current_row128", current, 128, 16384),
    ("strict_row128", strict, 128, 16384),
):
    if row["row_table_slices"] != "16":
        raise SystemExit(f"{name} did not use 16 active slices")
    if row["row_table_rows_per_slice"] != str(rows):
        raise SystemExit(f"{name} row count mismatch")
    if row["row_table_entries_per_subslice_row"] != "8":
        raise SystemExit(f"{name} entry geometry mismatch")
    actual_slots = 16 * rows * 8
    if actual_slots != slots:
        raise SystemExit(f"{name} line-slot calculation mismatch")
    for field, expected in (
        ("index_words", "16384"),
        ("offset_table_entries", "16384"),
        ("offset_table_epoch_entries", "16384"),
        ("virtual_index_partitions", "1"),
        ("virtual_index_range_passes", "0"),
        ("virtual_index_descriptor_spool", "0"),
        ("virtual_descriptor_spool_read_ahead", "0"),
        ("virtual_bounded_global_merge", "0"),
    ):
        if row[field] != expected:
            raise SystemExit(f"{name} {field}={row[field]} != {expected}")

if int(current["row_table_full_events"]) != 0:
    raise SystemExit("expanded current scheduling still drained the RowTable")
if int(current["offset_epoch_drains"]) != 0:
    raise SystemExit("expanded current scheduling drained the OffsetTable")

zero_mechanisms = (
    "row_table_full_events",
    "offset_epoch_drains",
    "bounded_replay_line_reads",
    "bounded_replay_words",
    "bounded_replay_passes",
    "bounded_replay_drains",
    "descriptor_spool_b_scans",
    "descriptor_spool_resident_descriptors",
    "descriptor_spool_external_descriptors",
    "descriptor_spool_line_writes",
    "descriptor_spool_line_reads",
    "bounded_global_descriptor_records",
    "bounded_global_admissions",
    "bounded_global_retirements",
)
for field in zero_mechanisms:
    if int(strict[field]) != 0:
        raise SystemExit(f"strict recreated forbidden state: {field}={strict[field]}")

for field in (
    "output_hash",
    "index_words",
    "source_issue_records",
    "source_issue_requests",
    "source_issue_sha256",
    "write_issues",
    "write_completions",
    "pages_ready",
    "stream_writes",
):
    if strict[field] != current[field]:
        raise SystemExit(
            f"expanded scheduling semantic mismatch {field}: "
            f"{current[field]} != {strict[field]}"
        )
if strict["output_hash"] != row64["output_hash"]:
    raise SystemExit("strict output hash differs from the certified row64 arm")
if strict["source_issue_sha256"] in ("", "none"):
    raise SystemExit("strict source issue digest is missing")

trace = (root / "strict_row128" / "run" / "virtual_trace.log").read_text()
summaries = [
    line for line in trace.splitlines()
    if "event=strict_two_phase_summary " in line
]
if len(summaries) != 1:
    raise SystemExit(f"expected one strict summary, found {len(summaries)}")
fields = dict(re.findall(r"([A-Za-z_]+)=([^ ]+)", summaries[0]))
for field, expected in (
    ("terminal", "1"),
    ("replay", "0"),
    ("descriptor_backing", "none"),
    ("b_words", "16384"),
    ("descriptor_inserts", "16384"),
    ("pages_ready", "4"),
    ("consumer_event", "1"),
):
    if fields.get(field) != expected:
        raise SystemExit(
            f"strict summary {field}={fields.get(field)} != {expected}"
        )
if int(fields["A_FIRST_ISSUE"]) < int(fields["ROW_OFFSET_LAST_INSERT"]):
    raise SystemExit("A_FIRST_ISSUE < ROW_OFFSET_LAST_INSERT")
if int(fields["a_issues"]) != int(fields["a_responses"]):
    raise SystemExit("strict A issue/response ledger did not close")
if int(fields["backing_issues"]) != int(fields["backing_acks"]):
    raise SystemExit("strict backing issue/ACK ledger did not close")
if int(strict["write_issues"]) != int(strict["write_completions"]):
    raise SystemExit("strict result write ledger did not close")

ticks64 = int(row64["simTicks"])
ticks_current = int(current["simTicks"])
ticks_strict = int(strict["simTicks"])
capacity_speedup = ticks64 / ticks_current
scheduling_speedup = ticks_current / ticks_strict
capacity_delta = ticks_current - ticks64
scheduling_delta = ticks_strict - ticks_current

with (root / "pair.tsv").open("w", newline="") as stream:
    writer = csv.writer(stream, delimiter="\t")
    writer.writerow(
        ("arm", "scheduling", "active_line_slots", "simTicks",
         "output_hash", "source_issue_requests", "source_issue_sha256",
         "row_table_full_events", "offset_epoch_drains")
    )
    writer.writerow(
        ("current_row64", "current", 8192, ticks64,
         row64["output_hash"], row64["source_issue_requests"],
         row64["source_issue_sha256"], row64["row_table_full_events"],
         row64["offset_epoch_drains"])
    )
    writer.writerow(
        ("current_row128", "current", 16384, ticks_current,
         current["output_hash"], current["source_issue_requests"],
         current["source_issue_sha256"], current["row_table_full_events"],
         current["offset_epoch_drains"])
    )
    writer.writerow(
        ("strict_row128", "strict", 16384, ticks_strict,
         strict["output_hash"], strict["source_issue_requests"],
         strict["source_issue_sha256"], strict["row_table_full_events"],
         strict["offset_epoch_drains"])
    )

with (root / "effects.tsv").open("w", newline="") as stream:
    writer = csv.writer(stream, delimiter="\t")
    writer.writerow(("effect", "baseline_ticks", "candidate_ticks",
                     "candidate_minus_baseline_ticks", "speedup"))
    writer.writerow(("capacity_current_row64_to_row128", ticks64,
                     ticks_current, capacity_delta,
                     f"{capacity_speedup:.9f}"))
    writer.writerow(("scheduling_current_to_strict_at_row128", ticks_current,
                     ticks_strict, scheduling_delta,
                     f"{scheduling_speedup:.9f}"))

# Packed lower bound mirrors report_maa_storage.py. The separate semantic C++
# view includes both per-entry bool arrays and 14 bytes of row-level fields.
iteration_bits = 15
entry_bits = 64 + 2 * iteration_bits + 1
row_bits = 64 + 2
active_delta_slots = 8192
active_delta_rows = 1024
allocated_delta_slots = 32768
allocated_delta_rows = 1920
active_packed_bits = (
    active_delta_slots * entry_bits + active_delta_rows * row_bits
)
allocated_packed_bits = (
    allocated_delta_slots * entry_bits + allocated_delta_rows * row_bits
)
active_cpp_bytes = active_delta_slots * 18 + active_delta_rows * 14
allocated_cpp_bytes = (
    allocated_delta_slots * 18 + allocated_delta_rows * 14
)
with (root / "rowtable_cost.tsv").open("w", newline="") as stream:
    writer = csv.writer(stream, delimiter="\t")
    writer.writerow(
        ("scope", "old_line_slots", "new_line_slots", "delta_line_slots",
         "old_rows", "new_rows", "delta_rows", "packed_delta_bits",
         "packed_delta_bytes", "semantic_cpp_delta_bits",
         "semantic_cpp_delta_bytes")
    )
    writer.writerow(
        ("active_fixed_16_slice", 8192, 16384, active_delta_slots,
         1024, 2048, active_delta_rows, active_packed_bits,
         (active_packed_bits + 7) // 8, active_cpp_bytes * 8,
         active_cpp_bytes)
    )
    writer.writerow(
        ("all_four_cpp_organizations_2_4_8_16", 32768, 65536,
         allocated_delta_slots, 1920, 3840, allocated_delta_rows,
         allocated_packed_bits, (allocated_packed_bits + 7) // 8,
         allocated_cpp_bytes * 8, allocated_cpp_bytes)
    )

verdict = "PASS_MICRO"
reasons = []
if ticks_strict > ticks_current:
    verdict = "REJECT_STRICT_REGRESSION"
    reasons.append(
        f"strict_row128={ticks_strict}>current_row128={ticks_current}"
    )
(root / "verdict.txt").write_text(
    f"{verdict} capacity_speedup={capacity_speedup:.9f} "
    f"scheduling_speedup={scheduling_speedup:.9f} "
    f"exact_output_source_ledgers=1 strict_invariants=1 "
    f"candidate_application={'eligible_for_cost_review' if verdict == 'PASS_MICRO' else 'prohibited'}"
    + (" reason=" + ",".join(reasons) if reasons else "")
    + "\n"
)
PY

sha256sum "$base_current/result.tsv" \
    "$out/current_row128/result.tsv" "$out/strict_row128/result.tsv" \
    "$out/pair.tsv" "$out/effects.tsv" "$out/rowtable_cost.tsv" \
    "$out/verdict.txt" > "$out/artifact_sha256.txt"
printf '0\n' > "$out/campaign.exit"
cat "$out/verdict.txt"
