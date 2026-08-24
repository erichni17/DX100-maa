#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "usage: $0 GEM5_BIN API_BINARY OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
binary=$(realpath "$2")
out=$(realpath -m "$3")
case_runner="$root/experiments/scripts/run_virtual_tile_consumer_case.sh"
ramulator="$root/ext/ramulator2/ramulator2/libramulator.so"
config="$root/configs/deprecated/example/se.py"

[[ -x $gem5 && -x $binary && -f $ramulator ]] || {
    echo "missing executable gem5/API binary or Ramulator library" >&2
    exit 2
}
[[ ! -e $out ]] || {
    echo "refusing to overwrite output: $out" >&2
    exit 2
}
[[ -z $(git -C "$root" status --short) ]] || {
    echo "strict A/B requires a clean committed source tree" >&2
    exit 1
}

mkdir -p "$out/inputs" "$out/checkpoint"
cp -- "$gem5" "$out/inputs/gem5.opt"
cp -- "$binary" "$out/inputs/api_binary"
cp -- "$ramulator" "$out/inputs/libramulator.so"
chmod 0555 "$out/inputs/gem5.opt" "$out/inputs/api_binary"
sha256sum "$out/inputs/libramulator.so" > "$out/inputs/ramulator.sha256"
printf 'transparent 4096\n' > "$out/inputs/treatment.txt"
printf '%s\n' '--maa_virtual_strict_two_phase' > "$out/inputs/strict.args"

frozen_gem5="$out/inputs/gem5.opt"
frozen_binary="$out/inputs/api_binary"
frozen_ramulator="$out/inputs/libramulator.so"
selector="$out/inputs/treatment.txt"
checkpoint="$out/checkpoint/gem5"
checkpoint_log="$out/checkpoint/checkpoint.log"
ramulator_dir=$(dirname "$frozen_ramulator")

set +e
LD_LIBRARY_PATH="$ramulator_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$frozen_gem5" --listener-mode=off --outdir="$checkpoint" \
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
    --max-checkpoints=1 --cmd "$frozen_binary" \
    --options "deferred $selector" > "$checkpoint_log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/checkpoint/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]] || {
    echo "shared checkpoint failed with rc=$checkpoint_rc" >&2
    exit 1
}
grep -Fq 'VIRTUAL_TILE_CONSUMER_LAYOUT mode=deferred page_elements=0' \
    "$checkpoint_log" || {
    echo "shared checkpoint is not the deferred API checkpoint" >&2
    exit 1
}

common_env=(
    "DX100_SHARED_CHECKPOINT_DIR=$checkpoint"
    "DX100_SHARED_TREATMENT_FILE=$selector"
    "DX100_SHARED_CHECKPOINT_LOG=$checkpoint_log"
    "DX100_FROZEN_RAMULATOR_LIBRARY=$frozen_ramulator"
    "DX100_RAMULATOR_PROVENANCE_FILE=$out/inputs/ramulator.sha256"
    "DX100_GEM5_SOURCE_COMMIT=$(git -C "$root" rev-parse HEAD)"
    "MAA_DEBUG_FLAGS=MAAVirtualTrace,MAAMacroEvent,MAAIssueDigest"
    "MAA_REQUIRE_SOURCE_ISSUE_DIGEST=1"
    "MAA_OFFSET_TABLE_ENTRIES=16384"
    "MAA_OFFSET_TABLE_EPOCH_ENTRIES=16384"
    "MAA_ROW_TABLE_SLICES=16"
    "MAA_VIRTUAL_INDEX_PARTITIONS=1"
    "MAA_VIRTUAL_INDEX_RANGE_PASSES=0"
    "MAA_VIRTUAL_INDEX_DESCRIPTOR_SPOOL=0"
    "MAA_VIRTUAL_BOUNDED_GLOBAL_MERGE=0"
)

env "${common_env[@]}" "$case_runner" "$frozen_gem5" \
    "$frozen_binary" transparent_4k "$out/current"
set +e
env "${common_env[@]}" \
    "DX100_EXTRA_MAA_ARGS_FILE=$out/inputs/strict.args" \
    "$case_runner" "$frozen_gem5" "$frozen_binary" \
    transparent_4k "$out/strict"
strict_rc=$?
set -e

if [[ $strict_rc -ne 0 ]]; then
    strict_log="$out/strict/restore.log"
    if grep -Eq \
        'strict two-phase (physical RowTable exposes only|cannot retain all)' \
        "$strict_log"; then
        python3 - "$out" "$strict_rc" <<'PY'
import csv
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
strict_rc = int(sys.argv[2])
with (root / "current" / "result.tsv").open(newline="") as stream:
    current = next(csv.DictReader(stream, delimiter="\t"))
log = (root / "strict" / "restore.log").read_text()
match = re.search(
    r"strict two-phase physical RowTable exposes only (\d+) line slots "
    r"for (\d+) descriptors",
    log,
)
if match is None:
    match = re.search(
        r"strict two-phase cannot retain all (\d+) descriptors", log
    )
    physical_slots = "runtime_distribution_failure"
    logical = match.group(1) if match else "unknown"
else:
    physical_slots, logical = match.groups()
with (root / "pair.tsv").open("w", newline="") as stream:
    writer = csv.writer(stream, delimiter="\t")
    writer.writerow(
        ("arm", "status", "simTicks", "output_hash", "index_words",
         "row_table_full_events", "physical_row_line_slots", "logical")
    )
    writer.writerow(
        ("current", "complete", current["simTicks"],
         current["output_hash"], current["index_words"],
         current["row_table_full_events"], "n/a", 16384)
    )
    writer.writerow(
        ("strict", "reject_capacity", "n/a", "n/a", 0, 0,
         physical_slots, logical)
    )
(root / "verdict.txt").write_text(
    "REJECT_CAPACITY strict_rc=" + str(strict_rc)
    + " physical_row_line_slots=" + physical_slots
    + " logical_descriptors=" + logical
    + " candidate_launch=prohibited\n"
)
(root / "campaign.exit").write_text("0\n")
PY
        sha256sum "$frozen_gem5" "$frozen_binary" "$selector" \
            "$out/current/result.tsv" "$strict_log" "$out/pair.tsv" \
            > "$out/artifact_sha256.txt"
        cat "$out/verdict.txt"
        exit 0
    fi
    echo "strict restore failed without a capacity classification" >&2
    exit "$strict_rc"
fi

python3 - "$out" <<'PY'
import csv
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])

def row(arm):
    with (root / arm / "result.tsv").open(newline="") as stream:
        return next(csv.DictReader(stream, delimiter="\t"))

current = row("current")
strict = row("strict")
exact = (
    "output_hash", "simInsts", "index_words", "source_issue_requests",
    "source_issue_sha256", "write_issues", "write_completions",
    "pages_ready", "stream_writes",
)
for field in exact:
    if current[field] != strict[field]:
        raise SystemExit(f"semantic ledger mismatch {field}: "
                         f"{current[field]} != {strict[field]}")
for field in (
    "descriptor_spool_b_scans", "descriptor_spool_line_writes",
    "descriptor_spool_line_reads", "bounded_replay_line_reads",
    "bounded_replay_words", "bounded_replay_passes",
):
    if int(strict[field]) != 0:
        raise SystemExit(f"strict mode recreated replay: {field}={strict[field]}")

trace = (root / "strict" / "run" / "virtual_trace.log").read_text()
summaries = [line for line in trace.splitlines()
             if "event=strict_two_phase_summary " in line]
if len(summaries) != 1:
    raise SystemExit(f"expected one strict summary, found {len(summaries)}")
fields = dict(re.findall(r"([a-z_]+)=([^ ]+)", summaries[0]))
if fields.get("terminal") != "1" or fields.get("replay") != "0":
    raise SystemExit("strict terminal/replay ledger is invalid")
if int(fields["b_words"]) != 16384 or int(fields["descriptor_inserts"]) != 16384:
    raise SystemExit("strict mode did not admit all 16K descriptors")
if int(fields["a_first_issue_tick"]) < int(fields["row_offset_last_insert_tick"]):
    raise SystemExit("A_FIRST_ISSUE < ROW_OFFSET_LAST_INSERT")
if int(fields["a_issues"]) != int(fields["a_responses"]):
    raise SystemExit("strict A issue/response mismatch")
if int(fields["backing_issues"]) != int(fields["backing_acks"]):
    raise SystemExit("strict backing issue/ACK mismatch")

current_ticks = int(current["simTicks"])
strict_ticks = int(strict["simTicks"])
if strict_ticks > current_ticks:
    raise SystemExit(
        f"strict reference regressed: {strict_ticks} > {current_ticks} simTicks"
    )

with (root / "pair.tsv").open("w", newline="") as stream:
    writer = csv.writer(stream, delimiter="\t")
    writer.writerow(("arm", "simTicks", *exact))
    writer.writerow(("current", current_ticks, *(current[x] for x in exact)))
    writer.writerow(("strict", strict_ticks, *(strict[x] for x in exact)))
(root / "verdict.txt").write_text(
    f"PASS current={current_ticks} strict={strict_ticks} "
    "exact_output_and_ledgers=1 replay=0 invariant=1\n"
)
PY

sha256sum "$frozen_gem5" "$frozen_binary" "$selector" \
    "$out/current/result.tsv" "$out/strict/result.tsv" "$out/pair.tsv" \
    > "$out/artifact_sha256.txt"
printf '0\n' > "$out/campaign.exit"
cat "$out/verdict.txt"
