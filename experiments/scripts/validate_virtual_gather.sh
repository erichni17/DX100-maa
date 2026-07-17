#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 || $# -gt 15 ]]; then
    cat >&2 <<EOF
usage: $0 N PATTERN OUTDIR [TIMEOUT_SECONDS] [BINARY] [COMBINE_SLOTS]
       [RESPONSE_SLOTS] [WRITE_CREDITS] [COMBINE_WORDS] [MASKED_WRITES]
       [RESPONSE_WORDS] [RESPONSE_WORD_POOL] [COMBINE_WAYS]
       [WORDS_PER_CYCLE] [COMBINE_BANKS]
EOF
    exit 2
fi

n=$1
pattern=$2
outdir=$3
timeout_seconds=${4:-21600}
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=${GEM5_BIN:-$root/build/X86/gem5.opt.virtual_banks_capped_f4e7491213bc}
binary=${5:-$root/benchmarks/API/test_virtual_gather_T16K.o}
combine_slots=${6:-16}
response_slots=${7:-8}
write_credits=${8:-32}
combine_words=${9:-0}
masked_writes=${10:-0}
response_words=${11:-0}
response_word_pool=${12:-0}
combine_ways=${13:-0}
words_per_cycle=${14:-0}
combine_banks=${15:-0}
masked_args=()
if [[ "$masked_writes" == 1 ]]; then
    masked_args+=(--maa_virtual_masked_writes)
fi
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
    --maa_num_initial_row_table_slices=16 \
    --maa_virtual_combine_slots="$combine_slots" \
    --maa_virtual_combine_words="$combine_words" \
    --maa_virtual_combine_ways="$combine_ways" \
    --maa_virtual_combine_banks="$combine_banks" \
    --maa_virtual_response_slots="$response_slots" \
    --maa_virtual_response_words="$response_words" \
    --maa_virtual_response_word_pool="$response_word_pool" \
    --maa_virtual_words_per_cycle="$words_per_cycle" \
    --maa_virtual_max_outstanding_writes="$write_credits" \
    "${masked_args[@]}" \
    --cmd "$binary" \
    --options "$n $pattern" >"$outdir/restore.log" 2>&1

mapfile -t results < <(
    grep -E 'VIRTUAL_GATHER(64)?_RESULT' "$outdir/restore.log" || true
)
if [[ ${#results[@]} -ne 1 ]]; then
    printf 'expected one result marker, found %d\n' "${#results[@]}" >&2
    exit 1
fi
printf '%s\n' "${results[0]}"
if [[ ! ${results[0]} =~ (^|[[:space:]])errors=0($|[[:space:]]) ]]; then
    echo "virtual gather verifier reported errors" >&2
    exit 1
fi
