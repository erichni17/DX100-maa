#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5="$root/build/X86/gem5.opt"
source_file="$root/benchmarks/API/test_logical_spd_cache_live.cpp"
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"

[[ -x $gem5 ]] || { echo "missing gem5.opt: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
mkdir -p "$out/artifacts"
binary="$out/artifacts/test_logical_spd_cache_live"

"${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O2 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers \
    -DGEM5 -DTILE_SIZE=16384 -DNUM_CORES=4 \
    -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/src/abi/x86/m5op.S" "$source_file" \
    -o "$binary"

checkpoint_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint"
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB
    --max-checkpoints=1 --cmd "$binary"
)
restore_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/run"
    --debug-flags=MAAVirtualTrace --debug-file=logical_spd_trace.log
    "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --checkpoint-dir="$out/checkpoint"
    --sys-clock 3.2GHz --cpu-clock 3.2GHz
    --caches --l1d_size=32kB --l1d_assoc=8 --l1d_mshrs=16
    --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8
    --l1i_mshrs=16 --l1i_write_buffers=8
    --l2cache --l2_size=256kB --l2_assoc=4 --l2_mshrs=32
    --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
    --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
    --cacheline_size=64 --mem-type Ramulator2
    --ramulator-config "$ramulator" --mem-channels=1
    --maa --maa_num_maas=1 --maa_num_tile_elements=16384
    --maa_physical_tile_elements=2048
    --maa_num_initial_row_table_slices=16 --cmd "$binary"
)

{
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'logical_elements=16384\nphysical_page_elements=2048\n'
    printf 'private_slots=2\nprivate_slot_bytes=16384\nprivate_payload_bytes=32768\n'
    printf 'hardware_bytes=32768\nmetadata_bytes=0\n'
    printf 'isoarea_timing_claim=0\n'
    printf 'checkpoint_command='
    printf '%q ' "${checkpoint_cmd[@]}"
    printf '\nrestore_command='
    printf '%q ' "${restore_cmd[@]}"
    printf '\n'
} > "$out/manifest.txt"
git -C "$root" status --short > "$out/source_status.txt"
git -C "$root" diff --binary > "$out/source.diff"
[[ ! -s $out/source_status.txt ]] || {
    echo "refusing smoke from a non-clean source status" >&2
    exit 1
}
[[ ! -s $out/source.diff ]] || {
    echo "refusing smoke from a nonempty source diff" >&2
    exit 1
}
sha256sum "$source_file" "$binary" "$gem5" "$config" "$ramulator" \
    "$out/source.diff" "$out/source_status.txt" \
    > "$out/artifact_sha256.txt"

set +e
timeout 600 "${checkpoint_cmd[@]}" > "$out/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]] || {
    echo "checkpoint failed with rc=$checkpoint_rc" >&2
    exit 1
}
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
          "$out/checkpoint.log" || true) -eq 1 ]] || {
    echo "checkpoint log does not contain one exact exit marker" >&2
    exit 1
}
find "$out/checkpoint" -type f -print0 | sort -z | \
    xargs -0 sha256sum > "$out/checkpoint_sha256.txt"

set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 timeout 600 \
    "${restore_cmd[@]}" > "$out/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" > "$out/restore.exit"
[[ $restore_rc -eq 0 ]] || {
    echo "restore failed with rc=$restore_rc" >&2
    exit 1
}

expected='LOGICAL_SPD_CACHE_LIVE_RESULT elements=16384 pages=8 expected_hash=7303085050985348899 output_hash=7303085050985348899 errors=0'
[[ $(grep -Fxc "$expected" "$out/restore.log" || true) -eq 1 ]] || {
    echo "missing exact logical SPD result" >&2
    exit 1
}
[[ $(grep -Ec \
          '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
          "$out/restore.log" || true) -eq 1 ]] || {
    echo "restore log does not contain one exact m5_exit marker" >&2
    exit 1
}
fatal_count=$(grep -Eic \
    'panic|fatal|assert|abort|segmentation fault|error:' \
    "$out/restore.log" || true)
[[ $fatal_count -eq 0 ]] || {
    echo "restore log contains $fatal_count fatal/error markers" >&2
    exit 1
}
[[ -s $out/run/stats.txt ]] || { echo "missing final stats" >&2; exit 1; }
grep -Eq '^simTicks[[:space:]]+[1-9][0-9]*' "$out/run/stats.txt" || {
    echo "missing positive simTicks" >&2
    exit 1
}
for resolved in \
    'num_maas=1' \
    'num_tile_elements=16384' \
    'physical_tile_elements=2048' \
    'num_initial_row_table_slices=16'; do
    grep -Fqx "$resolved" "$out/run/config.ini" || {
        echo "missing resolved MAA geometry: $resolved" >&2
        exit 1
    }
done
grep -Fq 'event=logical_spd_complete' \
    "$out/run/logical_spd_trace.log" || {
    echo "missing logical SPD completion trace" >&2
    exit 1
}

sha256sum "$out/checkpoint_sha256.txt" "$out/restore.log" \
    "$out/run/stats.txt" "$out/run/config.ini" \
    "$out/run/logical_spd_trace.log" > "$out/result_sha256.txt"
printf 'PASS logical_spd_cache_live_smoke out=%s\n' "$out"
