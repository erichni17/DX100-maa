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
source_file="$root/benchmarks/NAS/cg/cg.cpp"
cxx=${CXX:-g++}

[[ -x $gem5 ]] || { echo "missing gem5 binary: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence run from a dirty worktree" >&2
    git -C "$root" status --short >&2
    exit 1
}

mkdir -p "$out/input" "$out/bin" "$out/checkpoints" "$out/runs"
printf '%s\n' 'token_stream_ld legacy_4k' \
    > "$out/input/legacy.selector"
printf '%s\n' 'token_stream_ld residual_soa_jit' \
    > "$out/input/residual.selector"
chmod 0444 "$out/input"/*.selector
sha256sum "$out/input"/*.selector > "$out/selector_sha256.before"

binary="$out/bin/cg_logical16_rmw_smoke"
"$cxx" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O3 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers -Wno-unused-parameter -fopenmp \
    -DGEM5 -DMAA -DMAA_VIRTUAL_GATHER \
    -DMAA_GENERAL_VIRTUAL_CONSUMER -DMAA_CONSUMER_TILE_SIZE=4096 \
    -DCG_LOGICAL16_RMW -DCG_FP_ENABLE -DCG_NA=1024 \
    -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$binary"

source_commit=$(git -C "$root" rev-parse HEAD)
sha256sum "$binary" "$gem5" "$source_file" "$config" "$ramulator" "$0" \
    > "$out/artifact_sha256.txt"
{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'scope=CG_NA1024_residual_SpMV_exact_correctness_smoke\n'
    printf 'logical_elements=16384\nphysical_tile_elements=4096\n'
    printf 'soa_jit_predicate_active_credits=16\n'
    printf 'soa_jit_active_value_owners=32\n'
    printf 'producer=cpu_after_spd_completion\n'
    printf 'performance_promotable=0\nspeedup_claim=0\n'
    printf 'comparison=selector_specific_checkpoints_same_binary\n'
} > "$out/manifest.txt"

make_checkpoint() {
    local label=$1 selector="$out/input/$2.selector"
    local checkpoint="$out/checkpoints/$label"
    mkdir -p "$checkpoint"
    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
        "$gem5" --listener-mode=off --outdir="$checkpoint" \
        "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
        --max-checkpoints=1 --cmd "$binary" \
        --options "MAA_DEFERRED $selector" \
        > "$out/checkpoints/$label.log" 2>&1
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$out/checkpoints/$label.exit"
    [[ $rc -eq 0 ]] || {
        echo "$label checkpoint failed with rc=$rc" >&2
        return 1
    }
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
              "$out/checkpoints/$label.log" || true) -eq 1 ]] || {
        echo "$label checkpoint lacks its exact terminal" >&2
        return 1
    }
    (
        cd "$checkpoint"
        find . -type f -print0 | sort -z | xargs -0 sha256sum
    ) > "$out/checkpoints/$label.files.sha256"
}

make_checkpoint legacy legacy
make_checkpoint residual residual

common=(
    --listener-mode=off "$config" --cpu-type X86O3CPU -r 1 -n 4
    --mem-size 2GB --sys-clock 3.2GHz --cpu-clock 3.2GHz
    --caches --l1d_size=32kB --l1d_assoc=8 --l1d_mshrs=16
    --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8
    --l1i_mshrs=16 --l1i_write_buffers=8
    --l2cache --l2_size=256kB --l2_assoc=4 --l2_mshrs=32
    --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
    --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
    --cacheline_size=64 --mem-type Ramulator2 --ramulator-config "$ramulator"
    --mem-channels=1 --maa --maa_num_maas=1
    --maa_num_indirect_units_per_maa=4
    --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096
    --maa_num_offset_table_entries=16384
    --maa_num_offset_table_epoch_entries=16384
    --maa_num_initial_row_table_slices=16
    --maa_soa_jit_predicate_active_credits=16
    --maa_soa_jit_active_value_owners=32
    --cmd "$binary"
)

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

declare -A fingerprints
run_arm() {
    local label=$1 treatment=$2 selector="$out/input/$1.selector"
    local run="$out/runs/$label"
    mkdir -p "$run"
    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
        "$gem5" --outdir="$run" "${common[@]}" \
        --checkpoint-dir="$out/checkpoints/$label" \
        --options "MAA_DEFERRED $selector" \
        > "$run/restore.log" 2>&1
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$run/restore.exit"
    [[ $rc -eq 0 ]] || { echo "$label restore failed with rc=$rc" >&2; return 1; }

    [[ $(grep -Ec '^CG_FINGERPRINT .* result=PASS$' "$run/restore.log" || true) -eq 1 ]]
    [[ $(grep -Ec "^CG_LOGICAL16_RMW_SELECTION treatment=$treatment .*performance_promotable=0$" "$run/restore.log" || true) -eq 1 ]]
    [[ $(grep -Ec "^CG_LOGICAL16_RMW_TERMINAL treatment=$treatment .* result=PASS$" "$run/restore.log" || true) -eq 1 ]]
    [[ $(grep -Fxc 'ROI End!!!' "$run/restore.log" || true) -eq 1 ]]
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' "$run/restore.log" || true) -eq 1 ]]
    [[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' "$run/restore.log" || true) -eq 0 ]]
    for resolved in \
        num_tile_elements=16384 physical_tile_elements=4096 \
        num_offset_table_entries=16384 num_offset_table_epoch_entries=16384 \
        soa_jit_predicate_active_credits=16 soa_jit_active_value_owners=32; do
        grep -Fqx "$resolved" "$run/config.ini" || {
            echo "$label missing resolved config: $resolved" >&2
            return 1
        }
    done

    local terminal full_windows index_words value_words instructions terminals
    terminal=$(grep '^CG_LOGICAL16_RMW_TERMINAL ' "$run/restore.log")
    full_windows=$(sed -n 's/.* full_windows=\([0-9][0-9]*\).*/\1/p' <<<"$terminal")
    index_words=$(sed -n 's/.* staged_index_words=\([0-9][0-9]*\).*/\1/p' <<<"$terminal")
    value_words=$(sed -n 's/.* staged_value_words=\([0-9][0-9]*\).*/\1/p' <<<"$terminal")
    instructions=$(stat_sum "$run/stats.txt" IND_SoaJitInstructions)
    terminals=$(stat_sum "$run/stats.txt" IND_SoaJitTerminalCompletions)
    if [[ $treatment == legacy ]]; then
        [[ $full_windows -eq 0 && $index_words -eq 0 && $value_words -eq 0 ]]
        [[ $instructions -eq 0 && $terminals -eq 0 ]]
    else
        [[ $full_windows -gt 0 ]]
        [[ $index_words -eq $((full_windows * 16384)) ]]
        [[ $value_words -eq $index_words ]]
        [[ $instructions -eq $full_windows && $terminals -eq $full_windows ]]
        [[ $(stat_sum "$run/stats.txt" IND_SoaJitSelected) -eq $index_words ]]
        [[ $(stat_sum "$run/stats.txt" IND_SoaJitPredicateRejected) -eq 0 ]]
        [[ $(stat_sum "$run/stats.txt" IND_SoaJitAliasesApplied) -eq $index_words ]]
        [[ $(stat_sum "$run/stats.txt" IND_SoaJitAWriteIssues) -eq \
           $(stat_sum "$run/stats.txt" IND_SoaJitAWriteResponses) ]]
    fi
    fingerprints[$label]=$(grep '^CG_FINGERPRINT ' "$run/restore.log")
}

run_arm legacy legacy_4k
run_arm residual residual_soa_jit
[[ ${fingerprints[legacy]} == "${fingerprints[residual]}" ]] || {
    echo "legacy/residual exact CG fingerprint mismatch" >&2
    exit 1
}

sha256sum "$out/input"/*.selector > "$out/selector_sha256.after"
cmp --silent "$out/selector_sha256.before" "$out/selector_sha256.after" || {
    echo "an immutable selector changed during the run" >&2
    exit 1
}
{
    printf 'terminal=true\ncorrect=true\n'
    printf 'fingerprint=%s\n' "${fingerprints[legacy]}"
    printf 'logical_elements=16384\nphysical_tile_elements=4096\n'
    printf 'soa_jit_predicate_active_credits=16\n'
    printf 'soa_jit_active_value_owners=32\n'
    printf 'performance_promotable=0\nspeedup_claim=0\n'
} > "$out/result.txt"
printf 'PASS CG logical-16 residual RMW correctness smoke out=%s\n' "$out"
