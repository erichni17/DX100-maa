#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 7 ]]; then
    echo "usage: $0 OUTDIR GEM5 NATIVE16 NATIVE4 BOUNDED RAMULATOR CG_DATA_HEADER" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
mode=${CG_MATRIX_MODE:-full}
case $mode in
    full) completion_marker=matrix.complete ;;
    bounded-precheck) completion_marker=precheck.complete ;;
    *) echo "unsupported CG_MATRIX_MODE: $mode" >&2; exit 2 ;;
esac
gem5_source=$(realpath "$2")
native16_source=$(realpath "$3")
native4_source=$(realpath "$4")
bounded_source=$(realpath "$5")
ramulator_source=$(realpath "$6")
data_header_source=$(realpath "$7")
config="$root/configs/deprecated/example/se.py"
ramulator_config="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"

[[ ! -e $out ]] || { echo "refusing to overwrite $out" >&2; exit 2; }
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence run from a dirty worktree" >&2
    git -C "$root" status --short >&2
    exit 1
}

mkdir -p "$out/input"
record_exit() {
    local rc=$?
    trap - EXIT
    if [[ ! -e $out/$completion_marker && $rc -eq 0 ]]; then
        rc=125
    fi
    printf '%s\n' "$rc" > "$out/matrix.exit"
    exit "$rc"
}
trap record_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cp --reflink=auto "$gem5_source" "$out/input/gem5.opt"
cp --reflink=auto "$native16_source" "$out/input/cg_maa_16K_fp"
cp --reflink=auto "$native4_source" "$out/input/cg_maa_4K_fp"
cp --reflink=auto "$bounded_source" "$out/input/cg_maa_16K_bounded"
cp --reflink=auto "$ramulator_source" "$out/input/libramulator.so"
cp --reflink=auto "$data_header_source" "$out/input/cg_data_4C.h"
chmod 0555 "$out/input/gem5.opt" "$out/input"/cg_maa_*

gem5="$out/input/gem5.opt"
native16="$out/input/cg_maa_16K_fp"
native4="$out/input/cg_maa_4K_fp"
bounded="$out/input/cg_maa_16K_bounded"
ramulator="$out/input/libramulator.so"
source_commit=$(git -C "$root" rev-parse HEAD)
sha256sum "$gem5" "$native16" "$native4" "$bounded" "$ramulator" \
    "$out/input/cg_data_4C.h" "$config" "$ramulator_config" "$0" \
    > "$out/input/artifact_sha256.txt"
git -C "$root" archive --format=tar "$source_commit" -- \
    src/mem/MAA configs/common configs/deprecated/example/se.py \
    benchmarks/NAS/cg/cg.cpp benchmarks/NAS/cg/Makefile \
    experiments/scripts/run_cg_bounded_descriptor_matrix.sh \
    > "$out/input/source.tar"
{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'mode=%s\n' "$mode"
    printf 'comparison=three-binaries-three-checkpoints\n'
    printf 'input_mode=precomputed-cg-data-header\n'
    printf 'maa_mem_size_bytes=2147483648\n'
    printf 'bounded_logical_elements=16384\n'
    printf 'bounded_physical_elements=4096\n'
    printf 'bounded_consumer_elements=4096\n'
    printf 'timeout=none\n'
} > "$out/manifest.txt"

library_dir=$(dirname "$ramulator")
export LD_LIBRARY_PATH="$library_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

make_checkpoint() {
    local label=$1 binary=$2 expect_layout=$3
    local checkpoint="$out/checkpoint-$label"
    mkdir -p "$checkpoint"
    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
        "$gem5" --listener-mode=off --outdir="$checkpoint" \
        "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
        --max-checkpoints=1 --cmd "$binary" --options MAA \
        > "$out/checkpoint-$label.log" 2>&1
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$out/checkpoint-$label.exit"
    [[ $rc -eq 0 ]]
    grep -Fq 'Using data from file!' "$out/checkpoint-$label.log"
    ! grep -Fq 'makea started!' "$out/checkpoint-$label.log"
    grep -Fq 'maa_mem_size=2147483648' "$out/checkpoint-$label.log" || \
        [[ $expect_layout -eq 0 ]]
    if [[ $expect_layout -eq 1 ]]; then
        grep -Fq 'CG_BOUNDED_VIRTUAL_LAYOUT logical=16384 consumer=4096' \
            "$out/checkpoint-$label.log"
    fi
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
        "$out/checkpoint-$label.log") -eq 1 ]]
    (
        cd "$checkpoint"
        find . -type f -print0 | sort -z | xargs -0 sha256sum
    ) > "$out/checkpoint-$label.files.sha256"
    sha256sum "$out/checkpoint-$label.files.sha256" \
        > "$out/checkpoint-$label.identity.sha256"
}

common=(
    --listener-mode=off
    "$config"
    --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --sys-clock 3.2GHz --cpu-clock 3.2GHz
    --caches --l1d_size=32kB --l1d_assoc=8
    --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8
    --l1i_size=32kB --l1i_assoc=8
    --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16 --l1i_write_buffers=8
    --l2cache --l2_size=256kB --l2_assoc=4
    --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16
    --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256
    --l3_write_buffers=128 --l3_ports=4 --cacheline_size=64
    --mem-type Ramulator2 --ramulator-config "$ramulator_config"
    --mem-channels=2 --maa_ncbus_width=32
    --maa --maa_num_maas=1 --maa_l2_uncacheable --maa_l3_uncacheable
    --maa_num_initial_row_table_slices=16
    --maa_num_row_table_entries_per_subslice_row=8
    --maa_virtual_combine_slots=384 --maa_virtual_combine_words=4096
    --maa_virtual_combine_ways=4 --maa_virtual_response_slots=96
    --maa_virtual_response_word_pool=480 --maa_virtual_words_per_cycle=4
    --maa_virtual_max_outstanding_writes=64 --maa_virtual_masked_writes
    --maa_virtual_index_buffer_lines=4
    --maa_virtual_descriptor_spool_read_credits=24
    --maa_virtual_index_filter_words_per_cycle=64
    --options MAA --prog-interval=1000
)

run_arm() {
    local label=$1 binary=$2 checkpoint=$3 logical=$4 physical=$5
    local rows=$6 offsets=$7 bounded_mode=$8 bypass=$9
    local max_tick=${10:-0}
    local arm="$out/$label"
    mkdir -p "$arm/run"
    local treatment=(
        --checkpoint-dir "$checkpoint"
        --cmd "$binary"
        --maa_num_tile_elements="$logical"
        --maa_physical_tile_elements="$physical"
        --maa_num_row_table_rows_per_slice="$rows"
        --maa_num_offset_table_entries="$offsets"
        --maa_num_offset_table_epoch_entries="$offsets"
    )
    if [[ $bounded_mode -eq 1 ]]; then
        treatment+=(
            --maa_virtual_grow_order
            --maa_virtual_index_partitions=64
            --maa_virtual_index_range_policy=3
            --maa_virtual_index_range_passes
            --maa_virtual_index_descriptor_spool
            --maa_virtual_descriptor_spool_read_ahead
            --maa_virtual_index_force_cache
            --maa_virtual_partition_keep_combiner
        )
    else
        treatment+=(--maa_virtual_index_partitions=1)
    fi
    if [[ $bypass -eq 1 ]]; then
        treatment+=(--maa_virtual_descriptor_spool_source_bypass_cache)
    fi
    if [[ $max_tick -gt 0 ]]; then
        treatment+=(--abs-max-tick "$max_tick")
    fi
    printf '%q ' "$gem5" --outdir="$arm/run" "${common[@]}" \
        "${treatment[@]}" > "$arm/command.txt"
    printf '\n' >> "$arm/command.txt"
    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
        /usr/bin/time -f 'wall_seconds=%e max_rss_kb=%M' \
        "$gem5" --outdir="$arm/run" "${common[@]}" "${treatment[@]}" \
        > "$arm/run.log" 2> "$arm/time.log"
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$arm/exit_code"
    return "$rc"
}

if [[ $mode == bounded-precheck ]]; then
    make_checkpoint bounded "$bounded" 1
    checkpoint_tick=$(sed -nE \
        's/^Exiting @ tick ([0-9]+) because checkpoint$/\1/p' \
        "$out/checkpoint-bounded.log")
    [[ $checkpoint_tick =~ ^[0-9]+$ ]]
    max_tick=$((checkpoint_tick + 2000000000))
    printf 'checkpoint_tick=%s\nmax_tick=%s\n' "$checkpoint_tick" "$max_tick" \
        > "$out/precheck-window.txt"
    run_arm bounded4_cached "$bounded" "$out/checkpoint-bounded" \
        16384 4096 16 4096 1 0 "$max_tick"
    arm="$out/bounded4_cached"
    [[ $(cat "$arm/exit_code") -eq 0 ]]
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because simulate\(\) limit reached$' \
        "$arm/run.log") -eq 1 ]]
    ! grep -Eq 'panic:|fatal:|Program aborted' "$arm/run.log" "$arm/time.log"
    grep -Fxq 'num_tile_elements=16384' "$arm/run/config.ini"
    grep -Fxq 'physical_tile_elements=4096' "$arm/run/config.ini"
    grep -Fxq 'num_offset_table_entries=4096' "$arm/run/config.ini"
    grep -Fxq 'num_offset_table_epoch_entries=4096' "$arm/run/config.ini"
    grep -Fxq 'num_row_table_rows_per_slice=16' "$arm/run/config.ini"
    scans=$(awk '$1 ~ /IND_DescriptorSpoolBScans$/ { total += $2 } END { print total+0 }' \
        "$arm/run/stats.txt")
    external=$(awk '$1 ~ /IND_DescriptorSpoolExternalDescriptors$/ { total += $2 } END { print total+0 }' \
        "$arm/run/stats.txt")
    [[ $scans -gt 0 && $external -gt 0 ]]
    printf 'descriptor_scans=%s\ndescriptor_external=%s\n' "$scans" "$external" \
        > "$out/precheck-mechanism.txt"
    touch "$out/precheck.complete"
    exit 0
fi

checkpoint_jobs=()
make_checkpoint native16 "$native16" 0 & checkpoint_jobs+=("native16:$!")
make_checkpoint native4 "$native4" 0 & checkpoint_jobs+=("native4:$!")
make_checkpoint bounded "$bounded" 1 & checkpoint_jobs+=("bounded:$!")
for job in "${checkpoint_jobs[@]}"; do
    label=${job%%:*}
    pid=${job#*:}
    wait "$pid" || { echo "$label checkpoint failed" >&2; exit 1; }
done

jobs=()
run_arm native16 "$native16" "$out/checkpoint-native16" \
    16384 16384 64 16384 0 0 & jobs+=("native16:$!")
run_arm native4 "$native4" "$out/checkpoint-native4" \
    4096 4096 16 4096 0 0 & jobs+=("native4:$!")
run_arm bounded4_cached "$bounded" "$out/checkpoint-bounded" \
    16384 4096 16 4096 1 0 & jobs+=("bounded4_cached:$!")
run_arm bounded4_bypass "$bounded" "$out/checkpoint-bounded" \
    16384 4096 16 4096 1 1 & jobs+=("bounded4_bypass:$!")
failed=0
for job in "${jobs[@]}"; do
    label=${job%%:*}
    pid=${job#*:}
    if ! wait "$pid"; then
        echo "$label failed" >&2
        failed=1
    fi
done
[[ $failed -eq 0 ]]

printf 'arm\toutput_hash\tsimTicks\tdescriptor_scans\tdescriptor_external\tread_stalls\tcontrol_bytes_sum\n' \
    > "$out/results.tsv"
for label in native16 native4 bounded4_cached bounded4_bypass; do
    arm="$out/$label"
    log="$arm/run.log"
    stats="$arm/run/stats.txt"
    [[ $(cat "$arm/exit_code") -eq 0 ]]
    [[ $(grep -Fxc 'ROI End!!!' "$log") -eq 1 ]]
    [[ $(grep -Fxc 'Validation started' "$log") -eq 1 ]]
    [[ $(grep -Fxc 'Validation ended' "$log") -eq 1 ]]
    [[ $(grep -Ec '^CG_FINGERPRINT .* x_q5=88c0975669c7062d .* result=PASS$' \
        "$log") -eq 1 ]]
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
        "$log") -eq 1 ]]
    read -r ticks scans external stalls control < <(
        awk '
            /^---------- Begin Simulation Statistics/ { section++ }
            section == 1 && $1 == "simTicks" { ticks=$2 }
            section == 1 && $1 ~ /IND_DescriptorSpoolBScans$/ { scans+=$2 }
            section == 1 && $1 ~ /IND_DescriptorSpoolExternalDescriptors$/ { external+=$2 }
            section == 1 && $1 ~ /IND_DescriptorSpoolReadCreditStalls$/ { stalls+=$2 }
            section == 1 && $1 ~ /IND_DescriptorSpoolControlBytes$/ { control+=$2 }
            END { print ticks+0, scans+0, external+0, stalls+0, control+0 }
        ' "$stats")
    hash=$(sed -nE 's/^CG_FINGERPRINT .* x_q5=([0-9a-f]+) .* result=PASS$/\1/p' \
        "$log")
    if [[ $label == bounded4_* ]]; then
        [[ $scans -gt 0 && $external -gt 0 && $stalls -gt 0 ]]
    else
        [[ $scans -eq 0 && $external -eq 0 ]]
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$label" "$hash" \
        "$ticks" "$scans" "$external" "$stalls" "$control" \
        >> "$out/results.tsv"
done
touch "$out/matrix.complete"
