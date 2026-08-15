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
cxx=${CXX:-g++}

[[ -x $gem5 ]] || { echo "missing gem5 binary: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
mkdir -p "$out/bin" "$out/checkpoints" "$out/runs"

build_guest() {
    local tile=$1
    local binary="$out/bin/test_hybrid_rmw_soa_T${tile}"
    "$cxx" -I"$root/benchmarks/API" -I"$root/include" \
        -I"$root/util/m5/src" -std=c++11 -O2 -Wall -Wextra -Werror \
        -Wno-ignored-qualifiers -DGEM5 -DTILE_SIZE="$tile" \
        -DNUM_CORES=4 -DMAA_MEM_SIZE=0x80000000 \
        "$root/util/m5/src/abi/x86/m5op.S" \
        "$root/benchmarks/API/test_hybrid_rmw_soa.cpp" -o "$binary"
    printf '%s\n' "$binary"
}

binary16=$(build_guest 16384)
binary4=$(build_guest 4096)

make_checkpoint() {
    local name=$1
    local binary=$2
    local mode=$3
    local checkpoint="$out/checkpoints/$name"
    mkdir -p "$checkpoint"
    timeout 300 "$gem5" --listener-mode=off --outdir="$checkpoint" \
        "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
        --max-checkpoints=1 --cmd "$binary" --options "$mode" \
        >"$checkpoint/checkpoint.log" 2>&1
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
              "$checkpoint/checkpoint.log" || true) -eq 1 ]] || {
        echo "$name did not produce one exact checkpoint" >&2
        exit 1
    }
}

make_checkpoint ordinary16 "$binary16" ordinary
make_checkpoint ordinary4 "$binary4" ordinary
make_checkpoint soa16 "$binary16" soa

printf 'arm\tmode\tlogical\tphysical\tsimTicks\toutput_hash\n' \
    > "$out/matrix.tsv"
declare -A hashes
declare -A ticks

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

run_arm() {
    local arm=$1
    local checkpoint_name=$2
    local logical=$3
    local physical=$4
    local mode=$5
    local run="$out/runs/$arm"
    local checkpoint="$out/checkpoints/$checkpoint_name"
    mkdir -p "$run"
    timeout 1800 "$gem5" --listener-mode=off --outdir="$run" \
        --debug-flags=MAAVirtualTrace --debug-file=soa_jit_trace.log \
        "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB \
        --checkpoint-dir="$checkpoint" \
        --sys-clock 3.2GHz --cpu-clock 3.2GHz \
        --caches --l1d_size=32kB --l1d_assoc=8 --l1d_mshrs=16 \
        --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8 \
        --l1i_mshrs=16 --l1i_write_buffers=8 \
        --l2cache --l2_size=256kB --l2_assoc=4 --l2_mshrs=32 \
        --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16 \
        --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4 \
        --cacheline_size=64 --mem-type Ramulator2 \
        --ramulator-config "$ramulator" --mem-channels=1 \
        --maa --maa_num_maas=1 --maa_num_indirect_units_per_maa=1 \
        --maa_num_tile_elements="$logical" \
        --maa_physical_tile_elements="$physical" \
        --maa_num_offset_table_entries="$logical" \
        --maa_num_offset_table_epoch_entries="$logical" \
        --maa_num_initial_row_table_slices=16 \
        --cmd "$out/bin/test_hybrid_rmw_soa_T${logical}" \
        >"$run/restore.log" 2>&1

    local result
    result=$(grep -E '^HYBRID_RMW_SOA_RESULT ' "$run/restore.log" || true)
    [[ $(grep -Ec '^HYBRID_RMW_SOA_RESULT ' "$run/restore.log" || true) -eq 1 &&
       $result =~ (^|[[:space:]])mode=$mode($|[[:space:]]) &&
       $result =~ (^|[[:space:]])logical=16384($|[[:space:]]) &&
       $result =~ (^|[[:space:]])operations=2($|[[:space:]]) &&
       $result =~ (^|[[:space:]])errors=0($|[[:space:]]) ]] || {
        echo "$arm failed its exact API result contract" >&2
        exit 1
    }
    [[ $(grep -Fxc 'ROI Ended' "$run/restore.log" || true) -eq 1 ]] || {
        echo "$arm has an invalid ROI terminator" >&2
        exit 1
    }
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
              "$run/restore.log" || true) -eq 1 ]] || {
        echo "$arm has an invalid m5_exit terminator" >&2
        exit 1
    }
    [[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
              "$run/restore.log" || true) -eq 0 ]] || {
        echo "$arm log contains a fatal/error marker" >&2
        exit 1
    }
    for resolved in \
        "num_tile_elements=$logical" \
        "physical_tile_elements=$physical" \
        "num_offset_table_entries=$logical" \
        "num_offset_table_epoch_entries=$logical"; do
        grep -Fqx "$resolved" "$run/config.ini" || {
            echo "$arm missing resolved geometry: $resolved" >&2
            exit 1
        }
    done

    local instructions selected rejected index_issues index_responses
    local predicate_issues predicate_responses
    local value_issues value_responses aliases
    local a_read_issues a_read_responses write_issues write_responses terminal
    local context_high_water context_stalls
    instructions=$(stat_sum "$run/stats.txt" IND_SoaJitInstructions)
    selected=$(stat_sum "$run/stats.txt" IND_SoaJitSelected)
    rejected=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateRejected)
    index_issues=$(stat_sum "$run/stats.txt" IND_SoaJitIndexLineReads)
    index_responses=$(stat_sum "$run/stats.txt" IND_SoaJitIndexLineResponses)
    predicate_issues=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateLineReads)
    predicate_responses=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateLineResponses)
    value_issues=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadIssues)
    value_responses=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadResponses)
    aliases=$(stat_sum "$run/stats.txt" IND_SoaJitAliasesApplied)
    a_read_issues=$(stat_sum "$run/stats.txt" IND_SoaJitAReadIssues)
    a_read_responses=$(stat_sum "$run/stats.txt" IND_SoaJitAReadResponses)
    write_issues=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteIssues)
    write_responses=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteResponses)
    context_high_water=$(stat_sum "$run/stats.txt" IND_SoaJitContextHighWater)
    context_stalls=$(stat_sum "$run/stats.txt" IND_SoaJitContextStalls)
    terminal=$(stat_sum "$run/stats.txt" IND_SoaJitTerminalCompletions)
    if [[ $mode == soa ]]; then
        [[ $instructions -eq 2 && $terminal -eq 2 &&
           $selected -gt 0 && $rejected -gt 0 &&
           $((selected + rejected)) -eq 32768 &&
           $index_issues -gt 0 &&
           $index_issues -eq $index_responses &&
           $predicate_issues -gt 0 &&
           $predicate_issues -eq $predicate_responses &&
           $value_issues -eq $selected &&
           $value_responses -eq $selected && $aliases -eq $selected &&
           $a_read_issues -gt 0 && $a_read_issues -eq $a_read_responses &&
           $a_read_issues -eq $write_issues &&
           $write_issues -eq $write_responses &&
           $context_high_water -eq 2 && $context_stalls -gt 0 ]] || {
            echo "$arm failed exact SoA/JIT issue/response drain" >&2
            exit 1
        }
        local generations
        generations=$(awk '
            /event=soa_jit_complete/ && /terminal=1/ {
                for (i = 1; i <= NF; ++i)
                    if ($i ~ /^generation=/) seen[$i] = 1
            }
            END { for (generation in seen) count++; print count + 0 }
        ' "$run/soa_jit_trace.log")
        [[ $generations -eq 2 ]] || {
            echo "$arm did not close two distinct generations" >&2
            exit 1
        }
    else
        [[ $instructions -eq 0 && $terminal -eq 0 ]] || {
            echo "$arm unexpectedly used the SoA/JIT path" >&2
            exit 1
        }
    fi

    local output_hash sim_ticks
    output_hash=$(sed -n 's/.* output_hash=\([0-9][0-9]*\).*/\1/p' <<<"$result")
    sim_ticks=$(awk '$1 == "simTicks" { print $2; exit }' "$run/stats.txt")
    [[ $output_hash =~ ^[0-9]+$ && $sim_ticks =~ ^[1-9][0-9]*$ ]] || {
        echo "$arm has invalid hash or simTicks" >&2
        exit 1
    }
    hashes[$arm]=$output_hash
    ticks[$arm]=$sim_ticks
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$arm" "$mode" "$logical" "$physical" "$sim_ticks" \
        "$output_hash" >> "$out/matrix.tsv"
}

run_arm ordinary_native16 ordinary16 16384 16384 ordinary
run_arm ordinary_native4 ordinary4 4096 4096 ordinary
run_arm soa_metadata16_physical16 soa16 16384 16384 soa
run_arm soa_metadata16_physical4 soa16 16384 4096 soa

reference=${hashes[ordinary_native16]}
for arm in ordinary_native4 soa_metadata16_physical16 \
           soa_metadata16_physical4; do
    [[ ${hashes[$arm]} == "$reference" ]] || {
        echo "four-arm output hash mismatch at $arm" >&2
        exit 1
    }
done

awk -v physical16="${ticks[soa_metadata16_physical16]}" \
    -v physical4="${ticks[soa_metadata16_physical4]}" '
    BEGIN {
        if (physical16 <= 0 || physical4 <= 0) exit 1
        printf "soa_physical_spd_geometry_ratio=%.9f\n", \
               physical4 / physical16
        print "soa_pair_scope=geometry_independence_hidden_dependency_check"
        print "ordinary_vs_soa_scope=api_staging_old_value_output_differ"
    }
' > "$out/attribution.txt"

{
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'gem5_sha256='; sha256sum "$gem5" | awk '{print $1}'
    printf 'guest16_sha256='; sha256sum "$binary16" | awk '{print $1}'
    printf 'guest4_sha256='; sha256sum "$binary4" | awk '{print $1}'
    printf 'soa_pair_checkpoint=%s\n' "$out/checkpoints/soa16"
    printf 'soa_pair_only_geometry_delta=physical_tile_elements\n'
} > "$out/manifest.txt"

cat "$out/matrix.tsv"
cat "$out/attribution.txt"
echo "HYBRID_RMW_SOA_MATRIX_PASS"
