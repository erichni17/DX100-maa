#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "usage: $0 GEM5_BIN BINARY GRAPH OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
binary=$(realpath "$2")
graph=$(realpath "$3")
campaign=$(realpath -m "$4")
config=$root/configs/deprecated/example/se.py
ramulator=$root/ext/ramulator2/ramulator2/example_gem5_config.yaml
runner=$(realpath "$0")
checkpoint_timeout=${CHECKPOINT_TIMEOUT:-21600}
restore_timeout=${RESTORE_TIMEOUT:-172800}

for path in "$gem5" "$binary" "$graph" "$config" "$ramulator" "$runner"; do
    [[ -f $path ]] || { echo "missing artifact: $path" >&2; exit 3; }
done
[[ -x $gem5 && -x $binary ]] || {
    echo "simulator and benchmark must be executable" >&2
    exit 3
}
[[ ! -e $campaign ]] || {
    echo "campaign output already exists; choose a new path: $campaign" >&2
    exit 2
}

mkdir -p "$campaign/checkpoint" "$campaign/run"
trap 'rc=$?; trap - EXIT; if [[ $rc -ne 0 ]]; then rm -f "$campaign/native_oracle_candidate.pass"; printf "%s\n" "$rc" > "$campaign/campaign.fail"; fi; exit "$rc"' EXIT
exec > >(tee "$campaign/controller.log") 2>&1

sha256sum "$gem5" "$binary" "$graph" "$config" "$ramulator" "$runner" \
    "$root/benchmarks/gapbs/src/bfs.cc" > "$campaign/artifact_sha256.txt"
{
    printf 'simulator_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'simulator_sha256=%s\n' "$(sha256sum "$gem5" | cut -d' ' -f1)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s\n' 'purpose=native-only S16 BFS oracle candidate; no virtual arm is run'
    printf '%s\n' 'acceptance=valid structural certificate and positive native indirect-read count'
} > "$campaign/source.txt"
export LD_LIBRARY_PATH="$root/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"

write_command() {
    local output=$1
    shift
    printf '%q ' "$@" > "$output"
    printf '\n' >> "$output"
}

checkpoint_command=(
    timeout "$checkpoint_timeout" "$gem5" --listener-mode=off
    --outdir="$campaign/checkpoint" "$config" --cpu-type AtomicSimpleCPU
    -n 4 --mem-size 2GB --max-checkpoints=1 --cmd "$binary"
    --options "-f $graph -n 1"
)
write_command "$campaign/checkpoint/checkpoint.command" \
    "${checkpoint_command[@]}"
set +e
"${checkpoint_command[@]}" > "$campaign/checkpoint/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$campaign/checkpoint/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]]
mapfile -t checkpoints < <(
    find "$campaign/checkpoint" -mindepth 2 -maxdepth 2 -type f \
        -name m5.cpt -printf '%h\n' | sort -u
)
[[ ${#checkpoints[@]} -eq 1 ]]
sha256sum "${checkpoints[0]}"/* > \
    "$campaign/checkpoint/checkpoint_sha256.txt"
cp -a --reflink=auto "${checkpoints[0]}" "$campaign/run/"

restore_command=(
    timeout "$restore_timeout" "$gem5" --listener-mode=off
    --outdir="$campaign/run" "$config" --cpu-type X86O3CPU -r 1 -n 4
    --mem-size 2GB --sys-clock 3.2GHz --cpu-clock 3.2GHz --caches
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
    --maa_num_initial_row_table_slices=32 --cmd "$binary"
    --options "-f $graph -n 1"
)
write_command "$campaign/run/restore.command" "${restore_command[@]}"
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
    /usr/bin/time -f 'restore_wall=%e restore_rss_kb=%M' \
    "${restore_command[@]}" > "$campaign/run/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" > "$campaign/run/restore.exit"

stats_blob=$(awk '
    /^---------- Begin Simulation Statistics/ { section++ }
    section == 1 && $1 == "simTicks" { ticks=$2; ticks_seen++ }
    section == 1 && $1 == "system.maa.cycles_TOTAL" {
        cycles=$2; cycles_seen++
    }
    section == 1 && $1 == "system.maa.numInst_INDRD" {
        indrd=$2; indrd_seen++
    }
    /^---------- End Simulation Statistics/ && section == 1 {
        if (ticks_seen != 1 || cycles_seen != 1 || indrd_seen != 1 ||
            ticks !~ /^[0-9]+$/ || cycles !~ /^[0-9]+$/ ||
            indrd !~ /^[0-9]+$/)
            exit 2
        printf "%s\n%s\n%s\n", ticks, cycles, indrd
        emitted=1
        exit 0
    }
    END { if (!emitted) exit 2 }
' "$campaign/run/stats.txt") || stats_blob=
mapfile -t stats_fields <<< "$stats_blob"
ticks=${stats_fields[0]:-NA}
cycles=${stats_fields[1]:-NA}
indrd=${stats_fields[2]:-NA}

awk '
    /^BFS_FP / {
        lines++
        original=$0
        for (i = 2; i <= NF; i++) {
            split($i, pair, "=")
            if (pair[1] == "levels") levels=pair[2]
            else if (pair[1] == "reached") reached=pair[2]
            else if (pair[1] == "depth_reached") depth_reached=pair[2]
            else if (pair[1] == "max_depth") max_depth=pair[2]
            else if (pair[1] == "invalid_chains") invalid_chains=pair[2]
        }
    }
    END {
        if (lines != 1 || levels !~ /^[0-9]+$/ || reached !~ /^[0-9]+$/ ||
            depth_reached !~ /^[0-9]+$/ || max_depth !~ /^[0-9]+$/ ||
            invalid_chains != 0 || levels == 0 || reached == 0 ||
            depth_reached != reached || max_depth + 1 != levels)
            exit 2
        print original
    }
' "$campaign/run/restore.log" > "$campaign/native_certificate.txt"

roi=$(grep -Fxc 'ROI End!!!' "$campaign/run/restore.log" || true)
fatal=$(grep -Eic \
    'panic|fatal|assert|abort|segmentation fault|error:' \
    "$campaign/run/restore.log" || true)
valid=1
[[ $restore_rc -eq 0 && $roi -eq 1 && $fatal -eq 0 && \
   $ticks =~ ^[1-9][0-9]*$ && $cycles =~ ^[1-9][0-9]*$ && \
   $indrd =~ ^[1-9][0-9]*$ ]] || valid=0

printf 'rc\tsim_ticks\tmaa_cycles\tnative_indirect_reads\tcertificate_sha256\tfatal_count\tvalid\n' \
    > "$campaign/result.tsv"
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$restore_rc" "$ticks" "$cycles" "$indrd" \
    "$(sha256sum "$campaign/native_certificate.txt" | cut -d' ' -f1)" \
    "$fatal" "$valid" >> "$campaign/result.tsv"
cat "$campaign/result.tsv"
cat "$campaign/native_certificate.txt"
[[ $valid -eq 1 ]]
: > "$campaign/native_oracle_candidate.pass"
