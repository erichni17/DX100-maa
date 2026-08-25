#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5="$root/build/X86/gem5.opt"
source_file="$root/benchmarks/API/test_cg_product_handoff.cpp"
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"

[[ -x $gem5 ]] || { echo "missing frozen gem5.opt: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence from a dirty source worktree" >&2
    exit 1
}
mkdir -p "$out/artifacts"
binary="$out/artifacts/test_cg_product_handoff"

"${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O2 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers -DGEM5 -DTILE_SIZE=16384 -DNUM_CORES=4 \
    -DNUM_TILES_PER_CORE=8 -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$binary"

checkpoint_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint"
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB
    --max-checkpoints=1 --cmd "$binary"
)
restore_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/run"
    --debug-flags=MAATrace --debug-file=cg_product_handoff_trace.log
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
    --maa --maa_num_maas=1 --maa_num_tiles_per_core=8
    --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096
    --maa_num_initial_row_table_slices=16
    --maa_soa_jit_predicate_active_credits=16
    --maa_soa_jit_active_value_owners=32 --cmd "$binary"
)

{
    printf 'schema=dx100.cg.product_handoff_probe.v1\n'
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'scope=bitwise_physical_mul_to_coherent_publish_and_one_soa_jit_add\n'
    printf 'pages=4\npage_elements=4096\nlogical_elements=16384\n'
    printf 'physical_product_pages=4\nselected_sets=1\nmasked_passes=0\n'
    printf 'ordinary_page_rmws=4\nsoa_jit_descriptors=1\n'
    printf 'host_spd_reads=0\nperformance_claim=0\n'
    printf 'provenance=gem5_opt_and_ramulator_yaml_sha256_below\n'
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

expected='CG_PRODUCT_HANDOFF_RESULT pages=4 products=16384 published_index_words=16384 published_product_words=16384 index_hash=14754458253095254915 prepublication_product_hash=2849837644626199427 published_product_hash=2849837644626199427 ordinary_destination_hash=17263589712773219203 soa_destination_hash=17263589712773219203 exact_product_words=16384 exact_destination_words=4096 ordinary_page_rmws=4 soa_jit_descriptors=1 masked_passes=0 errors=0'
[[ $(grep -Fxc "$expected" "$out/restore.log" || true) -eq 1 ]] || {
    echo "missing exact CG product-handoff result" >&2
    exit 1
}
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
    "$out/restore.log" || true) -eq 1 ]] || {
    echo "restore log lacks its exact m5_exit marker" >&2
    exit 1
}
[[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
    "$out/restore.log" || true) -eq 0 ]] || {
    echo "restore log contains a fatal/error marker" >&2
    exit 1
}

stats="$out/run/stats.txt"
trace="$out/run/cg_product_handoff_trace.log"
[[ -s $stats && -s $trace ]] || {
    echo "missing final stats or MAA trace" >&2
    exit 1
}
sum_stat() {
    local suffix=$1
    awk -v suffix="$suffix" \
        '$1 ~ suffix "$" { value[$1]=$2 } END { for (name in value) total += value[name]; print total+0 }' \
        "$stats"
}
# Four index pages plus four product pages, 256 exact 64B writes per page.
[[ $(sum_stat 'STR_PublishIssues') -eq 2048 ]]
[[ $(sum_stat 'STR_PublishAccepts') -eq 2048 ]]
[[ $(sum_stat 'STR_PublishWriteResponses') -eq 2048 ]]
[[ $(sum_stat 'STR_PublishTerminals') -eq 8 ]]
[[ $(grep -Fc 'event=spd_publish_issue ' "$trace" || true) -eq 2048 ]]
[[ $(grep -Fc 'event=spd_publish_accept ' "$trace" || true) -eq 2048 ]]
[[ $(grep -Fc 'event=spd_publish_response ' "$trace" || true) -eq 2048 ]]
[[ $(grep -Fc 'event=spd_publish_terminal ' "$trace" || true) -eq 8 ]]
for resolved in \
    'num_maas=1' 'num_tiles_per_core=8' 'num_tile_elements=16384' \
    'physical_tile_elements=4096' 'num_initial_row_table_slices=16' \
    'soa_jit_predicate_active_credits=16' 'soa_jit_active_value_owners=32'; do
    grep -Fqx "$resolved" "$out/run/config.ini"
done
grep -Eq '^simTicks[[:space:]]+[1-9][0-9]*' "$stats"

sim_ticks=$(awk '$1 == "simTicks" { value=$2 } END { print value+0 }' "$stats")
{
    printf 'terminal=true\ncorrect=true\n'
    printf 'simTicks=%s\n' "$sim_ticks"
    printf 'published_pages=8\npublish_issues=2048\npublish_accepts=2048\n'
    printf 'publish_write_responses=2048\npublish_terminals=8\n'
    printf 'ordinary_page_rmws=4\nsoa_jit_descriptors=1\n'
    printf 'host_spd_reads=0\nperformance_claim=0\n'
} > "$out/result.txt"
sha256sum "$out/checkpoint_sha256.txt" "$out/restore.log" "$stats" \
    "$out/run/config.ini" "$trace" > "$out/result_sha256.txt"
printf 'PASS cg_product_handoff_probe simTicks=%s out=%s\n' "$sim_ticks" "$out"
