#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 GEM5_BIN OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
out=$(realpath -m "$2")
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
source_file="$root/benchmarks/API/test_hybrid_rmw_old_result.cpp"

[[ -x $gem5 ]] || { echo "missing gem5 binary: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence run from a dirty source tree" >&2
    exit 1
}

mkdir -p "$out/bin" "$out/checkpoint" "$out/run"
guest="$out/bin/test_hybrid_rmw_old_result"
"${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O2 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers -DGEM5 -DTILE_SIZE=16384 -DNUM_CORES=4 \
    -DMAA_MEM_SIZE=0x80000000 "$root/util/m5/src/abi/x86/m5op.S" \
    "$source_file" -o "$guest"

checkpoint_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" "$config"
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1
    --cmd "$guest"
)
restore_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/run"
    --debug-flags=MAAVirtualTrace --debug-file=old_result_trace.log
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
    --maa --maa_num_maas=1 --maa_num_indirect_units_per_maa=4
    --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096
    --maa_num_offset_table_entries=16384
    --maa_num_offset_table_epoch_entries=16384
    --maa_num_initial_row_table_slices=32 --cmd "$guest"
)

{
    printf 'schema=dx100.soa_jit.old_result.v1\n'
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'gem5_sha256='; sha256sum "$gem5" | awk '{print $1}'
    printf 'guest_sha256='; sha256sum "$guest" | awk '{print $1}'
    printf 'logical_elements=16384\nphysical_tile_elements=4096\n'
    printf 'num_indirect_units_per_maa=4\nrow_table_slices=32\n'
    printf 'old_result_line_credits=8\nold_result_payload_bytes=512\n'
    printf 'native_arms=0\nwall_timeout=none\n'
} >"$out/manifest.txt"

OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${checkpoint_cmd[@]}" \
    >"$out/checkpoint.log" 2>&1
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
          "$out/checkpoint.log" || true) -eq 1 ]]

OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${restore_cmd[@]}" \
    >"$out/run/restore.log" 2>&1

restore="$out/run/restore.log"
stats="$out/run/stats.txt"
trace="$out/run/old_result_trace.log"
config_ini="$out/run/config.ini"
expected='HYBRID_RMW_OLD_RESULT_RESULT generations=2 logical=16384 errors=0'
[[ $(grep -Fxc "$expected" "$restore" || true) -eq 1 ]]
[[ $(grep -Ec '^HYBRID_RMW_OLD_RESULT_GENERATION generation=[12] errors=0$' \
          "$restore" || true) -eq 2 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
          "$restore" || true) -eq 1 ]]
[[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
          "$restore" || true) -eq 0 ]]
[[ -s $stats && -s $trace ]]
[[ $(grep -Fxc 'num_indirect_units_per_maa=4' "$config_ini" || true) -eq 1 ]]
[[ $(grep -Fxc 'num_initial_row_table_slices=32' "$config_ini" || true) -eq 1 ]]

stat_sum() {
    local suffix=$1
    awk -v suffix="$suffix" '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ ("_" suffix "$") { sum += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            printf "%.0f\n", sum
            exit
        }
    ' "$stats"
}

instructions=$(stat_sum IND_SoaJitInstructions)
selected=$(stat_sum IND_SoaJitSelected)
rejected=$(stat_sum IND_SoaJitPredicateRejected)
captures=$(stat_sum IND_SoaJitOldResultCaptures)
issues=$(stat_sum IND_SoaJitOldResultWriteIssues)
responses=$(stat_sum IND_SoaJitOldResultWriteResponses)
credit_hwm=$(stat_sum IND_SoaJitOldResultCreditHighWater)
a_reads=$(stat_sum IND_SoaJitAReadIssues)
a_read_responses=$(stat_sum IND_SoaJitAReadResponses)
a_writes=$(stat_sum IND_SoaJitAWriteIssues)
a_write_responses=$(stat_sum IND_SoaJitAWriteResponses)
terminals=$(stat_sum IND_SoaJitTerminalCompletions)

[[ $instructions -eq 2 && $terminals -eq 2 ]]
[[ $selected -gt 0 && $rejected -gt 0 && \
   $((selected + rejected)) -eq 32768 ]]
[[ $captures -eq $selected && $issues -gt 0 && $issues -eq $responses ]]
[[ $credit_hwm -gt 0 && $credit_hwm -le 16 ]]
[[ $a_reads -gt 0 && $a_reads -eq $a_read_responses && \
   $a_reads -eq $a_writes && $a_writes -eq $a_write_responses ]]
[[ $(grep -Ec 'event=soa_jit_old_result_complete .* enabled=1 .* terminal=1$' \
          "$trace" || true) -eq 2 ]]
[[ $(grep -c 'event=soa_jit_old_result_issue ' "$trace" || true) -eq \
   $issues ]]
[[ $(grep -c 'event=soa_jit_old_result_response ' "$trace" || true) -eq \
   $responses ]]

sim_ticks=$(awk '$1 == "simTicks" { print $2; exit }' "$stats")
[[ $sim_ticks =~ ^[1-9][0-9]*$ ]]
{
    printf 'terminal=true\ncorrect=true\n'
    printf 'simTicks=%s\ninstructions=%s\nselected=%s\nrejected=%s\n' \
        "$sim_ticks" "$instructions" "$selected" "$rejected"
    printf 'old_result_captures=%s\nold_result_write_issues=%s\n' \
        "$captures" "$issues"
    printf 'old_result_write_responses=%s\ncredit_hwm_sum=%s\n' \
        "$responses" "$credit_hwm"
} >"$out/result.txt"
touch "$out/gate.complete"
cat "$out/result.txt"
echo "HYBRID_RMW_OLD_RESULT_SMOKE_PASS"
