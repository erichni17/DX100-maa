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
[[ $timeout_seconds =~ ^[0-9]+$ ]] || {
    echo "TIMEOUT_SECONDS must be a non-negative integer" >&2
    exit 2
}
restore_timeout=()
if [[ $timeout_seconds -ne 0 ]]; then
    restore_timeout=(timeout "$timeout_seconds")
fi
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
expect_failure=${EXPECT_FAILURE:-0}
expected_failure_regex=${EXPECTED_FAILURE_REGEX:-virtual (backing index|retirement write).*exceeds}
indirect_units=${MAA_NUM_INDIRECT_UNITS_PER_MAA:-1}
[[ $indirect_units =~ ^[1-9][0-9]*$ ]] || {
    echo "MAA_NUM_INDIRECT_UNITS_PER_MAA must be a positive integer" >&2
    exit 2
}
num_maas=${MAA_NUM_MAAS:-1}
[[ $num_maas =~ ^[1-9][0-9]*$ ]] || {
    echo "MAA_NUM_MAAS must be a positive integer" >&2
    exit 2
}
num_cpus=${GEM5_NUM_CPUS:-4}
[[ $num_cpus =~ ^[1-9][0-9]*$ ]] || {
    echo "GEM5_NUM_CPUS must be a positive integer" >&2
    exit 2
}
physical_tile_elements=${MAA_PHYSICAL_TILE_ELEMENTS:-0}
[[ $physical_tile_elements =~ ^[0-9]+$ ]] || {
    echo "MAA_PHYSICAL_TILE_ELEMENTS must be a non-negative integer" >&2
    exit 2
}
virtual_grow_order=${MAA_VIRTUAL_GROW_ORDER:-0}
[[ $virtual_grow_order == 0 || $virtual_grow_order == 1 ]] || {
    echo "MAA_VIRTUAL_GROW_ORDER must be 0 or 1" >&2
    exit 2
}
grow_order_args=()
if [[ $virtual_grow_order == 1 ]]; then
    grow_order_args+=(--maa_virtual_grow_order)
fi
retirement_cache_response_latency=${MAA_RETIREMENT_CACHE_RESPONSE_LATENCY:-1}
[[ $retirement_cache_response_latency =~ ^[1-9][0-9]*$ ]] || {
    echo "MAA_RETIREMENT_CACHE_RESPONSE_LATENCY must be a positive integer" >&2
    exit 2
}
masked_args=()
if [[ "$masked_writes" == 1 ]]; then
    masked_args+=(--maa_virtual_masked_writes)
fi
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
debug_args=()
if [[ -n ${GEM5_DEBUG_FLAGS:-} ]]; then
    debug_args+=(--debug-flags="$GEM5_DEBUG_FLAGS")
fi

if [[ -e "$outdir" ]]; then
    echo "refusing to overwrite existing output path: $outdir" >&2
    exit 2
fi
mkdir -p "$outdir"

set +e
/usr/bin/time -f 'checkpoint_wall=%e checkpoint_rss_kb=%M' \
    timeout 300 "$gem5" --listener-mode=off "${debug_args[@]}" \
    --outdir="$outdir" "$config" \
    --cpu-type AtomicSimpleCPU -n "$num_cpus" --mem-size 2GB \
    --max-checkpoints=1 \
    --cmd "$binary" --options "$n $pattern" >"$outdir/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" >"$outdir/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]] || {
    echo "checkpoint failed with rc=$checkpoint_rc" >&2
    exit 1
}
mapfile -t layouts < <(
    grep -E 'VIRTUAL_GATHER(64)?_LAYOUT' "$outdir/checkpoint.log" || true
)
if [[ ${#layouts[@]} -ne 1 ]] ||
   [[ ! ${layouts[0]} =~ (^|[[:space:]])mem_size=2147483648($|[[:space:]]) ]]; then
    echo "verifier memory map does not match gem5 --mem-size=2GB" >&2
    printf 'layout markers: %s\n' "${layouts[*]:-<none>}" >&2
    exit 1
fi

set +e
OMP_PROC_BIND=false OMP_NUM_THREADS="$num_cpus" \
/usr/bin/time -f 'restore_wall=%e restore_rss_kb=%M' \
    "${restore_timeout[@]}" \
    "$gem5" --listener-mode=off "${debug_args[@]}" \
    --outdir="$outdir" "$config" --cpu-type X86O3CPU -r 1 \
    -n "$num_cpus" \
    --mem-size 2GB --sys-clock 3.2GHz --cpu-clock 3.2GHz \
    --caches --l1d_size=32kB --l1d_assoc=8 \
    --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8 \
    --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher \
    --l1i_mshrs=16 --l1i_write_buffers=8 --l2cache --l2_size=256kB \
    --l2_assoc=4 --l2-hwp-type=StridePrefetcher --l2_mshrs=32 \
    --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16 \
    --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4 \
    --cacheline_size=64 --mem-type Ramulator2 --ramulator-config "$ramulator" \
    --mem-channels=1 --maa --maa_num_maas="$num_maas" \
    --maa_num_tile_elements=16384 \
    --maa_physical_tile_elements="$physical_tile_elements" \
    --maa_num_indirect_units_per_maa="$indirect_units" \
    --maa_retirement_cache_response_latency="$retirement_cache_response_latency" \
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
    "${grow_order_args[@]}" \
    "${masked_args[@]}" \
    --cmd "$binary" \
    --options "$n $pattern" >"$outdir/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" >"$outdir/restore.exit"

if [[ $expect_failure == 1 ]]; then
    [[ $restore_rc -ne 0 ]] || {
        echo "expected restore failure but command exited zero" >&2
        exit 1
    }
    grep -Eiq "$expected_failure_regex" "$outdir/restore.log" || {
        echo "restore failed without expected bounds diagnostic" >&2
        exit 1
    }
    echo "VIRTUAL_GATHER_EXPECTED_FAILURE pattern=$pattern rc=$restore_rc"
    exit 0
fi

[[ $restore_rc -eq 0 ]] || {
    echo "restore failed with rc=$restore_rc" >&2
    exit 1
}

mapfile -t results < <(
    grep -E 'VIRTUAL_GATHER(64)?_RESULT' "$outdir/restore.log" || true
)
if [[ ${#results[@]} -ne 1 ]]; then
    printf 'expected one result marker, found %d\n' "${#results[@]}" >&2
    exit 1
fi
roi_count=$(grep -Fxc 'ROI Ended' "$outdir/restore.log" || true)
fatal_count=$(grep -Eic \
    'panic|fatal|assert|abort|segmentation fault|error:' \
    "$outdir/restore.log" || true)
[[ $roi_count -eq 1 && $fatal_count -eq 0 ]] || {
    echo "invalid completion: roi_count=$roi_count fatal_count=$fatal_count" >&2
    exit 1
}
printf '%s\n' "${results[0]}"
if [[ ! ${results[0]} =~ (^|[[:space:]])errors=0($|[[:space:]]) ]]; then
    echo "virtual gather verifier reported errors" >&2
    exit 1
fi
