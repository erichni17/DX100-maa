#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5="$root/build/X86/gem5.opt"
source_file="$root/benchmarks/API/test_response_bearing_spd_publish.cpp"
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"

[[ -x $gem5 ]] || { echo "missing production gem5.opt: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing timed evidence from a dirty source worktree" >&2
    exit 1
}
mkdir -p "$out/artifacts"
binary="$out/artifacts/test_response_bearing_spd_publish"

"${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O2 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers -DGEM5 -DTILE_SIZE=16384 -DNUM_CORES=4 \
    -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$binary"

checkpoint_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint"
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB
    --max-checkpoints=1 --cmd "$binary"
)
restore_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/run"
    --debug-flags=MAATrace --debug-file=spd_publish_trace.log
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
    --maa_physical_tile_elements=4096
    --maa_num_initial_row_table_slices=16 --cmd "$binary"
)

{
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'scope=FP32_GZP_logical16_page0_correctness_only\n'
    printf 'logical_elements=16384\nphysical_tile_elements=4096\n'
    printf 'publisher_credits=8\ncache_line_bytes=64\n'
    printf 'speedup_claim=0\n'
    printf 'checkpoint_command='
    printf '%q ' "${checkpoint_cmd[@]}"
    printf '\nrestore_command='
    printf '%q ' "${restore_cmd[@]}"
    printf '\n'
} > "$out/manifest.txt"
sha256sum "$source_file" "$binary" "$gem5" "$config" "$ramulator" \
    > "$out/artifact_sha256.txt"

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

expected='RESPONSE_BEARING_SPD_PUBLISH_RESULT elements=4096 logical_page=0 logical_offset=0 generation=1 expected_hash=16924436845436167371 cpu_hash=16924436845436167371 maa_exact_words=4096 maa_all_equal=1 cpu_errors=0 maa_errors=0 errors=0'
[[ $(grep -Fxc "$expected" "$out/restore.log" || true) -eq 1 ]] || {
    echo "missing exact CPU/MAA backing-data result" >&2
    exit 1
}
[[ $(grep -Ec \
          '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
          "$out/restore.log" || true) -eq 1 ]] || {
    echo "restore log lacks its exact m5_exit marker" >&2
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
sim_ticks=$(awk '$1 == "simTicks" { value=$2 } END { print value+0 }' \
    "$out/run/stats.txt")
[[ $sim_ticks -gt 0 ]] || { echo "missing positive simTicks" >&2; exit 1; }

stat_value() {
    local suffix=$1
    awk -v suffix="$suffix" '$1 ~ suffix "$" { value=$2 } END { print value+0 }' \
        "$out/run/stats.txt"
}
[[ $(stat_value 'S0_STR_PublishIssues') -eq 256 ]]
[[ $(stat_value 'S0_STR_PublishAccepts') -eq 256 ]]
[[ $(stat_value 'S0_STR_PublishWriteResponses') -eq 256 ]]
[[ $(stat_value 'S0_STR_PublishCreditHWM') -eq 8 ]]
[[ $(stat_value 'S0_STR_PublishTerminals') -eq 1 ]]
[[ $(stat_value 'S0_STR_PublishCreditStalls') -gt 0 ]]

trace="$out/run/spd_publish_trace.log"
[[ -s $trace ]] || { echo "missing publisher trace" >&2; exit 1; }
[[ $(grep -c 'event=spd_publish_issue ' "$trace" || true) -eq 256 ]]
[[ $(grep -c 'event=spd_publish_accept ' "$trace" || true) -eq 256 ]]
[[ $(grep -c 'event=spd_publish_response ' "$trace" || true) -eq 256 ]]
[[ $(grep -c 'event=spd_publish_terminal ' "$trace" || true) -eq 1 ]]
for resolved in \
    'num_maas=1' \
    'num_tile_elements=16384' \
    'physical_tile_elements=4096'; do
    grep -Fqx "$resolved" "$out/run/config.ini" || {
        echo "missing resolved MAA geometry: $resolved" >&2
        exit 1
    }
done

{
    printf 'terminal=true\n'
    printf 'correct=true\n'
    printf 'simTicks=%s\n' "$sim_ticks"
    printf 'issues=256\naccepts=256\nwrite_responses=256\n'
    printf 'credit_hwm=8\nterminals=1\n'
    printf 'retries=%s\n' "$(stat_value 'S0_STR_PublishRetries')"
    printf 'credit_stalls=%s\n' \
        "$(stat_value 'S0_STR_PublishCreditStalls')"
} > "$out/result.txt"
sha256sum "$out/checkpoint_sha256.txt" "$out/restore.log" \
    "$out/run/stats.txt" "$out/run/config.ini" "$trace" \
    > "$out/result_sha256.txt"
printf 'PASS response_bearing_spd_publish simTicks=%s out=%s\n' \
    "$sim_ticks" "$out"
