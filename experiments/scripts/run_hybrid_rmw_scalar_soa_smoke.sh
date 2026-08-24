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
source="$root/benchmarks/API/test_hybrid_rmw_scalar_soa.cpp"

[[ -x $gem5 ]] || { echo "missing gem5 binary: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence run from a dirty source tree" >&2
    exit 1
}

mkdir -p "$out/bin" "$out/checkpoint" "$out/runs"
binary="$out/bin/test_hybrid_rmw_scalar_soa"
"${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O2 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers -DGEM5 -DTILE_SIZE=16384 -DNUM_CORES=4 \
    -DMAA_MEM_SIZE=0x80000000 "$root/util/m5/src/abi/x86/m5op.S" \
    "$source" -o "$binary"

"$gem5" --listener-mode=off --outdir="$out/checkpoint" \
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
    --max-checkpoints=1 --cmd "$binary" \
    >"$out/checkpoint.log" 2>&1
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
          "$out/checkpoint.log" || true) -eq 1 ]] || {
    echo "checkpoint did not close exactly" >&2
    exit 1
}

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

printf 'arm\tphysical\tsimTicks\n' >"$out/results.tsv"
for physical in 16384 4096; do
    arm="scalar_soa_physical${physical}"
    run="$out/runs/$arm"
    mkdir -p "$run"
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
        "$gem5" --listener-mode=off --outdir="$run" \
        --debug-flags=MAAVirtualTrace --debug-file=scalar_soa_trace.log \
        "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB \
        --checkpoint-dir="$out/checkpoint" \
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
        --maa_num_tile_elements=16384 \
        --maa_physical_tile_elements="$physical" \
        --maa_num_offset_table_entries=16384 \
        --maa_num_offset_table_epoch_entries=16384 \
        --maa_num_initial_row_table_slices=16 --cmd "$binary" \
        >"$run/restore.log" 2>&1

    expected='HYBRID_RMW_SCALAR_SOA_RESULT generations=2 logical=16384 errors=0'
    [[ $(grep -Fxc "$expected" "$run/restore.log" || true) -eq 1 ]] || {
        echo "$arm failed exact scalar result" >&2
        exit 1
    }
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
              "$run/restore.log" || true) -eq 1 ]] || {
        echo "$arm lacks one clean m5_exit" >&2
        exit 1
    }
    [[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
              "$run/restore.log" || true) -eq 0 ]] || {
        echo "$arm contains a fatal/error marker" >&2
        exit 1
    }

    instructions=$(stat_sum "$run/stats.txt" IND_SoaJitInstructions)
    selected=$(stat_sum "$run/stats.txt" IND_SoaJitSelected)
    rejected=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateRejected)
    predicate_issues=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateLineReads)
    predicate_responses=$(stat_sum "$run/stats.txt" IND_SoaJitPredicateLineResponses)
    value_issues=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadIssues)
    value_responses=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadResponses)
    a_reads=$(stat_sum "$run/stats.txt" IND_SoaJitAReadIssues)
    a_read_responses=$(stat_sum "$run/stats.txt" IND_SoaJitAReadResponses)
    writes=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteIssues)
    write_responses=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteResponses)
    terminal=$(stat_sum "$run/stats.txt" IND_SoaJitTerminalCompletions)
    [[ $instructions -eq 2 && $terminal -eq 2 && \
       $((selected + rejected)) -eq 32768 && $selected -gt 0 && \
       $rejected -gt 0 && $predicate_issues -gt 0 && \
       $predicate_issues -eq $predicate_responses && \
       $value_issues -eq 0 && $value_responses -eq 0 && \
       $a_reads -gt 0 && $a_reads -eq $a_read_responses && \
       $a_reads -eq $writes && $writes -eq $write_responses ]] || {
        echo "$arm failed the scalar no-value-read traffic ledger" >&2
        exit 1
    }
    generations=$(awk '
        /event=soa_jit_complete/ && /terminal=1/ {
            for (i = 1; i <= NF; ++i)
                if ($i ~ /^generation=/) seen[$i] = 1
        }
        END { for (generation in seen) count++; print count + 0 }
    ' "$run/scalar_soa_trace.log")
    captures=$(grep -c 'event=soa_jit_scalar_capture ' \
        "$run/scalar_soa_trace.log" || true)
    [[ $generations -eq 2 && $captures -eq $selected ]] || {
        echo "$arm failed scalar capture/generation closure" >&2
        exit 1
    }

    sim_ticks=$(awk '$1 == "simTicks" { print $2; exit }' "$run/stats.txt")
    [[ $sim_ticks =~ ^[1-9][0-9]*$ ]] || {
        echo "$arm has invalid simTicks" >&2
        exit 1
    }
    printf '%s\t%s\t%s\n' "$arm" "$physical" "$sim_ticks" \
        >>"$out/results.tsv"
done

{
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'gem5_sha256='; sha256sum "$gem5" | awk '{print $1}'
    printf 'guest_sha256='; sha256sum "$binary" | awk '{print $1}'
    printf 'native_baselines_rerun=0\nwall_timeout=none\n'
} >"$out/manifest.txt"

cat "$out/results.tsv"
echo "HYBRID_RMW_SCALAR_SOA_SMOKE_PASS"
