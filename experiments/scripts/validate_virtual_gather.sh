#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 || $# -gt 5 ]]; then
    echo "usage: $0 N PATTERN OUTDIR [TIMEOUT_SECONDS] [BINARY]" >&2
    exit 2
fi

n=$1
pattern=$2
outdir=$3
timeout_seconds=${4:-21600}
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5="$root/build/X86/gem5.opt.virtual_v1"
binary=${5:-$root/benchmarks/API/test_virtual_gather_T16K.o}
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"

rm -rf "$outdir"
mkdir -p "$outdir"

/usr/bin/time -f 'checkpoint_wall=%e checkpoint_rss_kb=%M' \
    timeout 300 "$gem5" --listener-mode=off --outdir="$outdir" "$config" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1 \
    --cmd "$binary" --options "$n $pattern" >"$outdir/checkpoint.log" 2>&1

OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
/usr/bin/time -f 'restore_wall=%e restore_rss_kb=%M' \
    timeout "$timeout_seconds" "$gem5" --listener-mode=off \
    --outdir="$outdir" "$config" --cpu-type X86O3CPU -r 1 -n 4 \
    --mem-size 2GB --sys-clock 3.2GHz --cpu-clock 3.2GHz \
    --caches --l1d_size=32kB --l1d_assoc=8 \
    --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8 \
    --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher \
    --l1i_mshrs=16 --l1i_write_buffers=8 --l2cache --l2_size=256kB \
    --l2_assoc=4 --l2-hwp-type=StridePrefetcher --l2_mshrs=32 \
    --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16 \
    --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4 \
    --cacheline_size=64 --mem-type Ramulator2 --ramulator-config "$ramulator" \
    --mem-channels=1 --maa --maa_num_tile_elements=16384 \
    --maa_num_initial_row_table_slices=16 --cmd "$binary" \
    --options "$n $pattern" >"$outdir/restore.log" 2>&1

grep -E 'VIRTUAL_GATHER(64)?_RESULT' "$outdir/restore.log"
