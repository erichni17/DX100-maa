#!/usr/bin/env bash
# Run one exact XRAGE smoke and, by default, its matched bounded-4K arm.
set -euo pipefail

if [[ $# -ne 8 ]]; then
    echo "usage: $0 OUTDIR GEM5_BIN XRAGE_VERIFY_BIN INPUT_JSON RAMULATOR_LIB RAMULATOR_PROVENANCE_JSON SIMULATOR_PROVENANCE_JSON CHECKPOINT_RUN|-" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5_source=$(realpath "$2")
workload_source=$(realpath "$3")
input_source=$(realpath "$4")
ramulator_source=$(realpath "$5")
ramulator_provenance_source=$(realpath "$6")
simulator_provenance_source=$(realpath "$7")
checkpoint_source=$8
run_mode=${XRAGE_RUN_MODE:-pair}
resume=${XRAGE_RESUME:-0}

case "$run_mode" in
    smoke|pair) ;;
    *)
        echo "XRAGE_RUN_MODE must be smoke or pair" >&2
        exit 2
        ;;
esac
[[ $resume == 0 || $resume == 1 ]] || {
    echo "XRAGE_RESUME must be 0 or 1" >&2
    exit 2
}
[[ -x $gem5_source && -x $workload_source && -f $input_source &&
   -f $ramulator_source && -f $ramulator_provenance_source &&
   -f $simulator_provenance_source ]] || {
    echo "missing gem5, XRAGE verifier, input, library, or provenance" >&2
    exit 2
}
if [[ $checkpoint_source != - ]]; then
    checkpoint_source=$(realpath "$checkpoint_source")
    [[ -d $checkpoint_source/checkpoint &&
       -f $checkpoint_source/manifest.txt &&
       -f $checkpoint_source/artifact_sha256.txt &&
       -f $checkpoint_source/checkpoint.command ]] || {
        echo "frozen checkpoint run is incomplete: $checkpoint_source" >&2
        exit 2
    }
fi
[[ ! -e $out || $resume == 1 ]] || {
    echo "refusing to overwrite evidence root: $out" >&2
    exit 2
}
[[ $resume == 0 || -d $out ]] || {
    echo "XRAGE_RESUME requires an existing evidence root: $out" >&2
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

if [[ $resume == 0 ]]; then
    cp --reflink=auto "$gem5_source" "$out/input/gem5.opt"
    cp --reflink=auto "$workload_source" "$out/input/xrage_verify"
    cp --reflink=auto "$input_source" "$out/input/xrage.json"
    cp --reflink=auto "$ramulator_source" "$out/input/libramulator.so"
    cp --reflink=auto "$ramulator_provenance_source" \
        "$out/input/ramulator_provenance.json"
    cp --reflink=auto "$simulator_provenance_source" \
        "$out/input/simulator_provenance.json"
    chmod 0555 "$out/input/gem5.opt" "$out/input/xrage_verify"
fi

gem5="$out/input/gem5.opt"
workload="$out/input/xrage_verify"
input="$out/input/xrage.json"
ramulator="$out/input/libramulator.so"
ramulator_provenance="$out/input/ramulator_provenance.json"
simulator_provenance="$out/input/simulator_provenance.json"
runner_source_commit=$(git -C "$root" rev-parse HEAD)
library_path="$out/input${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

mapfile -t ramulator_identity < <(python3 - "$ramulator_provenance" <<'PY'
import json
import sys

record = json.load(open(sys.argv[1], encoding="utf-8"))
print(record.get("schema", ""))
print(record.get("frozen_library", {}).get("sha256", ""))
print(record.get("elf_build_id", ""))
PY
)
[[ ${ramulator_identity[0]:-} == dx100.ramulator_provenance.v1 &&
   ${ramulator_identity[1]:-} =~ ^[0-9a-f]{64}$ &&
   ${ramulator_identity[1]} == \
   $(sha256sum "$ramulator" | awk '{print $1}') ]] || {
    echo "Ramulator provenance does not authenticate the copied ELF" >&2
    exit 1
}
ramulator_build_id=$(readelf -n "$ramulator" |
    sed -n 's/.*Build ID: //p' | head -1)
[[ -n $ramulator_build_id &&
   $ramulator_build_id == "${ramulator_identity[2]:-}" ]] || {
    echo "Ramulator ELF build ID differs from its provenance" >&2
    exit 1
}

mapfile -t simulator_identity < <(python3 - "$simulator_provenance" <<'PY'
import json
import sys

record = json.load(open(sys.argv[1], encoding="utf-8"))
build = record.get("build", {})
binary = record.get("frozen_binary", {})
status = record.get("source_status", {})
print(record.get("schema", ""))
print(record.get("simulator_source_commit", ""))
print(binary.get("sha256", ""))
print(build.get("command_path", ""))
print(build.get("command_sha256", ""))
print(build.get("log_path", ""))
print(build.get("log_sha256", ""))
print(build.get("exit_code", ""))
print(status.get("path", ""))
print(status.get("sha256", ""))
PY
)
simulator_source_commit=${simulator_identity[1]:-}
simulator_command_source=${simulator_identity[3]:-}
simulator_log_source=${simulator_identity[5]:-}
simulator_status_source=${simulator_identity[8]:-}
[[ ${simulator_identity[0]:-} == dx100.simulator_provenance.v1 &&
   $simulator_source_commit =~ ^[0-9a-f]{40}$ &&
   ${simulator_identity[2]:-} == $(sha256sum "$gem5" | awk '{print $1}') &&
   ${simulator_identity[7]:-} == 0 &&
   -f $simulator_command_source && -f $simulator_log_source &&
   -f $simulator_status_source && ! -s $simulator_status_source &&
   ${simulator_identity[4]:-} == \
   $(sha256sum "$simulator_command_source" | awk '{print $1}') &&
   ${simulator_identity[6]:-} == \
   $(sha256sum "$simulator_log_source" | awk '{print $1}') &&
   ${simulator_identity[9]:-} == \
   $(sha256sum "$simulator_status_source" | awk '{print $1}') ]] || {
    echo "simulator provenance does not authenticate a clean successful build" >&2
    exit 1
}
git -C "$root" cat-file -e "$simulator_source_commit^{commit}" || {
    echo "simulator source commit is unavailable in this repository" >&2
    exit 1
}

if [[ $resume == 0 ]]; then
    cp --reflink=auto "$simulator_command_source" \
        "$out/input/simulator_build.command"
    cp --reflink=auto "$simulator_log_source" \
        "$out/input/simulator_build.log"
    cp --reflink=auto "$simulator_status_source" \
        "$out/input/simulator_source_status.txt"
fi

if [[ $resume == 0 ]]; then
    git -C "$root" status --short > "$out/input/source_status.txt"
    git -C "$root" rev-parse HEAD > "$out/input/runner_source_commit"
    sha256sum "$gem5" "$workload" "$input" "$ramulator" \
        "$ramulator_provenance" "$simulator_provenance" \
        "$out/input/simulator_build.command" \
        "$out/input/simulator_build.log" \
        "$out/input/simulator_source_status.txt" \
        > "$out/input/artifact_sha256.txt"
else
    [[ -f $out/manifest.txt && -f $out/input/artifact_sha256.txt &&
       -z $(<"$out/input/source_status.txt") ]] || {
        echo "resumed XRAGE evidence lacks a clean frozen manifest" >&2
        exit 1
    }
    sha256sum --status -c "$out/input/artifact_sha256.txt" || {
        echo "resumed XRAGE input artifacts changed" >&2
        exit 1
    }
    recorded_checkpoint=$(sed -n 's/^checkpoint_source=//p' \
        "$out/manifest.txt")
    [[ $recorded_checkpoint == "$checkpoint_source" ]] || {
        echo "resumed XRAGE checkpoint differs from the frozen manifest" >&2
        exit 1
    }
    {
        printf 'resume_runner_source_commit=%s\n' "$runner_source_commit"
        printf 'resumed_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } > "$out/resume_manifest.txt"
fi
LD_LIBRARY_PATH="$library_path" ldd "$gem5" > "$out/input/gem5.ldd.txt"
loaded_ramulator=$(awk '$1 == "libramulator.so" { print $3 }' \
    "$out/input/gem5.ldd.txt")
[[ -n $loaded_ramulator && $(realpath "$loaded_ramulator") == \
    "$ramulator" ]] || {
    echo "frozen gem5 did not resolve the frozen Ramulator library" >&2
    exit 1
}

if [[ $resume == 0 ]]; then
{
    printf 'simulator_source_commit=%s\n' "$simulator_source_commit"
    printf 'runner_source_commit=%s\n' "$runner_source_commit"
    printf 'run_mode=%s\n' "$run_mode"
    printf 'workload=xrage_gather0\n'
    printf 'comparison=4k_physical_full_metadata_vs_4k_physical_bounded_metadata\n'
    printf 'correctness=exact_integer_output_hash\n'
    printf 'logical_tile_elements=16384\n'
    printf 'physical_tile_elements=4096\n'
    printf 'shared_checkpoint=pre_maa_atomic\n'
    printf 'checkpoint_source=%s\n' "$checkpoint_source"
    printf 'full_metadata=row_16x64_offset_16384_epoch_16384\n'
    printf 'bounded_metadata=row_16x32_offset_4096_epoch_4096\n'
    printf 'bounded_schedule=four_modulo_passes_finite_filter_16_words_per_cycle\n'
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$out/manifest.txt"
fi
sha256sum \
    "$root/experiments/scripts/run_xrage_virtual_case.sh" \
    "$root/experiments/scripts/run_xrage_direct_index_smoke.sh" \
    "$root/experiments/scripts/recover_xrage_checkpoint.sh" \
    "$root/experiments/scripts/verify_xrage_checkpoint_attestation.py" \
    "$root/configs/deprecated/example/se.py" \
    "$root/ext/ramulator2/ramulator2/example_gem5_config.yaml" \
    > "$out/$([[ $resume == 1 ]] && echo resume_runner_sha256.txt || \
        echo runner_sha256.txt)"

common=(
    XRAGE_SIMULATOR_SOURCE_COMMIT="$simulator_source_commit"
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

# A reused checkpoint must contain the same guest ABI and input bytes.  The
# input path itself is retained because the recovery guard compares the
# pre-checkpoint command, even though the input has already been parsed.
runtime_input=$input
if [[ $checkpoint_source != - ]]; then
    checkpoint_input=$(sed -n 's/^input=//p' \
        "$checkpoint_source/manifest.txt")
    [[ -f $checkpoint_input &&
       $(sha256sum "$checkpoint_input" | awk '{print $1}') == \
       $(sha256sum "$input" | awk '{print $1}') ]] || {
        echo "frozen checkpoint input does not match the requested XRAGE input" >&2
        exit 1
    }
    workload_hash=$(sha256sum "$workload" | awk '{print $1}')
    grep -Eq "^${workload_hash}  " \
        "$checkpoint_source/artifact_sha256.txt" || {
        echo "XRAGE verifier binary does not match the frozen checkpoint ABI" >&2
        exit 1
    }
    runtime_input=$checkpoint_input
    shared_checkpoint_run=$checkpoint_source
else
    shared_checkpoint_run=$out/full_metadata
fi

# The first exact verifier restore is both the smoke and the full-metadata arm.
full_environment=(
    "${common[@]}"
    MAA_ROW_TABLE_ROWS_PER_SLICE=64
    MAA_NUM_OFFSET_TABLE_ENTRIES=16384
    MAA_NUM_OFFSET_TABLE_EPOCH_ENTRIES=16384
    MAA_VIRTUAL_INDEX_PARTITIONS=1
    MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE=0
    MAA_VIRTUAL_PARTITION_KEEP_COMBINER=0
)
if [[ $resume == 1 ]]; then
    [[ -f $out/full_metadata/xrage_checkpoint_recovery.pass &&
       $(<"$out/full_metadata/restore.exit") == 0 &&
       -f $out/full_metadata/result.tsv ]] || {
        echo "resumed XRAGE full-metadata smoke is incomplete" >&2
        exit 1
    }
    sha256sum --status -c "$out/full_metadata/artifact_sha256.txt" || {
        echo "resumed XRAGE full-metadata artifacts changed" >&2
        exit 1
    }
    full_log="$out/full_metadata/restore.log"
    [[ $(grep -c '^MAA_GATHER_VERIFY_PASS ' "$full_log") -eq 1 &&
       $(grep -c 'because m5_exit instruction encountered' "$full_log") -eq 1 ]] || {
        echo "resumed XRAGE full-metadata completion markers are invalid" >&2
        exit 1
    }
    ! grep -Eqi 'panic|fatal|segmentation fault|MAA_GATHER_VERIFY_FAIL' \
        "$full_log" || {
        echo "resumed XRAGE full-metadata log contains a failure marker" >&2
        exit 1
    }
elif [[ $checkpoint_source == - ]]; then
    env LD_LIBRARY_PATH="$library_path" "${full_environment[@]}" \
        "$root/experiments/scripts/run_xrage_direct_index_smoke.sh" \
        "$gem5" "$workload" "$runtime_input" "$out/full_metadata" \
        > "$out/full_metadata.launch.log" 2>&1
else
    env LD_LIBRARY_PATH="$library_path" "${full_environment[@]}" \
        XRAGE_ALLOW_PRE_MAA_RETARGET=1 \
        "$root/experiments/scripts/recover_xrage_checkpoint.sh" \
        "$gem5" "$workload" "$runtime_input" "$shared_checkpoint_run" \
        "$out/full_metadata" > "$out/full_metadata.launch.log" 2>&1
fi
printf '0\n' > "$out/full_metadata.launch.exit"

checkpoint_command=$(<"$shared_checkpoint_run/checkpoint.command")
[[ $checkpoint_command == *"--cpu-type AtomicSimpleCPU"* &&
   ! $checkpoint_command =~ (^|[[:space:]])--maa($|[[:space:]]) &&
   $checkpoint_command != *"--maa_num_"* &&
   $checkpoint_command != *"--maa_physical_"* ]] || {
    echo "XRAGE checkpoint is not treatment-neutral" >&2
    exit 1
}
(
    cd "$shared_checkpoint_run/checkpoint"
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
        "$gem5" "$workload" "$runtime_input" "$shared_checkpoint_run" \
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
    grep -Fqx "checkpoint_run=$shared_checkpoint_run" \
        "$out/full_metadata/manifest.txt" 2>/dev/null ||
        [[ $checkpoint_source == - ]]
    grep -Fqx "checkpoint_run=$shared_checkpoint_run" \
        "$out/bounded_4k/manifest.txt" || {
        echo "bounded arm did not attest the shared checkpoint" >&2
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
