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
guest="$out/bin/test_hybrid_rmw_soa_T16384"
checkpoint="$out/checkpoint"
expected_hash=2761840269561229581
cxx=${CXX:-g++}

[[ -x $gem5 ]] || { echo "missing gem5 binary: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
mkdir -p "$out/bin" "$checkpoint" "$out/runs"

"$cxx" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O2 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers -DGEM5 -DTILE_SIZE=16384 \
    -DNUM_CORES=4 -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/src/abi/x86/m5op.S" \
    "$root/benchmarks/API/test_hybrid_rmw_soa.cpp" -o "$guest"

checkpoint_cmd=(
    timeout 300 "$gem5" --listener-mode=off --outdir="$checkpoint"
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB
    --max-checkpoints=1 --cmd "$guest" --options soa
)
printf '%q ' "${checkpoint_cmd[@]}" > "$checkpoint/command.txt"
printf '\n' >> "$checkpoint/command.txt"
"${checkpoint_cmd[@]}" > "$checkpoint/checkpoint.log" 2>&1
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
          "$checkpoint/checkpoint.log" || true) -eq 1 ]] || {
    echo "SoA/JIT control did not produce one exact checkpoint" >&2
    exit 1
}

printf 'active_credits\tsimTicks\toutput_hash\tpredicate_issues\t' \
    > "$out/matrix.tsv"
printf 'predicate_responses\tpredicate_hits\tpredicate_uses\tstalls\t' \
    >> "$out/matrix.tsv"
printf 'high_water\n' >> "$out/matrix.tsv"

stat_sum() {
    local stats=$1
    local suffix=$2
    awk -v suffix="$suffix" '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ ("_" suffix "$") { sum += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            printf "%.0f\n", sum
            exit
        }
    ' "$stats"
}

run_credit() {
    local credits=$1
    local run="$out/runs/active${credits}"
    mkdir -p "$run"
    local -a command=(
        timeout 1800 "$gem5" --listener-mode=off --outdir="$run"
        --debug-flags=MAAVirtualTrace --debug-file=soa_jit_trace.log
        "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
        --checkpoint-dir="$checkpoint"
        --sys-clock 3.2GHz --cpu-clock 3.2GHz
        --caches --l1d_size=32kB --l1d_assoc=8 --l1d_mshrs=16
        --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8
        --l1i_mshrs=16 --l1i_write_buffers=8
        --l2cache --l2_size=256kB --l2_assoc=4 --l2_mshrs=32
        --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
        --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
        --cacheline_size=64 --mem-type Ramulator2
        --ramulator-config "$ramulator" --mem-channels=1
        --maa --maa_num_maas=1 --maa_num_indirect_units_per_maa=1
        --maa_num_tile_elements=16384
        --maa_physical_tile_elements=4096
        --maa_num_offset_table_entries=16384
        --maa_num_offset_table_epoch_entries=16384
        --maa_num_initial_row_table_slices=16
        --maa_soa_jit_predicate_active_credits="$credits"
        --cmd "$guest"
    )
    printf '%q ' "${command[@]}" > "$run/command.txt"
    printf '\n' >> "$run/command.txt"
    "${command[@]}" > "$run/restore.log" 2>&1

    local result
    result=$(grep -E '^HYBRID_RMW_SOA_RESULT ' "$run/restore.log" || true)
    [[ $(grep -Ec '^HYBRID_RMW_SOA_RESULT ' "$run/restore.log" || true) \
           -eq 1 &&
       $result =~ (^|[[:space:]])mode=soa($|[[:space:]]) &&
       $result =~ (^|[[:space:]])logical=16384($|[[:space:]]) &&
       $result =~ (^|[[:space:]])operations=2($|[[:space:]]) &&
       $result =~ (^|[[:space:]])errors=0($|[[:space:]]) &&
       $result =~ (^|[[:space:]])output_hash=$expected_hash($|[[:space:]]) ]] || {
        echo "active${credits} failed the exact API/hash contract" >&2
        exit 1
    }
    [[ $(grep -Fxc 'ROI Ended' "$run/restore.log" || true) -eq 1 ]] || {
        echo "active${credits} has an invalid ROI terminator" >&2
        exit 1
    }
    [[ $(grep -Ec \
          '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
          "$run/restore.log" || true) -eq 1 ]] || {
        echo "active${credits} has an invalid m5_exit terminator" >&2
        exit 1
    }
    [[ $(grep -Eic \
          'panic|fatal|assert|abort|segmentation fault|error:' \
          "$run/restore.log" || true) -eq 0 ]] || {
        echo "active${credits} log contains a fatal/error marker" >&2
        exit 1
    }
    for resolved in \
        num_tile_elements=16384 \
        physical_tile_elements=4096 \
        num_offset_table_entries=16384 \
        num_offset_table_epoch_entries=16384 \
        soa_jit_predicate_active_credits="$credits"; do
        grep -Fqx "$resolved" "$run/config.ini" || {
            echo "active${credits} missing resolved config: $resolved" >&2
            exit 1
        }
    done

    local instructions terminal issues responses hits uses stalls active high_water
    local state_bytes sim_ticks output_hash generations
    instructions=$(stat_sum "$run/stats.txt" IND_SoaJitInstructions)
    terminal=$(stat_sum "$run/stats.txt" IND_SoaJitTerminalCompletions)
    issues=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateLineReads)
    responses=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateLineResponses)
    hits=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateLineHits)
    uses=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateUses)
    stalls=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateFeederStalls)
    active=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateActiveCredits)
    high_water=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateFeederHighWater)
    state_bytes=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateFeederStateBytes)
    [[ $instructions -eq 2 && $terminal -eq 2 &&
       $issues -eq 2048 && $responses -eq 2048 &&
       $hits -eq 32768 && $uses -eq 32768 &&
       $active -eq $((2 * credits)) &&
       $high_water -eq $((2 * credits)) && $state_bytes -eq 2880 ]] || {
        echo "active${credits} failed exact feeder accounting" >&2
        exit 1
    }
    generations=$(awk '
        /event=soa_jit_complete/ && /terminal=1/ {
            for (i = 1; i <= NF; ++i)
                if ($i ~ /^generation=/) seen[$i] = 1
        }
        END { for (generation in seen) count++; print count + 0 }
    ' "$run/soa_jit_trace.log")
    [[ $generations -eq 2 ]] || {
        echo "active${credits} did not close two exact generations" >&2
        exit 1
    }

    output_hash=$(sed -n \
        's/.* output_hash=\([0-9][0-9]*\).*/\1/p' <<< "$result")
    sim_ticks=$(awk '$1 == "simTicks" { print $2; exit }' "$run/stats.txt")
    [[ $output_hash == "$expected_hash" &&
       $sim_ticks =~ ^[1-9][0-9]*$ ]] || {
        echo "active${credits} has invalid output hash or simTicks" >&2
        exit 1
    }
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$credits" "$sim_ticks" "$output_hash" "$issues" "$responses" \
        "$hits" "$uses" "$stalls" "$high_water" >> "$out/matrix.tsv"
}

for credits in 1 4 8 16; do
    run_credit "$credits"
done

{
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'source_status_sha256='; git -C "$root" status --short | sha256sum | awk '{print $1}'
    printf 'benchmark_source_sha256='; sha256sum "$root/benchmarks/API/test_hybrid_rmw_soa.cpp" | awk '{print $1}'
    printf 'runner_source_sha256='; sha256sum "$root/experiments/scripts/run_soa_jit_predicate_feeder_matrix.sh" | awk '{print $1}'
    printf 'gem5_sha256='; sha256sum "$gem5" | awk '{print $1}'
    printf 'guest_sha256='; sha256sum "$guest" | awk '{print $1}'
    printf 'se_config_sha256='; sha256sum "$config" | awk '{print $1}'
    printf 'ramulator_config_sha256='; sha256sum "$ramulator" | awk '{print $1}'
    printf 'checkpoint=%s\n' "$checkpoint"
    printf 'expected_output_hash=%s\n' "$expected_hash"
    for credits in 1 4 8 16; do
        printf 'active%s_config_ini_sha256=' "$credits"
        sha256sum "$out/runs/active${credits}/config.ini" | awk '{print $1}'
        printf 'active%s_command_sha256=' "$credits"
        sha256sum "$out/runs/active${credits}/command.txt" | awk '{print $1}'
    done
} > "$out/manifest.txt"

cat "$out/matrix.tsv"
echo "SOA_JIT_PREDICATE_FEEDER_MATRIX_PASS"
