#!/usr/bin/env bash
# Candidate-only HashJoin SoA/JIT correctness gate. It never runs a native
# reference or a legacy guest and intentionally has no wall-clock timeout.
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$script_dir/../.." && pwd)
gem5=${GEM5_BINARY:?set GEM5_BINARY to a SoA/JIT-capable gem5.opt}
config=$root/configs/deprecated/example/se.py
ramulator=$root/ext/ramulator2/ramulator2/example_gem5_config.yaml
guest=$root/benchmarks/hashjoin/src/bin/x86/hj_maa_16K_hybrid
out=${HASHJOIN_HYBRID_OUT:-/data1/nier/dx100-runs/hashjoin-hybrid-small-$(date +%Y%m%d-%H%M%S)}

# Frozen input contract. PK/FK construction makes the exact cardinality S_SIZE.
readonly R_SIZE=65536
readonly S_SIZE=65536
readonly R_SEED=12345
readonly S_SEED=54321
readonly EXPECTED_RESULT=65536
readonly OMP_THREADS=4

[[ ! -e "$out" ]] || {
    echo "output already exists: $out" >&2
    exit 2
}
[[ -x "$gem5" ]] || {
    echo "missing gem5 binary: $gem5" >&2
    exit 2
}

if [[ ${HASHJOIN_HYBRID_SKIP_BUILD:-0} != 1 ]]; then
    (cd "$root/benchmarks/hashjoin" &&
        MAA_MEM_SIZE=0x80000000 bash ./compile_x86.sh GEM5)
fi
[[ -x "$guest" ]] || {
    echo "missing candidate guest: $guest" >&2
    exit 2
}

mkdir -p "$out"
export LD_LIBRARY_PATH="$root/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"

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

field() {
    local line=$1
    local name=$2
    awk -v name="$name" '{
        for (i = 1; i <= NF; ++i) {
            split($i, pair, "=")
            if (pair[1] == name) {
                print pair[2]
                exit
            }
        }
    }' <<<"$line"
}

printf 'kernel\tresult\trouted\tsoa_instructions\tsoa_terminals\tsimTicks\n' \
    >"$out/results.tsv"

for kernel in PRO PRH; do
    case_root=$out/$kernel
    checkpoint=$case_root/checkpoint
    run=$case_root/run
    mkdir -p "$checkpoint"
    mkdir -p "$run"
    options="-a $kernel -n $OMP_THREADS -r $R_SIZE -s $S_SIZE -x $R_SEED -y $S_SEED"

    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=$OMP_THREADS \
        "$gem5" --listener-mode=off --outdir="$checkpoint" \
        "$config" --cpu-type=AtomicSimpleCPU -n 4 --mem-size=2GB \
        --max-checkpoints=1 --cmd="$guest" --options="$options" \
        >"$case_root/checkpoint.log" 2>&1
    checkpoint_rc=$?
    set -e
    [[ $checkpoint_rc -eq 0 ]] || {
        echo "$kernel checkpoint gem5 exited with rc=$checkpoint_rc" >&2
        exit 1
    }
    checkpoint_dir=$(find "$checkpoint" -maxdepth 1 -type d \
        -name 'cpt.*' -print -quit)
    [[ -n "$checkpoint_dir" ]] || {
        echo "$kernel checkpoint is missing" >&2
        exit 1
    }

    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=$OMP_THREADS \
        "$gem5" --listener-mode=off --outdir="$run" \
        --debug-flags=MAAVirtualTrace --debug-file=soa_jit_trace.log \
        "$config" --cpu-type=X86O3CPU -r 1 -n 4 --mem-size=2GB \
        --checkpoint-dir="$checkpoint" \
        --sys-clock=3.2GHz --cpu-clock=3.2GHz \
        --caches --l1d_size=32kB --l1d_assoc=8 \
        --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 \
        --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8 \
        --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16 \
        --l1i_write_buffers=8 --l2cache --l2_size=256kB \
        --l2_assoc=4 --l2-hwp-type=StridePrefetcher --l2_mshrs=32 \
        --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16 \
        --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4 \
        --cacheline_size=64 --mem-type=Ramulator2 \
        --ramulator-config="$ramulator" --mem-channels=2 \
        --maa --maa_num_maas=1 --maa_num_indirect_units_per_maa=4 \
        --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096 \
        --maa_num_offset_table_entries=16384 \
        --maa_num_offset_table_epoch_entries=16384 \
        --maa_num_initial_row_table_slices=32 --maa_ncbus_width=32 \
        --maa_l2_uncacheable --maa_l3_uncacheable \
        --cmd="$guest" --options="$options" >"$run/run.log" 2>&1
    rc=$?
    set -e
    [[ $rc -eq 0 ]] || {
        echo "$kernel gem5 exited with rc=$rc" >&2
        exit 1
    }

    expected="HASHJOIN_HYBRID_RESULT result=$EXPECTED_RESULT"
    [[ $(grep -Fxc "$expected" "$run/run.log" || true) -eq 1 ]]
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
              "$run/run.log" || true) -eq 1 ]]
    [[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
              "$run/run.log" || true) -eq 0 ]]

    region=$(grep -F 'HASHJOIN_HYBRID_REGION_LAYOUT ' "$run/run.log")
    [[ $(field "$region" backing_regions) -eq 1 ]]
    [[ $(field "$region" threads) -eq 4 ]]
    [[ $(field "$region" max_region_id) -eq 31 ]]
    [[ $(field "$region" limit) -eq 31 ]]

    marker=$(grep -F 'HASHJOIN_HYBRID_SOA_JIT ' "$run/run.log")
    [[ $(field "$marker" enabled) -eq 1 ]]
    first_eligible=$(field "$marker" first_eligible)
    first_routed=$(field "$marker" first_routed)
    second_eligible=$(field "$marker" second_eligible)
    second_routed=$(field "$marker" second_routed)
    eligible=$(field "$marker" eligible)
    routed=$(field "$marker" routed)
    first_scatter_4k_actions=$(field "$marker" first_scatter_4k_actions)
    second_scatter_4k_actions=$(field "$marker" second_scatter_4k_actions)
    [[ $first_eligible -gt 0 && $first_routed -eq $first_eligible ]]
    [[ $second_routed -eq $second_eligible ]]
    [[ $routed -gt 0 && $routed -eq $eligible ]]
    [[ $first_scatter_4k_actions -eq 32 ]]
    [[ $second_scatter_4k_actions -gt 0 ]]

    stats=$run/stats.txt
    [[ -s "$stats" ]]
    instructions=$(stat_sum "$stats" IND_SoaJitInstructions)
    terminals=$(stat_sum "$stats" IND_SoaJitTerminalCompletions)
    selected=$(stat_sum "$stats" IND_SoaJitSelected)
    rejected=$(stat_sum "$stats" IND_SoaJitPredicateRejected)
    predicate_issues=$(stat_sum "$stats" IND_SoaJitPredicateLineReads)
    predicate_responses=$(stat_sum "$stats" IND_SoaJitPredicateLineResponses)
    value_issues=$(stat_sum "$stats" IND_SoaJitValueReadIssues)
    value_responses=$(stat_sum "$stats" IND_SoaJitValueReadResponses)
    aliases=$(stat_sum "$stats" IND_SoaJitAliasesApplied)
    a_reads=$(stat_sum "$stats" IND_SoaJitAReadIssues)
    a_read_responses=$(stat_sum "$stats" IND_SoaJitAReadResponses)
    writes=$(stat_sum "$stats" IND_SoaJitAWriteIssues)
    write_responses=$(stat_sum "$stats" IND_SoaJitAWriteResponses)
    [[ $instructions -eq $routed && $terminals -eq $instructions ]]
    [[ $selected -eq $((routed * 16384)) && $rejected -eq 0 ]]
    [[ $predicate_issues -eq 0 && $predicate_responses -eq 0 ]]
    [[ $value_issues -eq 0 && $value_responses -eq 0 ]]
    [[ $aliases -eq $selected ]]
    [[ $a_reads -gt 0 && $a_reads -eq $a_read_responses ]]
    [[ $a_reads -eq $writes && $writes -eq $write_responses ]]

    trace=$run/soa_jit_trace.log
    [[ $(grep -Ec 'event=soa_jit_complete .*terminal=1' "$trace" || true) \
        -eq $instructions ]]
    sim_ticks=$(awk '$1 == "simTicks" { value=$2 } END { print value }' "$stats")
    [[ $sim_ticks =~ ^[1-9][0-9]*$ ]]
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$kernel" "$EXPECTED_RESULT" \
        "$routed" "$instructions" "$terminals" "$sim_ticks" \
        >>"$out/results.tsv"
done

{
    printf 'schema=dx100.hashjoin_hybrid_small.v1\n'
    printf 'candidate_only=1\nnative_rerun=0\nwall_timeout=none\n'
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'source_status=%s\n' "$(git -C "$root" status --short | wc -l)"
    printf 'guest_sha256='; sha256sum "$guest" | awk '{print $1}'
    printf 'gem5_sha256='; sha256sum "$gem5" | awk '{print $1}'
    printf 'input=r_size:%d,s_size:%d,r_seed:%d,s_seed:%d,non_unique:0,full_range:0\n' \
        "$R_SIZE" "$S_SIZE" "$R_SEED" "$S_SEED"
    printf 'expected_cardinality=%d\n' "$EXPECTED_RESULT"
    printf 'checkpoint_paths=PRO/checkpoint,PRH/checkpoint\n'
    printf 'geometry=memory_channels:2,row_table_slices:32,indirect_units:4,logical_elements:16384,physical_elements:4096\n'
} >"$out/manifest.txt"

cat "$out/results.tsv"
echo "HASHJOIN_HYBRID_SMALL_PASS out=$out"
