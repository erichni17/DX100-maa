#!/usr/bin/env bash
# Exact scalar-SoA smoke, then one full NAS IS Class B hybrid-only arm.
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "usage: $0 {smoke|full} GEM5_BIN OUTDIR" >&2
    exit 2
fi

action=$1
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$2")
out=$(realpath -m "$3")
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
frozen_sweep="$root/experiments/analysis/physical_tile_sweep_baseline_20260822.json"
key_header=${IS_KEY_HEADER:-/data1/nier/worktrees/DX100-full-tile-sweep-20260720/benchmarks/NAS/is/key_array_4C.h}

[[ $action == smoke || $action == full ]] || {
    echo "action must be smoke or full" >&2
    exit 2
}
[[ -x $gem5 ]] || { echo "missing gem5 binary: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence run from a dirty worktree" >&2
    git -C "$root" status --short >&2
    exit 1
}

mkdir -p "$out/bin" "$out/checkpoint" "$out/run"
export LD_LIBRARY_PATH="$root/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"

if [[ $action == smoke ]]; then
    guest="$out/bin/test_hybrid_rmw_scalar_soa"
    source_file="$root/benchmarks/API/test_hybrid_rmw_scalar_soa.cpp"
    guest_options=()
    memory=2GB
    "${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
        -I"$root/util/m5/src" -std=c++11 -O2 -Wall -Wextra -Werror \
        -Wno-ignored-qualifiers -DGEM5 -DTILE_SIZE=16384 -DNUM_CORES=4 \
        -DMAA_MEM_SIZE=0x80000000 "$root/util/m5/src/abi/x86/m5op.S" \
        "$source_file" -o "$guest"
else
    [[ -f $key_header ]] || {
        echo "missing frozen Class B input header: $key_header" >&2
        exit 2
    }
    key_header=$(realpath "$key_header")
    guest="$out/bin/is_maa_16K_scalar_soa_roi_verify"
    source_file="$root/benchmarks/NAS/is/is.cpp"
    guest_options=(MAA scalar_soa_jit)
    memory=16GB
    "${CXX:-g++}" -I"$root/benchmarks/API" -I"$root/include" \
        -I"$root/util/m5/src" -I"$(dirname "$key_header")" \
        -std=c++11 -O3 -Wall -Wextra -Wno-ignored-qualifiers \
        -Wno-unused-parameter -fopenmp -DGEM5 -DMAA \
        -DIS_SCALAR_SOA_JIT -DNUM_CORES=4 -DTILE_SIZE=16384 \
        -DDO_VERIFY "-DCLASS='B'" -DVERIFY_BEFORE_GEM5_EXIT \
        -DUSE_DATA_FROM_FILE "$root/util/m5/src/abi/x86/m5op.S" \
        "$source_file" -o "$guest"
fi

source_commit=$(git -C "$root" rev-parse HEAD)
{
    printf 'action=%s\nsource_commit=%s\n' "$action" "$source_commit"
    printf 'source_path=%s\nsource_sha256=' "$source_file"
    sha256sum "$source_file" | awk '{print $1}'
    printf 'gem5_path=%s\ngem5_sha256=' "$gem5"
    sha256sum "$gem5" | awk '{print $1}'
    printf 'guest_path=%s\nguest_sha256=' "$guest"
    sha256sum "$guest" | awk '{print $1}'
    if [[ $action == full ]]; then
        printf 'input_path=%s\ninput_sha256=' "$key_header"
        sha256sum "$key_header" | awk '{print $1}'
        printf 'frozen_native_source=%s\nfrozen_native_sha256=' \
            "$frozen_sweep"
        sha256sum "$frozen_sweep" | awk '{print $1}'
    else
        printf 'input=compiled_exact_micro_vectors\n'
    fi
    printf 'logical_elements=16384\nphysical_tile_elements=4096\n'
    printf 'row_table_slices=32\n'
    printf 'native_runs=0\nwall_timeout=none\n'
} > "$out/manifest.txt"

printf '%q ' "$gem5" --listener-mode=off --outdir="$out/checkpoint" \
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size "$memory" \
    --max-checkpoints=1 --cmd "$guest" > "$out/checkpoint.command.txt"
if ((${#guest_options[@]})); then
    printf '%q ' --options "${guest_options[*]}" \
        >> "$out/checkpoint.command.txt"
fi
printf '\n' >> "$out/checkpoint.command.txt"

checkpoint_command=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint"
    "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size "$memory"
    --max-checkpoints=1 --cmd "$guest"
)
if ((${#guest_options[@]})); then
    checkpoint_command+=(--options "${guest_options[*]}")
fi
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${checkpoint_command[@]}" \
    > "$out/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
          "$out/checkpoint.log" || true) -eq 1 ]]

common=(
    --listener-mode=off "$config" --cpu-type X86O3CPU -r 1 -n 4
    --mem-size "$memory" --checkpoint-dir "$out/checkpoint"
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
    --maa_num_initial_row_table_slices=32
    --maa_virtual_index_buffer_lines=16
    --maa_soa_jit_predicate_active_credits=16
    --maa_soa_jit_active_contexts=32
    --maa_soa_jit_active_value_owners=32
    --maa_soa_jit_value_prefetch_credits=0
    --cmd "$guest"
)
if ((${#guest_options[@]})); then
    common+=(--options "${guest_options[*]}")
fi

printf '%q ' "$gem5" --outdir="$out/run" "${common[@]}" \
    > "$out/run/command.txt"
printf '\n' >> "$out/run/command.txt"
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 "$gem5" --outdir="$out/run" \
    "${common[@]}" > "$out/run/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" > "$out/run/restore.exit"
[[ $restore_rc -eq 0 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
          "$out/run/restore.log" || true) -eq 1 ]]
[[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
          "$out/run/restore.log" || true) -eq 0 ]]
[[ -s $out/run/stats.txt ]]

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

stats="$out/run/stats.txt"
instructions=$(stat_sum "$stats" IND_SoaJitInstructions)
selected=$(stat_sum "$stats" IND_SoaJitSelected)
rejected=$(stat_sum "$stats" IND_SoaJitPredicateRejected)
predicate_issues=$(stat_sum "$stats" IND_SoaJitPredicateLineReads)
predicate_responses=$(stat_sum "$stats" IND_SoaJitPredicateLineResponses)
index_lines=$(stat_sum "$stats" IND_VirtIndexLineReads)
index_words=$(stat_sum "$stats" IND_VirtIndexWords)
value_issues=$(stat_sum "$stats" IND_SoaJitValueReadIssues)
value_responses=$(stat_sum "$stats" IND_SoaJitValueReadResponses)
value_fills=$(stat_sum "$stats" IND_SoaJitValueFills)
a_read_issues=$(stat_sum "$stats" IND_SoaJitAReadIssues)
a_read_responses=$(stat_sum "$stats" IND_SoaJitAReadResponses)
a_write_issues=$(stat_sum "$stats" IND_SoaJitAWriteIssues)
a_write_responses=$(stat_sum "$stats" IND_SoaJitAWriteResponses)
aliases=$(stat_sum "$stats" IND_SoaJitAliasesApplied)
terminals=$(stat_sum "$stats" IND_SoaJitTerminalCompletions)

[[ $instructions -ge 2 && $terminals -eq $instructions ]]
[[ $selected -gt 0 && $((selected + rejected)) -gt 0 ]]
[[ $predicate_issues -eq $predicate_responses ]]
[[ $index_lines -gt 0 && $index_words -eq $((selected + rejected)) ]]
[[ $value_issues -eq 0 && $value_responses -eq 0 && $value_fills -eq 0 ]]
[[ $a_read_issues -gt 0 && $a_read_issues -eq $a_read_responses ]]
[[ $a_read_issues -eq $a_write_issues && \
   $a_write_issues -eq $a_write_responses ]]
[[ $aliases -eq $selected ]]

if [[ $action == smoke ]]; then
    expected='HYBRID_RMW_SCALAR_SOA_RESULT generations=2 logical=16384 errors=0'
    [[ $(grep -Fxc "$expected" "$out/run/restore.log" || true) -eq 1 ]]
    [[ $instructions -eq 2 && $terminals -eq 2 ]]
else
    [[ $(grep -Fxc \
        'IS_SCALAR_SOA_JIT_SELECTION compiled=1 treatment=scalar_soa_jit legacy_default=0' \
        "$out/run/restore.log" || true) -eq 1 ]]
    terminal=$(grep '^IS_SCALAR_SOA_JIT_TERMINAL ' \
        "$out/run/restore.log")
    [[ $terminal == *'generations=2048 full_windows=2048 tail_words=0 index_words=33554432'* ]]
    [[ $terminal == *'predicate_words=0 value_words=0 host_spd_reads=0 staging_bytes=0 result=PASS'* ]]
    [[ $(grep -Fxc 'ROI End!!!' "$out/run/restore.log" || true) -eq 1 ]]
    [[ $(grep -Fxc 'successfull: passed verification 6' \
          "$out/run/restore.log" || true) -eq 1 ]]
    [[ $instructions -eq 2048 && $terminals -eq 2048 ]]
    [[ $selected -eq 33554432 && $rejected -eq 0 ]]
    [[ $predicate_issues -eq 0 && $predicate_responses -eq 0 ]]
    [[ $index_words -eq 33554432 ]]
fi

for resolved in num_tile_elements=16384 physical_tile_elements=4096 \
    num_offset_table_entries=16384 num_offset_table_epoch_entries=16384 \
    num_initial_row_table_slices=32 \
    virtual_index_buffer_lines=16 soa_jit_predicate_active_credits=16 \
    soa_jit_active_contexts=32 soa_jit_active_value_owners=32 \
    soa_jit_value_prefetch_credits=0; do
    grep -Fqx "$resolved" "$out/run/config.ini"
done

sim_ticks=$(awk '$1 == "simTicks" { print $2; exit }' "$stats")
[[ $sim_ticks =~ ^[1-9][0-9]*$ ]]
{
    printf 'action\tsimTicks\tinstructions\tterminals\tselected\trejected\tindex_lines\tindex_words\tpredicate_issues\tpredicate_responses\tvalue_issues\tvalue_responses\ta_read_issues\ta_read_responses\ta_write_issues\ta_write_responses\taliases\n'
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$action" "$sim_ticks" "$instructions" "$terminals" "$selected" \
        "$rejected" "$index_lines" "$index_words" "$predicate_issues" \
        "$predicate_responses" "$value_issues" "$value_responses" \
        "$a_read_issues" "$a_read_responses" "$a_write_issues" \
        "$a_write_responses" "$aliases"
} > "$out/result.tsv"
printf 'PASS\n' > "$out/terminal.status"
cat "$out/result.tsv"
echo "PASS NAS IS scalar SoA/JIT $action out=$out"
