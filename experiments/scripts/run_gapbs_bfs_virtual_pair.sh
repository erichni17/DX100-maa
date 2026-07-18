#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 6 ]]; then
    cat >&2 <<EOF
usage: $0 GEM5_BIN NATIVE_BINARY VIRTUAL_BINARY GRAPH ORACLE_JSON OUTDIR
EOF
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
native=$(realpath "$2")
virtual=$(realpath "$3")
graph=$(realpath "$4")
oracle=$(realpath "$5")
campaign=$(realpath -m "$6")
config=$root/configs/deprecated/example/se.py
ramulator=$root/ext/ramulator2/ramulator2/example_gem5_config.yaml
runner=$(realpath "$0")
checkpoint_timeout=${CHECKPOINT_TIMEOUT:-21600}
restore_timeout=${RESTORE_TIMEOUT:-172800}
replicas=3

for path in "$gem5" "$native" "$virtual" "$graph" "$oracle" \
            "$config" "$ramulator" "$runner"; do
    [[ -f $path ]] || { echo "missing artifact: $path" >&2; exit 3; }
done
[[ -x $gem5 && -x $native && -x $virtual ]] || {
    echo "simulator and benchmark binaries must be executable" >&2
    exit 3
}
command -v jq >/dev/null || { echo "jq is required" >&2; exit 3; }
[[ ! -e $campaign ]] || {
    echo "campaign output already exists; choose a new path: $campaign" >&2
    exit 2
}

expected_certificate=$(jq -er '.expected_certificate.text' "$oracle")
expected_certificate_sha=$(jq -er '.expected_certificate.sha256' "$oracle")
expected_graph_sha=$(jq -er '.workload.graph_sha256' "$oracle")
expected_source_sha=$(jq -er '.benchmark.source_sha256' "$oracle")
expected_source_commit=$(jq -er '.benchmark.source_commit' "$oracle")
actual_certificate_sha=$(
    printf '%s\n' "$expected_certificate" | sha256sum | cut -d' ' -f1
)
actual_graph_sha=$(sha256sum "$graph" | cut -d' ' -f1)
actual_source_sha=$(
    sha256sum "$root/benchmarks/gapbs/src/bfs.cc" | cut -d' ' -f1
)
[[ $expected_certificate_sha =~ ^[0-9a-f]{64}$ &&
   $expected_graph_sha =~ ^[0-9a-f]{64}$ &&
   $expected_source_sha =~ ^[0-9a-f]{64}$ &&
   $expected_source_commit =~ ^[0-9a-f]{40}$ ]] || {
    echo "oracle contains malformed hashes" >&2
    exit 3
}
[[ $actual_certificate_sha == "$expected_certificate_sha" ]] || {
    echo "oracle certificate hash mismatch" >&2
    exit 3
}
[[ $actual_graph_sha == "$expected_graph_sha" ]] || {
    echo "graph does not match frozen oracle" >&2
    exit 3
}
[[ $actual_source_sha == "$expected_source_sha" ]] || {
    echo "BFS source does not match frozen oracle" >&2
    exit 3
}
git -C "$root" merge-base --is-ancestor "$expected_source_commit" HEAD || {
    echo "frozen BFS source commit is not in the current history" >&2
    exit 3
}

mkdir -p "$campaign/checkpoints" "$campaign/runs"
finish_campaign() {
    local rc=$?
    trap - EXIT
    if [[ $rc -ne 0 ]]; then
        rm -f "$campaign/campaign.pass"
        printf '%s\n' "$rc" > "$campaign/campaign.fail"
    fi
    exit "$rc"
}
trap finish_campaign EXIT
exec > >(tee "$campaign/controller.log") 2>&1

printf '%s\n' "$expected_certificate" > \
    "$campaign/expected_certificate.txt"
sha256sum "$gem5" "$native" "$virtual" "$graph" "$oracle" "$config" \
    "$ramulator" "$runner" "$root/benchmarks/gapbs/src/bfs.cc" \
    "$root/benchmarks/gapbs/Makefile" > "$campaign/artifact_sha256.txt"
{
    printf 'simulator_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'simulator_sha256=%s\n' "$(sha256sum "$gem5" | cut -d' ' -f1)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'replicas_per_arm=%s\n' "$replicas"
    printf 'oracle_sha256=%s\n' "$(sha256sum "$oracle" | cut -d' ' -f1)"
    printf '%s\n' 'acceptance=exact frozen certificate, deterministic replicas, and balanced virtual writes'
} > "$campaign/source.txt"
export LD_LIBRARY_PATH="$root/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"

write_command() {
    local output=$1
    shift
    printf '%q ' "$@" > "$output"
    printf '\n' >> "$output"
}

checkpoint_one() {
    local arm=$1 binary=$2 out=$campaign/checkpoints/$1
    mkdir -p "$out"
    local -a command=(
        timeout "$checkpoint_timeout" "$gem5" --listener-mode=off
        --outdir="$out" "$config" --cpu-type AtomicSimpleCPU -n 4
        --mem-size 2GB --max-checkpoints=1 --cmd "$binary"
        --options "-f $graph -n 1"
    )
    write_command "$out/checkpoint.command" "${command[@]}"
    echo "[$(date -Is)] checkpoint start: $arm"
    set +e
    "${command[@]}" > "$out/checkpoint.log" 2>&1
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$out/checkpoint.exit"
    [[ $rc -eq 0 ]]
    mapfile -t checkpoints < <(
        find "$out" -mindepth 2 -maxdepth 2 -type f -name m5.cpt \
            -printf '%h\n' | sort -u
    )
    [[ ${#checkpoints[@]} -eq 1 ]]
    sha256sum "${checkpoints[0]}"/* > "$out/checkpoint_sha256.txt"
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
    --sys-clock 3.2GHz --cpu-clock 3.2GHz --caches
    --l1d_size=32kB --l1d_assoc=8 --l1d-hwp-type=StridePrefetcher
    --l1d_mshrs=16 --l1d_write_buffers=8
    --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher
    --l1i_mshrs=16 --l1i_write_buffers=8
    --l2cache --l2_size=256kB --l2_assoc=4
    --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16
    --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256
    --l3_write_buffers=128 --l3_ports=4 --cacheline_size=64
    --mem-type Ramulator2 --ramulator-config "$ramulator" --mem-channels=2
    --maa_ncbus_width=32 --maa --maa_num_maas=1
    --maa_num_tile_elements=16384 --maa_l2_uncacheable --maa_l3_uncacheable
    --maa_num_initial_row_table_slices=32
)
virtual_options=(
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
        section == 1 && $1 == "simTicks" { ticks=$2; ticks_seen++ }
        section == 1 && $1 == "system.maa.cycles_TOTAL" {
            cycles=$2; cycles_seen++
        }
        section == 1 && $1 == "system.maa.numInst_INDRD" {
            indrd=$2; indrd_seen++
        }
        section == 1 && $1 == "system.maa.cpu_spd_data_read_deferrals" {
            deferrals=$2; deferrals_seen++
        }
        section == 1 && $1 == "system.maa.cpu_spd_data_read_retry_signals" {
            signals=$2; signals_seen++
        }
        section == 1 && $1 == "system.maa.cpu_spd_data_read_retry_attempts" {
            attempts=$2; attempts_seen++
        }
        section == 1 &&
            $1 == "system.maa.cpu_spd_data_read_retry_acceptances" {
            acceptances=$2; acceptances_seen++
        }
        section == 1 && $1 ~ /^system\.maa\.I[0-9]+_IND_VirtWriteIssues$/ {
            issues += $2; issues_seen++
        }
        section == 1 && $1 ~ /^system\.maa\.I[0-9]+_IND_VirtWriteCompletions$/ {
            completions += $2; completions_seen++
        }
        /^---------- End Simulation Statistics/ && section == 1 {
            if (ticks_seen != 1 || cycles_seen != 1 || indrd_seen != 1 ||
                deferrals_seen != 1 || signals_seen != 1 ||
                attempts_seen != 1 || acceptances_seen != 1 ||
                issues_seen == 0 ||
                completions_seen == 0 || ticks !~ /^[0-9]+$/ ||
                cycles !~ /^[0-9]+$/ || indrd !~ /^[0-9]+$/ ||
                deferrals !~ /^[0-9]+$/ || signals !~ /^[0-9]+$/ ||
                attempts !~ /^[0-9]+$/ || acceptances !~ /^[0-9]+$/)
                exit 2
            printf "%s\n%s\n%s\n%s\n%s\n%s\n%s\n%.0f\n%.0f\n",
                ticks, cycles, indrd, deferrals, signals, attempts,
                acceptances, issues, completions
            emitted=1
            exit 0
        }
        END { if (!emitted) exit 2 }
    ' "$1"
}

restore_one() {
    local arm=$1 replica=$2 binary=$3
    shift 3
    local out=$campaign/runs/$arm/replica_$replica
    mkdir -p "$out"
    mapfile -t checkpoints < <(
        find "$campaign/checkpoints/$arm" -mindepth 2 -maxdepth 2 \
            -type f -name m5.cpt -printf '%h\n' | sort -u
    )
    [[ ${#checkpoints[@]} -eq 1 ]]
    cp -a --reflink=auto "${checkpoints[0]}" "$out/"
    local -a command=(
        timeout "$restore_timeout" "$gem5" --listener-mode=off
        --outdir="$out" "${common[@]}" "$@" --cmd "$binary"
        --options "-f $graph -n 1"
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

    local roi exits fatal certificate_lines certificate_matches
    local stats_blob ticks=NA cycles=NA indrd=NA deferrals=NA signals=NA
    local attempts=NA acceptances=NA
    local issues=NA completions=NA valid=1
    local -a stats_fields=()
    roi=$(grep -Fxc 'ROI End!!!' "$out/restore.log" || true)
    exits=$(grep -Fc 'Exiting @ tick' "$out/restore.log" || true)
    fatal=$(grep -Eic \
        'panic|fatal|assert|abort|segmentation fault|error:' \
        "$out/restore.log" || true)
    certificate_lines=$(grep -Ec '^BFS_FP ' "$out/restore.log" || true)
    certificate_matches=$(grep -Fxc "$expected_certificate" \
        "$out/restore.log" || true)
    [[ $certificate_lines -eq 1 && $certificate_matches -eq 1 ]] || valid=0
    if stats_blob=$(extract_first_stats "$out/stats.txt"); then
        mapfile -t stats_fields <<< "$stats_blob"
        if [[ ${#stats_fields[@]} -eq 9 ]]; then
            ticks=${stats_fields[0]}
            cycles=${stats_fields[1]}
            indrd=${stats_fields[2]}
            deferrals=${stats_fields[3]}
            signals=${stats_fields[4]}
            attempts=${stats_fields[5]}
            acceptances=${stats_fields[6]}
            issues=${stats_fields[7]}
            completions=${stats_fields[8]}
        else
            valid=0
        fi
    else
        valid=0
    fi
    [[ $rc -eq 0 && $roi -eq 1 && $exits -eq 1 && $fatal -eq 0 ]] || \
        valid=0
    grep -Fq 'because m5_exit instruction encountered' \
        "$out/restore.log" || valid=0
    [[ $ticks =~ ^[1-9][0-9]*$ && $cycles =~ ^[1-9][0-9]*$ &&
       $indrd =~ ^[1-9][0-9]*$ && $deferrals =~ ^[0-9]+$ &&
       $signals =~ ^[0-9]+$ && $attempts =~ ^[0-9]+$ &&
       $acceptances =~ ^[0-9]+$ &&
       $issues =~ ^[0-9]+$ && $completions =~ ^[0-9]+$ ]] || valid=0
    if [[ $deferrals =~ ^[0-9]+$ && $signals =~ ^[0-9]+$ &&
          $attempts =~ ^[0-9]+$ && $acceptances =~ ^[0-9]+$ ]]; then
        [[ $deferrals -eq $signals && $signals -eq $attempts &&
           $acceptances -le $attempts ]] || valid=0
    else
        valid=0
    fi
    if [[ $issues =~ ^[0-9]+$ && $completions =~ ^[0-9]+$ ]]; then
        if [[ $arm == virtual ]]; then
            [[ $issues -gt 0 && $issues -eq $completions ]] || valid=0
        else
            [[ $issues -eq 0 && $completions -eq 0 ]] || valid=0
        fi
    else
        valid=0
    fi
    grep -E '^BFS_FP ' "$out/restore.log" > "$out/certificate.txt" || true
    printf 'arm\treplica\trc\tsim_ticks\tmaa_cycles\tindirect_reads\ttile_read_deferrals\tretry_signals\tretry_attempts\tretry_acceptances\twrite_issues\twrite_completions\tcertificate_sha256\tfatal_count\tvalid\n' \
        > "$out/result.tsv"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$arm" "$replica" "$rc" "$ticks" "$cycles" "$indrd" \
        "$deferrals" "$signals" "$attempts" "$acceptances" \
        "$issues" "$completions" \
        "$(sha256sum "$out/certificate.txt" | cut -d' ' -f1)" \
        "$fatal" "$valid" >> "$out/result.tsv"
    [[ $valid -eq 1 ]] || return 1
    echo "[$(date -Is)] restore complete: $arm replica=$replica ticks=$ticks"
}

run_phase() {
    local arm=$1 binary=$2
    shift 2
    local status=0
    local -a pids=()
    for ((replica = 1; replica <= replicas; replica++)); do
        restore_one "$arm" "$replica" "$binary" "$@" &
        pids+=("$!")
    done
    for pid in "${pids[@]}"; do
        wait "$pid" || status=1
    done
    [[ $status -eq 0 ]]
    mapfile -t signatures < <(
        for ((replica = 1; replica <= replicas; replica++)); do
            awk -F '\t' 'NR == 2 {
                print $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
            }' "$campaign/runs/$arm/replica_$replica/result.tsv"
        done | sort -u
    )
    [[ ${#signatures[@]} -eq 1 ]]
}

run_phase native "$native"
run_phase virtual "$virtual" "${virtual_options[@]}"

printf 'arm\treplica\trc\tsim_ticks\tmaa_cycles\tindirect_reads\ttile_read_deferrals\tretry_signals\tretry_attempts\tretry_acceptances\twrite_issues\twrite_completions\tcertificate_sha256\tfatal_count\tvalid\n' \
    > "$campaign/results.tsv"
for arm in native virtual; do
    for ((replica = 1; replica <= replicas; replica++)); do
        sed -n '2p' "$campaign/runs/$arm/replica_$replica/result.tsv"
    done
done >> "$campaign/results.tsv"

native_ticks=$(awk -F '\t' '$1 == "native" { print $4; exit }' \
    "$campaign/results.tsv")
virtual_ticks=$(awk -F '\t' '$1 == "virtual" { print $4; exit }' \
    "$campaign/results.tsv")
awk -v native="$native_ticks" -v virtual="$virtual_ticks" 'BEGIN {
    printf "native_ticks\tvirtual_ticks\tspeedup\telapsed_reduction_percent\n"
    printf "%.0f\t%.0f\t%.9f\t%.6f\n", native, virtual,
        native / virtual, (1 - virtual / native) * 100
}' > "$campaign/summary.tsv"
: > "$campaign/campaign.pass"
cat "$campaign/results.tsv"
cat "$campaign/summary.tsv"
