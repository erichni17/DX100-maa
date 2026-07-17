#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 8 ]]; then
    cat >&2 <<EOF
usage: $0 GEM5_BIN OUTDIR SOURCE NATIVE_BIN VIRTUAL_BIN N EXPECTED_HASH EXPECTED_ELEMENTS
EOF
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
campaign=$(realpath -m "$2")
source_file=$(realpath "$3")
native=$(realpath "$4")
virtual=$(realpath "$5")
n=$6
expected_hash=$7
expected_elements=$8

[[ $n =~ ^[1-9][0-9]*$ && $expected_hash =~ ^[0-9]+$ && \
   $expected_elements =~ ^[1-9][0-9]*$ ]] || {
    echo "N, EXPECTED_HASH, and EXPECTED_ELEMENTS must be positive integers" >&2
    exit 2
}

config=$root/configs/deprecated/example/se.py
ramulator=$root/ext/ramulator2/ramulator2/example_gem5_config.yaml
runner=$(realpath "$0")
mkdir -p "$campaign"
exec > >(tee -a "$campaign/controller.log") 2>&1

sha256sum "$gem5" "$config" "$ramulator" "$native" "$virtual" \
    "$root/benchmarks/API/MAA_gem5.hpp" "$source_file" \
    "$root/benchmarks/UME/Makefile" "$runner" \
    > "$campaign/artifact_sha256.txt"
{
    printf 'simulator_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'simulator_sha256=%s\n' "$(sha256sum "$gem5" | cut -d' ' -f1)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'n=%s\n' "$n"
    printf 'expected_hash=%s\n' "$expected_hash"
    printf 'expected_elements=%s\n' "$expected_elements"
    printf '%s\n' 'oracle_policy=predeclared scalar hash and exact final reference'
    printf '%s\n' 'timing_policy=first ROI stats; fingerprint and reference are out of ROI'
} > "$campaign/source.txt"
export LD_LIBRARY_PATH="$root/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"

write_command() {
    local output=$1
    shift
    printf '%q ' "$@" > "$output"
    printf '\n' >> "$output"
}

checkpoint_one() {
    local arm=$1
    local binary=$2
    local out=$campaign/checkpoints/$arm
    rm -rf "$out"
    mkdir -p "$out"
    local -a command=(
        timeout 21600 "$gem5" --listener-mode=off --outdir="$out"
        "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB
        --max-checkpoints=1 --cmd "$binary" --options "$n"
    )
    write_command "$out/checkpoint.command" "${command[@]}"
    echo "[$(date -Is)] checkpoint start: $arm"
    set +e
    "${command[@]}" > "$out/checkpoint.log" 2>&1
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$out/checkpoint.exit"
    [[ $rc -eq 0 ]] || return 1
    compgen -G "$out/cpt.*" >/dev/null
    sha256sum "$out"/cpt.*/* > "$out/checkpoint_sha256.txt"
    echo "[$(date -Is)] checkpoint complete: $arm"
}

checkpoint_one native "$native" & native_checkpoint_pid=$!
checkpoint_one virtual "$virtual" & virtual_checkpoint_pid=$!
checkpoint_status=0
wait "$native_checkpoint_pid" || checkpoint_status=1
wait "$virtual_checkpoint_pid" || checkpoint_status=1
[[ $checkpoint_status -eq 0 ]] || exit 10

common=(
    "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --sys-clock 3.2GHz --cpu-clock 3.2GHz --caches --l1d_size=32kB
    --l1d_assoc=8 --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16
    --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8
    --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16 --l1i_write_buffers=8
    --l2cache --l2_size=256kB --l2_assoc=4
    --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16
    --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256
    --l3_write_buffers=128 --l3_ports=4 --cacheline_size=64
    --mem-type Ramulator2 --ramulator-config "$ramulator" --mem-channels=2
    --maa_ncbus_width=32 --maa --maa_num_maas=1
    --maa_num_tile_elements=16384 --maa_l2_uncacheable --maa_l3_uncacheable
    --maa_num_initial_row_table_slices=32
)
virtual_args=(
    --maa_virtual_combine_slots=384
    --maa_virtual_combine_words=4096
    --maa_virtual_combine_ways=4
    --maa_virtual_combine_banks=4
    --maa_virtual_response_slots=96
    --maa_virtual_response_word_pool=480
    --maa_virtual_words_per_cycle=4
    --maa_virtual_max_outstanding_writes=64
    --maa_virtual_masked_writes
)

extract_first_stats() {
    awk '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 == "simTicks" { ticks=$2 }
        section == 1 && $1 == "system.maa.cycles_TOTAL" { cycles=$2 }
        section == 1 && $1 ~ /^system\.maa\.I[0-9]+_IND_VirtWriteIssues$/ {
            issues += $2
        }
        section == 1 && $1 ~ /^system\.maa\.I[0-9]+_IND_VirtWriteCompletions$/ {
            completions += $2
        }
        /^---------- End Simulation Statistics/ && section == 1 {
            printf "%s\t%s\t%s\t%s\n", ticks, cycles,
                issues + 0, completions + 0
            exit
        }
    ' "$1"
}

restore_one() {
    local arm=$1
    local replica=$2
    local binary=$3
    shift 3
    local out=$campaign/runs/$arm/replica_$replica
    rm -rf "$out"
    mkdir -p "$out"
    cp -a --reflink=auto "$campaign/checkpoints/$arm"/cpt.* "$out"/
    local -a command=(
        timeout 172800 "$gem5" --listener-mode=off --outdir="$out"
        "${common[@]}" "$@" --cmd "$binary" --options "$n"
    )
    write_command "$out/restore.command" "${command[@]}"
    echo "[$(date -Is)] restore start: $arm replica=$replica"
    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
        /usr/bin/time -f 'restore_wall=%e restore_rss_kb=%M' \
        "${command[@]}" > "$out/restore.log" 2>&1
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$out/restore.exit"

    local roi fatal fp_count reference_count ticks cycles issues completions
    local valid=1
    roi=$(grep -Fxc 'ROI Ended' "$out/restore.log" || true)
    fatal=$(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
        "$out/restore.log" || true)
    fp_count=$(grep -Fxc \
        "UME_OUTPUT_FP output_hash=$expected_hash nonfinite=0" \
        "$out/restore.log" || true)
    reference_count=$(grep -Fxc \
        "UME_REFERENCE_PASS point_volume_errors=0 point_gradient_errors=0 elements=$expected_elements" \
        "$out/restore.log" || true)
    read -r ticks cycles issues completions \
        < <(extract_first_stats "$out/stats.txt")
    [[ $rc -eq 0 && $roi -eq 1 && $fatal -eq 0 && $fp_count -eq 1 && \
       $reference_count -eq 1 && -n $ticks && -n $cycles ]] || valid=0
    if [[ $arm == virtual ]]; then
        [[ $issues -gt 0 && $issues -eq $completions ]] || valid=0
    else
        [[ $issues -eq 0 && $completions -eq 0 ]] || valid=0
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$arm" "$replica" "$rc" "$ticks" "$cycles" "$issues" \
        "$completions" "$expected_hash" "$fatal" "$valid" \
        > "$out/result.tsv"
    [[ $valid -eq 1 ]] || {
        echo "restore invalid: $arm replica=$replica rc=$rc" >&2
        return 1
    }
    echo "[$(date -Is)] restore complete: $arm replica=$replica ticks=$ticks"
}

run_phase() {
    local arm=$1
    local binary=$2
    shift 2
    local status=0
    local -a pids=()
    for replica in 1 2 3; do
        restore_one "$arm" "$replica" "$binary" "$@" &
        pids+=("$!")
    done
    for pid in "${pids[@]}"; do
        wait "$pid" || status=1
    done
    [[ $status -eq 0 ]]
    mapfile -t unique_ticks \
        < <(cut -f4 "$campaign"/runs/$arm/*/result.tsv | sort -u)
    [[ ${#unique_ticks[@]} -eq 1 ]]
}

# Native completes first so virtual execution cannot influence its control.
run_phase native "$native"
run_phase virtual "$virtual" "${virtual_args[@]}"

printf 'arm\treplica\trc\tsim_ticks\tmaa_cycles\twrite_issues\twrite_completions\toutput_hash\tfatal_count\tvalid\n' \
    > "$campaign/results.tsv"
cat "$campaign"/runs/{native,virtual}/*/result.tsv >> "$campaign/results.tsv"
: > "$campaign/campaign.pass"
cat "$campaign/results.tsv"
