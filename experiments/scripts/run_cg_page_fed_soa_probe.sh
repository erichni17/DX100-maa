#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5="$root/build/X86/gem5.opt"
frozen_ramulator=/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/input/libramulator.so
frozen_ramulator_sha256=76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753
source_file="$root/benchmarks/API/test_cg_page_fed_soa.cpp"
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"

hash_value() { sha256sum "$1" | awk '{print $1}'; }

[[ -x $gem5 ]] || { echo "missing source-built gem5: $gem5" >&2; exit 2; }
[[ -f $frozen_ramulator && $(hash_value "$frozen_ramulator") == \
   "$frozen_ramulator_sha256" ]] || {
    echo "missing or mismatched frozen Ramulator library" >&2
    exit 2
}
export LD_LIBRARY_PATH="$(dirname "$frozen_ramulator"):${LD_LIBRARY_PATH:-}"
resolved_ramulator=$(ldd "$gem5" | awk '$1 == "libramulator.so" {print $3}')
[[ -n $resolved_ramulator && \
   $(realpath "$resolved_ramulator") == $(realpath "$frozen_ramulator") ]]
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence from a dirty source worktree" >&2
    exit 1
}

mkdir -p "$out/artifacts"
binary="$out/artifacts/test_cg_page_fed_soa"
"${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++17 -O2 -Wall -Wextra -Werror \
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
    --debug-flags=MAAVirtualTrace --debug-file=cg_page_fed_soa_trace.log
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
    --maa_ncbus_width=32 --maa --maa_num_maas=1
    --maa_num_indirect_units_per_maa=4 --maa_num_tiles_per_core=8
    --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096
    --maa_num_offset_table_entries=16384
    --maa_num_offset_table_epoch_entries=16384
    --maa_num_initial_row_table_slices=16
    --maa_l2_uncacheable --maa_l3_uncacheable
    --maa_page_fed_soa_jit
    --maa_soa_jit_predicate_active_credits=16
    --maa_soa_jit_active_value_owners=32 --cmd "$binary"
)

{
    printf 'schema=dx100.cg.page_fed_soa_probe.v1\n'
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'gem5_path=%s\ngem5_sha256=%s\n' "$gem5" \
        "$(hash_value "$gem5")"
    printf 'ramulator_library_path=%s\nramulator_library_sha256=%s\n' \
        "$frozen_ramulator" "$frozen_ramulator_sha256"
    printf 'guest_sha256=%s\n' "$(hash_value "$binary")"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'scope=four_page_collision_ordinary_existing_soa_page_fed\n'
    printf 'pages=4\npage_elements=4096\nlogical_elements=16384\n'
    printf 'candidate_coherent_index_bytes=0\nindex_publish_pages=0\n'
    printf 'product_publish_pages=4\nproduct_publish_lines=1024\n'
    printf 'full_cg_index_write_lines_eliminated=11223040\n'
    printf 'performance_claim=0\n'
    printf 'checkpoint_command='
    printf '%q ' "${checkpoint_cmd[@]}"
    printf '\nrestore_command='
    printf '%q ' "${restore_cmd[@]}"
    printf '\n'
} > "$out/manifest.txt"

artifact_paths=(
    "$source_file" "$binary" "$gem5" "$frozen_ramulator" "$config"
    "$ramulator" "$0"
)
sha256sum "${artifact_paths[@]}" > "$out/artifacts.before.sha256"

set +e
"${checkpoint_cmd[@]}" > "$out/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
    "$out/checkpoint.log" || true) -eq 1 ]]
find "$out/checkpoint" -type f -print0 | sort -z | \
    xargs -0 sha256sum > "$out/checkpoint.before.sha256"

set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
    "${restore_cmd[@]}" > "$out/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" > "$out/restore.exit"
[[ $restore_rc -eq 0 ]]

expected='CG_PAGE_FED_SOA_RESULT pages=4 products=16384 ordinary_page_rmws=4 existing_soa_descriptors=1 page_fed_descriptors=1 page_fed_admissions=4 page_fed_open_responses=1 page_fed_closes=1 page_fed_command_responses=5 page_fed_total_responses=6 admitted_index_words=16384 index_publish_pages=0 product_publish_pages=4 product_publish_lines=1024 coherent_index_read_lines=0 coherent_index_write_lines=0 product_hash=2849837644626199427 ordinary_hash=17263589712773219203 existing_soa_hash=17263589712773219203 page_fed_hash=17263589712773219203 errors=0'
[[ $(grep -Fxc "$expected" "$out/restore.log" || true) -eq 1 ]]
[[ $(grep -Fxc 'CG_PAGE_FED_SOA_LEDGER persistent_state_bytes=16 command_doorbell_bytes=8 command_queue_bytes=0 index_payload_bytes=0 hidden_descriptor_bytes=0 row_offset_incremental_bytes=0 candidate_coherent_index_bytes=0 product_backing_bytes=65536 comparator_index_bytes=65536 probe_input_index_bytes=65536' "$out/restore.log" || true) -eq 1 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
    "$out/restore.log" || true) -eq 1 ]]
[[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
    "$out/restore.log" || true) -eq 0 ]]

stats="$out/run/stats.txt"
trace="$out/run/cg_page_fed_soa_trace.log"
[[ -s $stats && -s $trace ]]
sum_stat() {
    local suffix=$1
    awk -v suffix="$suffix" '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ ("_" suffix "$") { total += $2; found++ }
        /^---------- End Simulation Statistics/ && section == 1 {
            if (!found) exit 2
            printf "%.0f\n", total
            exit
        }
    ' "$stats"
}

[[ $(sum_stat 'IND_SoaJitPageFedOperations') -eq 1 ]]
[[ $(sum_stat 'IND_SoaJitPageFedAdmitCommands') -eq 4 ]]
[[ $(sum_stat 'IND_SoaJitPageFedCloseCommands') -eq 1 ]]
[[ $(sum_stat 'IND_SoaJitPageFedCommandResponses') -eq 5 ]]
[[ $(sum_stat 'IND_SoaJitPageFedAdmittedWords') -eq 16384 ]]
[[ $(sum_stat 'IND_SoaJitPageFedSpdIndexReads') -eq 16384 ]]
[[ $(sum_stat 'IND_SoaJitPageFedRowWrites') -eq 16384 ]]
[[ $(sum_stat 'IND_SoaJitPageFedCoherentIndexReadLines') -eq 0 ]]
[[ $(sum_stat 'IND_SoaJitPageFedCoherentIndexWriteLines') -eq 0 ]]
[[ $(sum_stat 'IND_SoaJitPageFedStateByteOperations') -eq 16 ]]
[[ $(sum_stat 'STR_PublishIssues') -eq 1024 ]]
[[ $(sum_stat 'STR_PublishAccepts') -eq 1024 ]]
[[ $(sum_stat 'STR_PublishWriteResponses') -eq 1024 ]]
[[ $(sum_stat 'STR_PublishTerminals') -eq 4 ]]
[[ $(grep -Fc 'event=soa_jit_page_fed_admit ' "$trace" || true) -eq 4 ]]
[[ $(grep -Fc 'event=soa_jit_page_fed_open_response ' "$trace" || true) -eq 1 ]]
[[ $(grep -Ec 'event=soa_jit_page_fed_complete .*opens=1 open_responses=1 admissions=4 closes=1 command_responses=5 total_abi_responses=6 .*coherent_index_read_lines=0 coherent_index_write_lines=0 index_payload_bytes=0 descriptor_payload_bytes=0 persistent_state_bytes=16 value_read_lines=16384 value_read_responses=16384 a_read_lines=256 a_write_lines=256 .*terminal=1$' "$trace" || true) -eq 1 ]]
for resolved in \
    'page_fed_soa_jit=true' 'num_maas=1' \
    'num_indirect_units_per_maa=4' 'num_tiles_per_core=8' \
    'num_tile_elements=16384' 'physical_tile_elements=4096' \
    'num_initial_row_table_slices=16' 'num_offset_table_entries=16384' \
    'num_offset_table_epoch_entries=16384'; do
    grep -Fqx "$resolved" "$out/run/config.ini"
done
grep -Eq '^simTicks[[:space:]]+[1-9][0-9]*' "$stats"
sim_ticks=$(awk '$1 == "simTicks" { print $2; exit }' "$stats")

find "$out/checkpoint" -type f -print0 | sort -z | \
    xargs -0 sha256sum > "$out/checkpoint.after.sha256"
cmp -s "$out/checkpoint.before.sha256" "$out/checkpoint.after.sha256"
sha256sum "${artifact_paths[@]}" > "$out/artifacts.after.sha256"
cmp -s "$out/artifacts.before.sha256" "$out/artifacts.after.sha256"
{
    printf 'terminal=true\ncorrect=true\nsimTicks=%s\n' "$sim_ticks"
    printf 'page_fed_operations=1\nadmissions=4\ncloses=1\n'
    printf 'open_responses=1\ncommand_responses=5\ntotal_abi_responses=6\n'
    printf 'index_publish_pages=0\ncoherent_index_lines=0\n'
    printf 'product_publish_pages=4\nproduct_publish_lines=1024\n'
    printf 'product_value_read_lines=16384\npersistent_state_bytes=16\n'
    printf 'performance_claim=0\n'
} > "$out/result.txt"
sha256sum "$out/checkpoint.before.sha256" "$out/restore.log" "$stats" \
    "$out/run/config.ini" "$trace" "$out/result.txt" \
    > "$out/result_sha256.txt"
printf 'PASS\n' > "$out/gate.complete"
printf 'PASS cg_page_fed_soa_probe simTicks=%s out=%s\n' "$sim_ticks" "$out"
