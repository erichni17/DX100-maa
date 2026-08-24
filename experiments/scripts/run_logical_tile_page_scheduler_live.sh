#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5="$root/build/X86/gem5.opt"
source_file="$root/benchmarks/API/test_logical_tile_page_scheduler_live.cpp"
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"

[[ -x $gem5 ]] || { echo "missing gem5.opt: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
mkdir -p "$out/artifacts"
binary="$out/artifacts/test_logical_tile_page_scheduler_live"

"${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O2 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers -DGEM5 -DTILE_SIZE=16384 -DNUM_CORES=4 \
    -DNUM_TILES_PER_CORE=4 -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$binary"

checkpoint_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint"
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB
    --max-checkpoints=1 --cmd "$binary"
)
restore_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/run"
    --debug-flags=MAAVirtualTrace,MAATrace
    --debug-file=logical_page_trace.log
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
    --maa --maa_num_maas=1 --maa_num_tiles_per_core=4
    --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096
    --maa_logical_tile_page_scheduler
    --maa_num_initial_row_table_slices=16 --cmd "$binary"
)

{
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'logical_elements=16384\npage_elements=4096\npages=4\n'
    printf 'reserved_frames_per_maa=4\nreserved_lane_span=2\n'
    printf 'total_lanes=16\nreserved_lanes=8\nguest_visible_lanes=8\n'
    printf 'additional_payload_bytes=0\n'
    printf 'payload_reduction_vs_same_16-lane_native16=75%%\n'
    printf 'datatype=fp32\narchitectural_operations=9\ngenerations=2\n'
    printf 'expected_native_actions=80\nexpected_write_pages=24\n'
    printf 'expected_write_responses=6144\ncomparison_arms=0\n'
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
    "$out/source.diff" "$out/source_status.txt" > "$out/artifact_sha256.txt"

set +e
"${checkpoint_cmd[@]}" > "$out/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]] || {
    echo "checkpoint failed with rc=$checkpoint_rc" >&2
    exit 1
}
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
          "$out/checkpoint.log" || true) -eq 1 ]] || {
    echo "checkpoint log lacks its exact terminal marker" >&2
    exit 1
}
find "$out/checkpoint" -type f -print0 | sort -z | \
    xargs -0 sha256sum > "$out/checkpoint_sha256.txt"

set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
    "${restore_cmd[@]}" > "$out/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" > "$out/restore.exit"
[[ $restore_rc -eq 0 ]] || {
    echo "restore failed with rc=$restore_rc" >&2
    exit 1
}

expected='LOGICAL_PAGE_LIVE_RESULT operations=9 generations=2 a_hash=5238007371172236237 b_hash=4619008359347519206 unary_hash=8757546768500349369 distinct_vector_hash=1468879162217515462 self_vector_hash=9332068828147211593 dense_store_hash=9332068828147211593 c_hash=12485598873299661541 unary_generation2_hash=16675341876698374373 dense_store_generation2_hash=16675341876698374373 errors=0'
[[ $(grep -Fxc "$expected" "$out/restore.log" || true) -eq 1 ]] || {
    echo "missing exact logical-page hash guard" >&2
    exit 1
}
[[ $(grep -Ec \
          '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
          "$out/restore.log" || true) -eq 1 ]] || {
    echo "restore log lacks its exact m5_exit marker" >&2
    exit 1
}
[[ $(grep -Eic \
          'panic|fatal|assert|abort|segmentation fault|error:' \
          "$out/restore.log" || true) -eq 0 ]] || {
    echo "restore log contains a fatal/error marker" >&2
    exit 1
}

stats="$out/run/stats.txt"
trace="$out/run/logical_page_trace.log"
[[ -s $stats && -s $trace ]] || {
    echo "missing final stats or logical-page trace" >&2
    exit 1
}
sum_stat() {
    local suffix=$1
    awk -v suffix="$suffix" \
        '$1 ~ suffix "$" { value[$1]=$2 } END { for (name in value) total += value[name]; print total+0 }' \
        "$stats"
}
[[ $(sum_stat 'STR_PublishIssues') -eq 6144 ]]
[[ $(sum_stat 'STR_PublishAccepts') -eq 6144 ]]
[[ $(sum_stat 'STR_PublishWriteResponses') -eq 6144 ]]
[[ $(sum_stat 'STR_PublishTerminals') -eq 24 ]]
[[ $(grep -Fc 'event=logical_page_admit ' "$trace" || true) -eq 9 ]]
[[ $(grep -Fc 'event=logical_page_begin ' "$trace" || true) -eq 36 ]]
[[ $(grep -Fc 'event=logical_page_native_dispatch ' "$trace" || true) -eq 80 ]]
[[ $(grep -Fc 'event=logical_page_native_complete ' "$trace" || true) -eq 80 ]]
[[ $(grep -Fc 'event=logical_page_retire ' "$trace" || true) -eq 9 ]]
for pair in 0:12 1:24 2:4 3:8 4:8 5:8 6:16; do
    action=${pair%%:*}
    count=${pair##*:}
    [[ $(grep -Ec \
              "event=logical_page_native_dispatch .* action=$action " \
              "$trace" || true) -eq $count ]]
    [[ $(grep -Ec \
              "event=logical_page_native_complete .* action=$action " \
              "$trace" || true) -eq $count ]]
done
[[ $(grep -Fc 'event=spd_publish_issue ' "$trace" || true) -eq 6144 ]]
[[ $(grep -Fc 'event=spd_publish_accept ' "$trace" || true) -eq 6144 ]]
[[ $(grep -Fc 'event=spd_publish_response ' "$trace" || true) -eq 6144 ]]
[[ $(grep -Fc 'event=spd_publish_terminal ' "$trace" || true) -eq 24 ]]
grep -Eq 'event=logical_page_admit .*dst=0 dst_generation=2 ' "$trace"
grep -Eq 'event=logical_page_admit .*src1=0 src1_generation=2 .*dst=2 dst_generation=2 ' "$trace"
for resolved in \
    'num_maas=1' 'num_tiles_per_core=4' 'num_tile_elements=16384' \
    'physical_tile_elements=4096' 'logical_tile_page_scheduler=true' \
    'num_initial_row_table_slices=16'; do
    grep -Fqx "$resolved" "$out/run/config.ini"
done
grep -Eq '^simTicks[[:space:]]+[1-9][0-9]*' "$stats"

sha256sum "$out/checkpoint_sha256.txt" "$out/restore.log" \
    "$stats" "$out/run/config.ini" "$trace" > "$out/result_sha256.txt"
printf 'PASS logical_tile_page_scheduler_live out=%s\n' "$out"
