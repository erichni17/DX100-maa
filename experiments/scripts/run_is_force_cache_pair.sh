#!/usr/bin/env bash
# Matched NAS-IS control/treatment for coherent MAA target routing.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
RUN_ROOT=${RUN_ROOT:-/data1/nier/dx100-runs/2026-08-02-is-force-cache-pair}
GEM5=${GEM5:-$ROOT/build/X86/gem5.opt}
WORKLOAD=${WORKLOAD:-$ROOT/benchmarks/NAS/is/is_maa_16K_roi_verify}
CHECKPOINT=${CHECKPOINT:-$RUN_ROOT/checkpoint}
RAMCFG=$ROOT/ext/ramulator2/ramulator2/example_gem5_config.yaml
SE=$ROOT/configs/deprecated/example/se.py
ARM=${1:-}

common_args=(
    "$SE"
    --cpu-type X86O3CPU -r 1 -n 4 --mem-size 16GB
    --sys-clock 3.2GHz --cpu-clock 3.2GHz
    --caches
    --l1d_size=32kB --l1d_assoc=8 --l1d-hwp-type=StridePrefetcher
    --l1d_mshrs=16 --l1d_write_buffers=8
    --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher
    --l1i_mshrs=16 --l1i_write_buffers=8
    --l2cache --l2_size=256kB --l2_assoc=4
    --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16
    --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256
    --l3_write_buffers=128 --l3_ports 4 --cacheline_size=64
    --mem-type Ramulator2 --ramulator-config "$RAMCFG" --mem-channels 2
    --maa_ncbus_width 32 --maa --maa_num_maas 1
    --maa_num_tile_elements 16384 --maa_l2_uncacheable --maa_l3_uncacheable
    --maa_num_initial_row_table_slices 32
    --cmd "$WORKLOAD" --options MAA --prog-interval=1000
)

write_manifest() {
    local output=$1
    {
        printf 'source_commit\t%s\n' "$(git -C "$ROOT" rev-parse HEAD)"
        printf 'gem5\t%s\n' "$GEM5"
        printf 'gem5_sha256\t%s\n' "$(sha256sum "$GEM5" | awk '{print $1}')"
        printf 'workload\t%s\n' "$WORKLOAD"
        printf 'workload_sha256\t%s\n' "$(sha256sum "$WORKLOAD" | awk '{print $1}')"
        printf 'frozen_input_sha256\t%s\n' \
            "$(sha256sum "$ROOT/benchmarks/NAS/is/key_array_4C.h" | awk '{print $1}')"
        printf 'checkpoint\t%s\n' "$CHECKPOINT"
        printf 'checkpoint_tick\t%s\n' "$(basename "$(find "$CHECKPOINT" -maxdepth 1 -type d -name 'cpt.[0-9]*' -print -quit)")"
    } > "$output"
}

prepare() {
    mkdir -p "$RUN_ROOT"
    make -C "$ROOT/benchmarks/NAS/is" GEM5_BUILD=1 is_maa_16K_roi_verify
    if ! find "$CHECKPOINT" -maxdepth 1 -type d -name 'cpt.[0-9]*' -print -quit 2>/dev/null | grep -q .; then
        mkdir -p "$CHECKPOINT"
        export LD_LIBRARY_PATH="$ROOT/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"
        OMP_PROC_BIND=false OMP_NUM_THREADS=4 "$GEM5" --outdir="$CHECKPOINT" "$SE" \
            --cpu-type AtomicSimpleCPU -n 4 --mem-size 16GB --max-checkpoints=1 \
            --cmd "$WORKLOAD" --options MAA > "$CHECKPOINT/ckpt.log" 2>&1
    fi
    write_manifest "$RUN_ROOT/manifest.tsv"
}

run_arm() {
    local arm=$1
    local out=$RUN_ROOT/$arm
    local -a treatment=()
    case "$arm" in
        control) ;;
        treatment) treatment=(--maa_force_cache_access) ;;
        *) echo "unknown arm: $arm" >&2; exit 2 ;;
    esac
    [[ -f $RUN_ROOT/manifest.tsv ]] || { echo "run prepare first" >&2; exit 2; }
    [[ ! -e $out ]] || { echo "refusing to overwrite $out" >&2; exit 2; }
    mkdir -p "$out"
    cp -a "$CHECKPOINT"/cpt.* "$out"/
    printf '%q ' "$GEM5" --outdir="$out" "${common_args[@]}" "${treatment[@]}" > "$out/command.sh"
    printf '\n' >> "$out/command.sh"
    export LD_LIBRARY_PATH="$ROOT/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"
    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 "$GEM5" --outdir="$out" \
        "${common_args[@]}" "${treatment[@]}" > "$out/run.log" 2>&1
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$out/wrapper.exit"
    [[ $rc -eq 0 ]] || exit "$rc"
    grep -Fq 'ROI End!!!' "$out/run.log"
    grep -Fq 'successfull: passed verification' "$out/run.log"
    grep -Fq 'm5_exit instruction encountered' "$out/run.log"
    [[ $(grep -c '^---------- Begin Simulation Statistics ----------$' "$out/stats.txt") -ge 1 ]]
    printf 'PASS\n' > "$out/terminal.status"
}

case "$ARM" in
    prepare) prepare ;;
    control|treatment) run_arm "$ARM" ;;
    *) echo "usage: $0 {prepare|control|treatment}" >&2; exit 2 ;;
esac
