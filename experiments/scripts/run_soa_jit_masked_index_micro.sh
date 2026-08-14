#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "usage: $0 OUTDIR [GEM5]" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5=$(realpath "${2:-$root/build/X86/gem5.opt}")
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
guest="$out/inputs/test_hybrid_rmw_soa"
selector="$out/inputs/treatment.txt"
checkpoint="$out/checkpoint"
cxx=${CXX:-g++}

[[ -x $gem5 ]] || { echo "missing gem5: $gem5" >&2; exit 2; }
[[ -f $config && -f $ramulator ]] || exit 2
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
mkdir -p "$out/inputs" "$checkpoint" "$out/runs"

"$cxx" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O2 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers -DGEM5 -DTILE_SIZE=16384 -DNUM_CORES=4 \
    -DMAA_MEM_SIZE=0x80000000 "$root/util/m5/src/abi/x86/m5op.S" \
    "$root/benchmarks/API/test_hybrid_rmw_soa.cpp" -o "$guest"

printf '%s\n' soa > "$selector"
make_checkpoint() {
    timeout 300 "$gem5" --listener-mode=off --outdir="$checkpoint" \
        "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
        --max-checkpoints=1 --cmd "$guest" \
        --options "selector $selector" \
        >"$checkpoint/checkpoint.log" 2>&1
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
              "$checkpoint/checkpoint.log" || true) -eq 1 ]] || {
        echo "micro did not produce one exact checkpoint" >&2
        exit 1
    }
}
make_checkpoint "selector $selector"

stat_sum() {
    local stats=$1 suffix=$2
    awk -v suffix="$suffix" '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ ("_" suffix "$") { sum += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            printf "%.0f\n", sum
            exit
        }
    ' "$stats"
}

common=(
    "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --checkpoint-dir="$checkpoint" --sys-clock 3.2GHz --cpu-clock 3.2GHz
    --caches --l1d_size=32kB --l1d_assoc=8 --l1d_mshrs=16
    --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8 --l1i_mshrs=16
    --l1i_write_buffers=8 --l2cache --l2_size=256kB --l2_assoc=4
    --l2_mshrs=32 --l2_write_buffers=16 --l3cache --l3_size=8MB
    --l3_assoc=16 --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
    --cacheline_size=64 --mem-type Ramulator2 --ramulator-config "$ramulator"
    --mem-channels=1 --maa --maa_num_maas=1
    --maa_num_indirect_units_per_maa=1 --maa_num_tile_elements=16384
    --maa_physical_tile_elements=4096 --maa_num_offset_table_entries=16384
    --maa_num_offset_table_epoch_entries=16384
    --maa_num_initial_row_table_slices=16 --maa_virtual_index_buffer_lines=8
    --maa_soa_jit_predicate_active_credits=16
    --maa_soa_jit_active_contexts=8 --maa_soa_jit_value_lookahead=8
    --maa_soa_jit_value_cache_enable --maa_soa_jit_active_value_owners=32
    --maa_soa_jit_apply_lanes=1 --cmd "$guest"
)

printf 'arm\tmode\tsimTicks\tfill_cycles\tindex_lines\tpredicate_lines\tselected\trejected\toutput_hash\n' \
    > "$out/results.tsv"

run_arm() {
    local arm=$1 mode=$2 run="$out/runs/$1"
    mkdir "$run"
    printf '%s\n' "$mode" > "$selector"
    cp "$selector" "$run/frozen_treatment.txt"
    if timeout 1800 "$gem5" --listener-mode=off --outdir="$run" \
        --debug-flags=MAAVirtualTrace --debug-file=soa_jit_trace.log \
        "${common[@]}" >"$run/restore.log" 2>&1; then
        printf '0\n' > "$run/wrapper.rc"
    else
        rc=$?
        printf '%s\n' "$rc" > "$run/wrapper.rc"
        echo "$arm gem5 failed with rc=$rc" >&2
        exit "$rc"
    fi

    local result stats="$run/stats.txt"
    result=$(grep -E '^HYBRID_RMW_SOA_RESULT ' "$run/restore.log" || true)
    [[ $(grep -Ec '^HYBRID_RMW_SOA_RESULT ' "$run/restore.log" || true) -eq 1 &&
       $result =~ (^|[[:space:]])mode=$mode($|[[:space:]]) &&
       $result =~ (^|[[:space:]])logical=16384($|[[:space:]]) &&
       $result =~ (^|[[:space:]])operations=2($|[[:space:]]) &&
       $result =~ (^|[[:space:]])errors=0($|[[:space:]]) ]] || {
        echo "$arm failed its exact result contract" >&2
        exit 1
    }
    grep -Fqx 'ROI Ended' "$run/restore.log"
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
              "$run/restore.log" || true) -eq 1 ]]
    [[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
              "$run/restore.log" || true) -eq 0 ]]
    [[ -s $stats && $(grep -c '^---------- Begin Simulation Statistics' \
                          "$stats") -ge 1 ]]
    for resolved in num_tile_elements=16384 physical_tile_elements=4096 \
        num_offset_table_entries=16384 \
        num_offset_table_epoch_entries=16384; do
        grep -Fqx "$resolved" "$run/config.ini"
    done

    local instructions terminal selected rejected predicate_lines
    local predicate_responses index_lines values aliases areads awrites
    instructions=$(stat_sum "$stats" IND_SoaJitInstructions)
    terminal=$(stat_sum "$stats" IND_SoaJitTerminalCompletions)
    selected=$(stat_sum "$stats" IND_SoaJitSelected)
    rejected=$(stat_sum "$stats" IND_SoaJitPredicateRejected)
    predicate_lines=$(stat_sum "$stats" IND_SoaJitPredicateLineReads)
    predicate_responses=$(stat_sum "$stats" IND_SoaJitPredicateLineResponses)
    index_lines=$(stat_sum "$stats" IND_VirtIndexLineReads)
    values=$(stat_sum "$stats" IND_SoaJitValueDeliveries)
    aliases=$(stat_sum "$stats" IND_SoaJitAliasesApplied)
    areads=$(stat_sum "$stats" IND_SoaJitAReadIssues)
    awrites=$(stat_sum "$stats" IND_SoaJitAWriteIssues)
    [[ $instructions -eq 2 && $terminal -eq 2 &&
       $((selected + rejected)) -eq 32768 && $selected -gt 0 &&
       $rejected -gt 0 && $predicate_lines -eq $predicate_responses &&
       $values -eq $selected && $aliases -eq $selected &&
       $areads -eq $awrites ]]
    if [[ $mode == soa ]]; then
        [[ $predicate_lines -eq 2048 ]]
        [[ $(grep -c 'event=soa_jit_complete .*predicate_mode=separate_array' \
                  "$run/soa_jit_trace.log") -eq 2 ]]
    else
        [[ $predicate_lines -eq 0 ]]
        [[ $(grep -c 'event=soa_jit_complete .*predicate_mode=masked_index' \
                  "$run/soa_jit_trace.log") -eq 2 ]]
        [[ $(grep -c 'masked_index_compare_bits=32' \
                  "$run/soa_jit_trace.log") -eq 2 ]]
        [[ $(grep -c 'masked_index_mode_state_bits=1' \
                  "$run/soa_jit_trace.log") -eq 2 ]]
        [[ $(grep -c 'masked_index_additional_buffer_bytes=0' \
                  "$run/soa_jit_trace.log") -eq 2 ]]
    fi

    local ticks fill hash
    ticks=$(awk '$1 == "simTicks" { print $2; exit }' "$stats")
    fill=$(stat_sum "$stats" IND_CyclesFill)
    hash=$(sed -n 's/.* output_hash=\([0-9][0-9]*\).*/\1/p' <<<"$result")
    [[ $ticks =~ ^[1-9][0-9]*$ && $fill =~ ^[1-9][0-9]*$ &&
       $hash =~ ^[0-9]+$ ]]
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$arm" "$mode" "$ticks" "$fill" "$index_lines" \
        "$predicate_lines" "$selected" "$rejected" "$hash" \
        >> "$out/results.tsv"
}

run_arm separate_predicate soa
run_arm masked_index soa-masked-index

python3 - "$out/results.tsv" "$out/summary.txt" <<'PY'
import csv
import sys

rows = list(csv.DictReader(open(sys.argv[1]), delimiter="\t"))
if [row["arm"] for row in rows] != ["separate_predicate", "masked_index"]:
    raise SystemExit("unexpected arm order")
base, masked = rows
for key in ("index_lines", "selected", "rejected", "output_hash"):
    if base[key] != masked[key]:
        raise SystemExit(f"unmatched {key}: {base[key]} != {masked[key]}")
lines_avoided = int(base["predicate_lines"]) - int(masked["predicate_lines"])
bytes_avoided = lines_avoided * 64
if lines_avoided != 2048 or bytes_avoided != 131072:
    raise SystemExit("predicate traffic delta is not exactly two 64KiB arrays")
with open(sys.argv[2], "w") as output:
    output.write(f"separate_simTicks={base['simTicks']}\n")
    output.write(f"masked_simTicks={masked['simTicks']}\n")
    output.write(
        f"simTicks_delta={int(masked['simTicks']) - int(base['simTicks'])}\n"
    )
    output.write(f"separate_fill_cycles={base['fill_cycles']}\n")
    output.write(f"masked_fill_cycles={masked['fill_cycles']}\n")
    output.write(
        "fill_cycles_delta="
        f"{int(masked['fill_cycles']) - int(base['fill_cycles'])}\n"
    )
    output.write(f"predicate_lines_avoided={lines_avoided}\n")
    output.write(f"bytes_avoided={bytes_avoided}\n")
    output.write("bytes_avoided_per_rmw=65536\n")
    output.write("compare_bits=32\nmode_state_bits=1\n")
    output.write("additional_buffer_bytes=0\n")
PY

{
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'source_diff_sha256='; git -C "$root" diff --binary | sha256sum | awk '{print $1}'
    printf 'gem5_sha256='; sha256sum "$gem5" | awk '{print $1}'
    printf 'guest_sha256='; sha256sum "$guest" | awk '{print $1}'
    printf 'config_sha256='; sha256sum "$config" | awk '{print $1}'
    printf 'ramulator_sha256='; sha256sum "$ramulator" | awk '{print $1}'
    printf 'checkpoint=%s\n' "$checkpoint"
    printf 'shared_checkpoint_arms=separate_predicate,masked_index\n'
    printf 'only_treatment=word5_mode_and_predicate_fetch\n'
    printf 'ordinary_semantics_changed=0\n'
} > "$out/manifest.txt"

cat "$out/results.tsv"
cat "$out/summary.txt"
echo "SOA_JIT_MASKED_INDEX_MICRO_PASS"
