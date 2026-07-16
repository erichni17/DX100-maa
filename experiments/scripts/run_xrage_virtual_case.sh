#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 ]]; then
    echo "usage: $0 NAME BINARY DATA OUTDIR [gem5 options ...]" >&2
    exit 2
fi

name=$1
binary=$2
data=$3
outdir=$4
shift 4
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=${GEM5_BIN:-$root/build/X86/gem5.opt.virtual_v2}
config=$root/configs/deprecated/example/se.py
ramulator=$root/ext/ramulator2/ramulator2/example_gem5_config.yaml
checkpoint_timeout=${CHECKPOINT_TIMEOUT:-43200}
restore_timeout=${RESTORE_TIMEOUT:-86400}

mkdir -p "$outdir"
printf 'name=%s\nbinary=%s\ndata=%s\ngem5=%s\n' \
    "$name" "$binary" "$data" "$gem5" > "$outdir/manifest.txt"
sha256sum "$gem5" "$binary" "$data" >> "$outdir/manifest.txt"
printf 'extra_options=' >> "$outdir/manifest.txt"
printf ' %q' "$@" >> "$outdir/manifest.txt"
printf '\n' >> "$outdir/manifest.txt"

export LD_LIBRARY_PATH="$root/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"
rm -rf "$outdir"/cpt.*
timeout "$checkpoint_timeout" "$gem5" --listener-mode=off --outdir="$outdir" \
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
    --max-checkpoints=1 --cmd "$binary" --options "-f $data" \
    > "$outdir/checkpoint.log" 2>&1
ls "$outdir"/cpt.* >/dev/null

OMP_PROC_BIND=false OMP_NUM_THREADS=4 timeout "$restore_timeout" \
    "$gem5" --listener-mode=off --outdir="$outdir" "$config" \
    --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB \
    --sys-clock 3.2GHz --cpu-clock 3.2GHz \
    --caches --l1d_size=32kB --l1d_assoc=8 \
    --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8 \
    --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher \
    --l1i_mshrs=16 --l1i_write_buffers=8 --l2cache --l2_size=256kB \
    --l2_assoc=4 --l2-hwp-type=StridePrefetcher --l2_mshrs=32 \
    --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16 \
    --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4 \
    --cacheline_size=64 --mem-type Ramulator2 --ramulator-config "$ramulator" \
    --mem-channels=2 --maa --maa_num_maas=1 --maa_num_tile_elements=16384 \
    --maa_l2_uncacheable --maa_l3_uncacheable \
    --maa_num_initial_row_table_slices=32 "$@" \
    --cmd "$binary" --options "-f $data" > "$outdir/restore.log" 2>&1

awk '$1=="simTicks" || $1=="system.maa.cycles_TOTAL" || $1~"IND_Virt" {print}' \
    "$outdir/stats.txt" | tail -24 > "$outdir/result.txt"
rg 'ROI End|panic|fatal|Error:' "$outdir/restore.log" >> "$outdir/result.txt" || true
cat "$outdir/result.txt"
