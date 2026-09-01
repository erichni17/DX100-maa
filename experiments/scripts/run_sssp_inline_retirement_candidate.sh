#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTDIR" >&2
    exit 2
fi
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
mkdir -p "$out"/{bin,graph,checkpoint,run,provenance}
status=FAIL
trap 'rc=$?; printf '\''{"terminal":true,"status":"%s","driver_rc":%d}\n'\'' "$status" "$rc" >"$out/terminal.json"' EXIT

gem5=${SSSP_INLINE_GEM5:-$root/build/X86/gem5.opt}
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
frozen_ramulator=/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/input/libramulator.so
accepted=/data1/nier/worktrees/codex-coordination/sessions/sssp-locality-matched-micro-20260901-20260831-225546-d4d67a8b/evidence/sssp-locality-matched-micro-r1/campaign
graph="$out/graph/sssp_locality_matched.wsg"
guest="$out/bin/sssp_inline_retirement_fp"
fingerprint='SSSP_FINGERPRINT vertices=69633 reached=69633 unreachable=0 distance_sum=135168 max_distance=2 hash_a=a0531a7ddb9387df hash_b=39f1ea63bc8817e8 triangle_violations=0 missing_predecessors=0 nonpositive_weights=0 negative_distances=0 result=PASS'

[[ -x $gem5 && -f $frozen_ramulator ]] || exit 2
cp -- "$accepted/graph/sssp_locality_matched.wsg" "$graph"
[[ $(sha256sum "$graph" | awk '{print $1}') == 902d3b2dfceddc44a354ce2f7a9a3d572327c2c2fc7ff99190baff74d059c3e3 ]]
"${CXX:-g++}" -I"$root/benchmarks/gapbs/src" \
    -I"$root/benchmarks/API" -I"$root/include" -I"$root/util/m5/src" \
    -std=c++11 -O3 -Wall -Wextra -Werror -Wno-ignored-qualifiers \
    -Wno-unused-parameter -fopenmp -DGEM5 -DMAA -DNUM_CORES=4 \
    -DNUM_TILES_PER_CORE=8 -DTILE_SIZE=16384 \
    -DMAA_CONSUMER_TILE_SIZE=4096 -DMAA_MEM_SIZE=0x80000000 \
    -DSSSP_FP_ENABLE=1 -DSSSP_OLD_RESULT_HYBRID=1 \
    -DSSSP_INLINE_OPERAND_RETIREMENT=1 "$root/util/m5/src/abi/x86/m5op.S" \
    "$root/benchmarks/gapbs/src/sssp.cc" -o "$guest"

export LD_LIBRARY_PATH="$(dirname "$frozen_ramulator"):${LD_LIBRARY_PATH:-}"
ldd "$gem5" >"$out/provenance/gem5.ldd.txt"
resolved=$(awk '$1 == "libramulator.so" {print $3}' "$out/provenance/gem5.ldd.txt")
[[ -n $resolved && $(realpath "$resolved") == $(realpath "$frozen_ramulator") ]]
sha256sum "$gem5" "$guest" "$graph" "$frozen_ramulator" "$config" \
    "$ramulator" >"$out/provenance/artifacts.sha256"
git -C "$root" rev-parse HEAD >"$out/provenance/source.commit"
git -C "$root" diff >"$out/provenance/source.diff"

options="-f $graph -n 1 -r 0 -d 1 -v"
checkpoint_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" "$config"
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1
    --cmd "$guest" --options "$options"
)
printf '%q ' "${checkpoint_cmd[@]}" >"$out/provenance/checkpoint.command"
printf '\n' >>"$out/provenance/checkpoint.command"
env OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${checkpoint_cmd[@]}" \
    >"$out/checkpoint.log" 2>&1
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' "$out/checkpoint.log" || true) -eq 1 ]]

restore_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/run" \
    --debug-flags=MAAVirtualTrace --debug-file=maa-virtual-trace.log
    "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --checkpoint-dir="$out/checkpoint" --sys-clock 3.2GHz --cpu-clock 3.2GHz
    --caches --l1d_size=32kB --l1d_assoc=8
    --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8
    --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher
    --l1i_mshrs=16 --l1i_write_buffers=8 --l2cache --l2_size=256kB
    --l2_assoc=4 --l2-hwp-type=StridePrefetcher --l2_mshrs=32
    --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
    --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4 --cacheline_size=64
    --mem-type Ramulator2 --ramulator-config "$ramulator" --mem-channels=2
    --maa_ncbus_width=32 --maa --maa_num_maas=1
    --maa_num_indirect_units_per_maa=4 --maa_num_tile_elements=16384
    --maa_physical_tile_elements=4096 --maa_num_offset_table_entries=16384
    --maa_num_offset_table_epoch_entries=16384
    --maa_num_initial_row_table_slices=32 --maa_l2_uncacheable
    --maa_l3_uncacheable --maa_page_fed_soa_jit
    --maa_inline_operand_page_fed_rmw --maa_soa_jit_predicate_active_credits=1
    --maa_soa_jit_active_contexts=8 --maa_soa_jit_value_lookahead=1
    --maa_soa_jit_value_prefetch_credits=0 --maa_soa_jit_active_value_owners=64
    --maa_soa_jit_apply_lanes=1 --cmd "$guest" --options "$options"
)
printf '%q ' "${restore_cmd[@]}" >"$out/provenance/restore.command"
printf '\n' >>"$out/provenance/restore.command"
env OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${restore_cmd[@]}" \
    >"$out/run/restore.log" 2>&1

stats="$out/run/stats.txt"
trace="$out/run/maa-virtual-trace.log"
log="$out/run/restore.log"
[[ -s $stats && -s $trace ]]
[[ $(grep -Fxc "$fingerprint" "$log" || true) -eq 1 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' "$log" || true) -eq 1 ]]
[[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' "$log" || true) -eq 0 ]]
terminal=$(grep '^SSSP_INLINE_OPERAND_RETIREMENT_TERMINAL ' "$log")
[[ $(grep -c '^SSSP_INLINE_OPERAND_RETIREMENT_TERMINAL ' "$log") -eq 1 ]]
for token in routed_windows=4 logical_operations=4 paired_admissions=16 \
    operand_insertions=65536 operand_consumptions=65536 \
    retirement_records=65536 retirement_acked_lines=8192 \
    index_publish_pages=0 value_publish_pages=0 old_result_words=0 \
    physical_spd_words=4096 inline_operand_live_bytes=65536 \
    row_offset_incremental_bytes=0 incremental_sram_bytes_per_unit=592 \
    external_retirement_ring_bytes_per_unit=32768 counts_close=1; do
    [[ " $terminal " == *" $token "* ]]
done

stat_sum() { awk -v suffix="$2" '
 /^---------- Begin Simulation Statistics/ {s++}
 s==1 && ($1=="system.maa." suffix || $1~("_" suffix "$")) {v+=$2}
 /^---------- End Simulation Statistics/ && s==1 {printf "%.0f\n",v; exit}' "$1"; }
stat_exact() { awk -v key="$2" '
 /^---------- Begin Simulation Statistics/ {s++}
 s==1 && $1==key {print $2; exit}' "$1"; }
ticks=$(stat_exact "$stats" simTicks)
cache_lines=$(stat_sum "$stats" IND_NumCacheLineInserted)
rows=$(stat_sum "$stats" IND_NumRowsInserted)
soa=$(stat_sum "$stats" IND_SoaJitInstructions)
selected=$(stat_sum "$stats" IND_SoaJitSelected)
publisher=$(stat_sum "$stats" STR_PublishIssues)
epoch_drains=$(stat_sum "$stats" IND_SoaJitEpochDrains)
a_reads=$(stat_sum "$stats" IND_SoaJitAReadIssues)
a_read_responses=$(stat_sum "$stats" IND_SoaJitAReadResponses)
a_writes=$(stat_sum "$stats" IND_SoaJitAWriteIssues)
a_write_responses=$(stat_sum "$stats" IND_SoaJitAWriteResponses)
[[ $ticks -le 840612362 && $cache_lines -lt 345420 && $rows -lt 43416 ]]
[[ $soa -eq 4 && $selected -eq 65536 && $publisher -eq 0 && $epoch_drains -eq 0 ]]
[[ $a_reads -gt 0 && $a_reads -eq $a_read_responses && \
   $a_reads -eq $a_writes && $a_writes -eq $a_write_responses ]]
[[ $(grep -c 'event=inline_operand_retirement_complete ' "$trace") -eq 4 ]]
[[ $(grep -c 'terminal_storage_closure=1 terminal=1' "$trace") -eq 4 ]]
speedup=$(awk -v base=672489890 -v candidate="$ticks" 'BEGIN {printf "%.9f",base/candidate}')
cat >"$out/result.txt" <<EOF
terminal=true
correct=true
candidate_only=true
graph_sha256=902d3b2dfceddc44a354ce2f7a9a3d572327c2c2fc7ff99190baff74d059c3e3
native4_frozen_simTicks=672489890
native16_frozen_simTicks=618231027
candidate_simTicks=$ticks
speedup_vs_frozen_native4=$speedup
cache_lines=$cache_lines
rows=$rows
full_s22_runs=0
native_control_reruns=0
EOF
status=PASS
