#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 4 ]]; then
    echo "usage: $0 GEM5_BIN OUTDIR [TIMEOUT_SECONDS] [N]" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
out=$(realpath -m "$2")
timeout_seconds=${3:-300}
n=${4:-16384}
virtual_words_per_cycle=${MAA_VIRTUAL_WORDS_PER_CYCLE:-1}
virtual_max_outstanding_writes=${MAA_VIRTUAL_MAX_OUTSTANDING_WRITES:-1}
[[ $timeout_seconds =~ ^[1-9][0-9]*$ ]] || {
    echo "TIMEOUT_SECONDS must be a positive integer" >&2
    exit 2
}
[[ $n =~ ^[1-9][0-9]*$ ]] || {
    echo "N must be a positive integer" >&2
    exit 2
}
[[ $virtual_words_per_cycle =~ ^[1-9][0-9]*$ ]] || {
    echo "MAA_VIRTUAL_WORDS_PER_CYCLE must be a positive integer" >&2
    exit 2
}
[[ $virtual_max_outstanding_writes =~ ^[1-9][0-9]*$ ]] || {
    echo "MAA_VIRTUAL_MAX_OUTSTANDING_WRITES must be a positive integer" >&2
    exit 2
}
[[ ! -e $out ]] || {
    echo "refusing to overwrite existing output: $out" >&2
    exit 2
}

ramulator_dir=/data1/nier/DX100/ext/ramulator2/ramulator2
ramulator_lib=$ramulator_dir/libramulator.so
expected_ramulator_sha=76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753
actual_ramulator_sha=$(sha256sum "$ramulator_lib" | awk '{print $1}')
[[ $actual_ramulator_sha == "$expected_ramulator_sha" ]] || {
    echo "production Ramulator hash mismatch: $actual_ramulator_sha" >&2
    exit 1
}

mkdir -p "$out/bin"
cxx=${CXX:-g++}
binary=$out/bin/test_native_live_checkpoint
"$cxx" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O2 -Wall -Wextra -g3 -fopenmp \
    -DGEM5 -DTILE_SIZE=16384 -DNUM_CORES=1 -DMAA_MEM_SIZE=0x80000000 \
    "$root/benchmarks/API/test_native_live_checkpoint.cpp" \
    "$root/util/m5/src/abi/x86/m5op.S" -o "$binary"

resolved_ramulator=$(LD_LIBRARY_PATH="$ramulator_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    ldd "$gem5" | awk '/libramulator[.]so/ && !found {print $3; found=1}')
[[ $(realpath "$resolved_ramulator") == $(realpath "$ramulator_lib") ]] || {
    echo "gem5 resolves a non-production Ramulator: $resolved_ramulator" >&2
    exit 1
}

{
    printf 'simulator_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'gem5=%s\n' "$gem5"
    printf 'ramulator=%s\n' "$ramulator_lib"
    printf 'ramulator_sha256=%s\n' "$actual_ramulator_sha"
    printf 'n=%s\n' "$n"
    printf 'timeout_seconds=%s\n' "$timeout_seconds"
    printf 'virtual_words_per_cycle=%s\n' "$virtual_words_per_cycle"
    printf 'virtual_max_outstanding_writes=%s\n' \
        "$virtual_max_outstanding_writes"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$out/provenance.txt"
sha256sum "$gem5" "$binary" \
    "$root/benchmarks/API/test_native_live_checkpoint.cpp" \
    "$root/benchmarks/API/MAA_gem5.hpp" "$ramulator_lib" \
    >"$out/artifact_sha256.txt"

config=$root/configs/deprecated/example/se.py
ramulator_config=$root/ext/ramulator2/ramulator2/example_gem5_config.yaml
runtime_library_path="$ramulator_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

set +e
LD_LIBRARY_PATH="$runtime_library_path" \
/usr/bin/time -f 'checkpoint_wall=%e checkpoint_rss_kb=%M rc=%x' \
    timeout 300 "$gem5" --listener-mode=off --outdir="$out" "$config" \
    --cpu-type AtomicSimpleCPU -n 1 --mem-size 2GB --max-checkpoints=1 \
    --cmd "$binary" --options "$n" >"$out/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" >"$out/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]] || {
    echo "initial checkpoint failed with rc=$checkpoint_rc" >&2
    exit 1
}
grep -Eq '^NATIVE_LIVE_CHECKPOINT_LAYOUT mem_size=2147483648 ' \
    "$out/checkpoint.log"

set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=1 LD_LIBRARY_PATH="$runtime_library_path" \
/usr/bin/time -f 'restore_wall=%e restore_rss_kb=%M rc=%x' \
    timeout "$timeout_seconds" "$gem5" --listener-mode=off \
    --debug-flags=Drain --outdir="$out" "$config" \
    --cpu-type X86O3CPU -r 1 -n 1 --mem-size 2GB \
    --sys-clock 3.2GHz --cpu-clock 3.2GHz \
    --caches --l1d_size=32kB --l1d_assoc=8 \
    --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 \
    --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8 \
    --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16 \
    --l1i_write_buffers=8 --l2cache --l2_size=256kB --l2_assoc=4 \
    --l2-hwp-type=StridePrefetcher --l2_mshrs=32 \
    --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16 \
    --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4 \
    --cacheline_size=64 --mem-type Ramulator2 \
    --ramulator-config "$ramulator_config" --mem-channels=1 --maa \
    --maa_num_maas=1 --maa_num_tile_elements=16384 \
    --maa_physical_tile_elements=0 --maa_num_indirect_units_per_maa=1 \
    --maa_retirement_cache_response_latency=1 \
    --maa_num_initial_row_table_slices=16 \
    --maa_virtual_combine_slots=384 --maa_virtual_combine_words=4096 \
    --maa_virtual_combine_ways=4 --maa_virtual_combine_banks=0 \
    --maa_virtual_response_slots=128 --maa_virtual_response_words=0 \
    --maa_virtual_response_word_pool=480 \
    --maa_virtual_words_per_cycle="$virtual_words_per_cycle" \
    --maa_virtual_max_outstanding_writes="$virtual_max_outstanding_writes" \
    --maa_virtual_masked_writes \
    --cmd "$binary" --options "$n" >"$out/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" >"$out/restore.exit"

[[ $(grep -Ec 'global: Trying to drain [0-9]+ objects[.]' \
    "$out/restore.log") -ge 3 ]]
grep -Fq 'global: Failed to drain system.maa' "$out/restore.log"
grep -Fq 'global: Failed to drain system.mem_ctrls' "$out/restore.log"
if [[ $restore_rc -eq 124 ]]; then
    ! grep -Fq 'NATIVE_LIVE_DRAIN_RETURNED' "$out/restore.log"
    echo "live checkpoint timed out: out=$out" >&2
    exit 1
fi

[[ $restore_rc -eq 0 ]] || {
    echo "restore failed with unexpected rc=$restore_rc" >&2
    exit 1
}
grep -Fq 'NATIVE_LIVE_DRAIN_RETURNED' "$out/restore.log"
grep -Eq '^NATIVE_LIVE_CHECKPOINT_RESULT n=[0-9]+ errors=0 hash=[0-9]+$' \
    "$out/restore.log"
[[ $(grep -Fxc 'ROI Ended' "$out/restore.log") -eq 1 ]]
echo "NATIVE_LIVE_CHECKPOINT_COMPLETED out=$out"
