#!/usr/bin/env bash
# Exact shared-checkpoint performance gate for the CG residual SoA/JIT path.
set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "usage: $0 GEM5_BIN OUTDIR [TREATMENT_GEM5_FLAG ...]" >&2
    echo "example: $0 build/X86/gem5.opt /tmp/cg-gate --maa_soa_jit_pre_a_value_lookahead" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
out=$(realpath -m "$2")
shift 2
treatment_flags=("$@")
[[ ${#treatment_flags[@]} -gt 0 ]] || {
    echo "at least one explicit simulator-only treatment flag is required" >&2
    exit 2
}
for flag in "${treatment_flags[@]}"; do
    [[ $flag == --maa_soa_jit_* ]] || {
        echo "treatment flag is not an explicit SoA/JIT simulator flag: $flag" >&2
        exit 2
    }
done
treatment_config_lines=()
pre_a_treatment=false
for flag in "${treatment_flags[@]}"; do
    resolved=${flag#--}
    resolved=${resolved//-/_}
    [[ $resolved == *=* ]] || resolved+="=true"
    treatment_config_lines+=("$resolved")
    [[ $resolved == 'maa_soa_jit_pre_a_value_lookahead=true' ]] && \
        pre_a_treatment=true
done

config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
source_file="$root/benchmarks/NAS/cg/cg.cpp"
cxx=${CXX:-g++}
replicas=${CG_HYBRID_REPLICAS:-2}
timeout_seconds=${CG_HYBRID_TIMEOUT_SECONDS:-0}

[[ -x $gem5 ]] || { echo "missing gem5 binary: $gem5" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
[[ $replicas =~ ^[2-9][0-9]*$ ]] || {
    echo "CG_HYBRID_REPLICAS must be at least two" >&2; exit 2;
}
[[ $timeout_seconds =~ ^[0-9]+$ ]] || {
    echo "CG_HYBRID_TIMEOUT_SECONDS must be a non-negative integer" >&2; exit 2;
}
timeout_command=()
((timeout_seconds == 0)) || timeout_command=(timeout "$timeout_seconds")
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence run from a dirty worktree" >&2
    git -C "$root" status --short >&2
    exit 1
}

mkdir -p "$out/bin" "$out/input" "$out/checkpoint" "$out/runs"
selector="$out/input/residual_soa_jit.selector"
printf '%s\n' 'token_stream_ld residual_soa_jit' > "$selector"
chmod 0444 "$selector"

guest="$out/bin/cg_logical16_hybrid_gate"
"$cxx" -I"$root/benchmarks/API" -I"$root/include" -I"$root/util/m5/src" \
    -std=c++11 -O3 -Wall -Wextra -Werror -Wno-ignored-qualifiers \
    -Wno-unused-parameter -fopenmp -DGEM5 -DMAA -DMAA_VIRTUAL_GATHER \
    -DMAA_GENERAL_VIRTUAL_CONSUMER -DMAA_CONSUMER_TILE_SIZE=4096 \
    -DCG_LOGICAL16_RMW -DCG_FP_ENABLE -DCG_NA=1024 -DNUM_CORES=4 \
    -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$guest"

source_commit=$(git -C "$root" rev-parse HEAD)
sha256sum "$gem5" "$guest" "$selector" "$source_file" "$config" "$ramulator" "$0" \
    > "$out/input/artifact_sha256.txt"
selector_sha=$(sha256sum "$selector" | awk '{print $1}')
{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'comparison=one_guest_one_selector_one_checkpoint_treatment_flags_only\n'
    printf 'selector_sha256=%s\n' "$selector_sha"
    printf 'replicas=%s\n' "$replicas"
    printf 'logical_elements=16384\nphysical_tile_elements=4096\n'
    printf 'offset_table_entries=16384\noffset_table_epoch_entries=16384\n'
    printf 'soa_jit_predicate_active_credits=16\nsoa_jit_active_value_owners=32\n'
    printf 'sequential_value_prefetch_credits=0\n'
    printf 'timeout_seconds=%s\nparallel_restores=%s\n' "$timeout_seconds" "$((replicas * 2))"
    printf 'treatment_flags='
    printf '%q ' "${treatment_flags[@]}"
    printf '\n'
} > "$out/manifest.txt"

# The selector is supplied before m5_checkpoint but CG consumes it only after
# restore; therefore this one checkpoint is selector-identical and treatment-neutral.
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${timeout_command[@]}" \
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" "$config" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1 \
    --cmd "$guest" --options "MAA_DEFERRED $selector" \
    > "$out/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' "$out/checkpoint.log") -eq 1 ]]
! grep -Eq 'CG_LOGICAL16_RMW_SELECTION|CG_FINGERPRINT|ROI End!!!' "$out/checkpoint.log"
(
    cd "$out/checkpoint"
    find . -type f -print0 | sort -z | xargs -0 sha256sum
) > "$out/checkpoint.files.sha256"
checkpoint_sha=$(sha256sum "$out/checkpoint.files.sha256" | awk '{print $1}')
printf '%s\n' "$checkpoint_sha" > "$out/checkpoint.identity.sha256"

common=(
    --listener-mode=off "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --sys-clock 3.2GHz --cpu-clock 3.2GHz
    --caches --l1d_size=32kB --l1d_assoc=8 --l1d_mshrs=16 --l1d_write_buffers=8
    --l1i_size=32kB --l1i_assoc=8 --l1i_mshrs=16 --l1i_write_buffers=8
    --l2cache --l2_size=256kB --l2_assoc=4 --l2_mshrs=32 --l2_write_buffers=16
    --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
    --cacheline_size=64 --mem-type Ramulator2 --ramulator-config "$ramulator" --mem-channels=1
    --maa --maa_num_maas=1 --maa_num_indirect_units_per_maa=4
    --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096
    --maa_num_offset_table_entries=16384 --maa_num_offset_table_epoch_entries=16384
    --maa_num_initial_row_table_slices=16 --maa_soa_jit_predicate_active_credits=16
    --maa_soa_jit_active_value_owners=32 --maa_soa_jit_value_prefetch_credits=0
    --cmd "$guest" --options "MAA_DEFERRED $selector" --checkpoint-dir "$out/checkpoint"
)

stat_sum() {
    local stats=$1 suffix=$2
    awk -v suffix="$suffix" '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ ("_" suffix "$") { sum += $2 }
        /^---------- End Simulation Statistics/ && section == 1 { printf "%.0f\\n", sum; exit }
    ' "$stats"
}

normalize_config() {
    # The only permitted resolved-config delta is the supplied treatment flag.
    local config_file=$1 joined line
    joined=$(IFS='|'; printf '%s' "${treatment_config_lines[*]}")
    awk -v permitted="$joined" '
        BEGIN { split(permitted, lines, "|") }
        { for (i in lines) if ($0 == lines[i]) next; print }
    ' "$config_file" | sha256sum | awk '{print $1}'
}

header=$'arm\treplica\tsimTicks\tfingerprint_sha256\tterminal_sha256\tconfig_common_sha256\tselected\tterminal_completions\tvalue_read_issues\tvalue_read_responses\tvalue_fills\ta_read_issues\ta_read_responses\ta_write_issues\ta_write_responses\tpre_a_issues\tpre_a_ready\tpre_a_uses'
printf '%s\n' "$header" > "$out/matrix.tsv"
declare -A ticks fingerprints terminals configs selected value_issues a_read_issues a_write_issues pre_a_issues

run_arm() {
    local arm=$1 replica=$2
    local name="${arm}_r${replica}"
    local run="$out/runs/$name"
    local -a command=("$gem5" --outdir="$run" "${common[@]}")
    [[ $arm == control ]] || command+=("${treatment_flags[@]}")
    mkdir -p "$run"
    printf '%q ' "${command[@]}" > "$run/command.txt"; printf '\n' >> "$run/command.txt"
    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${timeout_command[@]}" "${command[@]}" > "$run/restore.log" 2>&1
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$run/restore.exit"
    [[ $rc -eq 0 ]]
    [[ $(grep -Ec '^CG_FINGERPRINT .* result=PASS$' "$run/restore.log") -eq 1 ]]
    [[ $(grep -Ec '^CG_LOGICAL16_RMW_TERMINAL treatment=residual_soa_jit .* result=PASS$' "$run/restore.log") -eq 1 ]]
    [[ $(grep -Fxc 'ROI End!!!' "$run/restore.log") -eq 1 ]]
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' "$run/restore.log") -eq 1 ]]
    [[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' "$run/restore.log") -eq 0 ]]
    for resolved in num_tile_elements=16384 physical_tile_elements=4096 \
        num_offset_table_entries=16384 num_offset_table_epoch_entries=16384 \
        soa_jit_predicate_active_credits=16 soa_jit_active_value_owners=32 \
        soa_jit_value_prefetch_credits=0; do grep -Fqx "$resolved" "$run/config.ini"; done
    if [[ $arm == control ]]; then
        [[ $pre_a_treatment == false ]] || grep -Fqx 'soa_jit_pre_a_value_lookahead=false' "$run/config.ini"
    else
        for resolved in "${treatment_config_lines[@]}"; do
            grep -Fqx "$resolved" "$run/config.ini"
        done
    fi
    cmp -s "$out/checkpoint.files.sha256" <(cd "$out/checkpoint" && find . -type f -print0 | sort -z | xargs -0 sha256sum)

    local instruction terminal value_responses fills a_read_responses a_write_responses pre_a_ready pre_a_uses
    instruction=$(stat_sum "$run/stats.txt" IND_SoaJitInstructions); terminal=$(stat_sum "$run/stats.txt" IND_SoaJitTerminalCompletions)
    selected[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitSelected)
    value_issues[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadIssues); value_responses=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadResponses); fills=$(stat_sum "$run/stats.txt" IND_SoaJitValueFills)
    a_read_issues[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitAReadIssues); a_read_responses=$(stat_sum "$run/stats.txt" IND_SoaJitAReadResponses)
    a_write_issues[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteIssues); a_write_responses=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteResponses)
    pre_a_issues[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitPreAValueIssues); pre_a_ready=$(stat_sum "$run/stats.txt" IND_SoaJitPreAValueReadyAtAResponse); pre_a_uses=$(stat_sum "$run/stats.txt" IND_SoaJitPreAValueUses)
    [[ $instruction -gt 0 && $instruction -eq $terminal && ${selected[$name]} -gt 0 ]]
    [[ ${value_issues[$name]} -eq $value_responses && $value_responses -eq $fills ]]
    [[ ${a_read_issues[$name]} -eq $a_read_responses && ${a_read_issues[$name]} -eq ${a_write_issues[$name]} && ${a_write_issues[$name]} -eq $a_write_responses ]]
    if [[ $arm == treatment && $pre_a_treatment == true ]]; then
        [[ ${pre_a_issues[$name]} -gt 0 && ${pre_a_issues[$name]} -eq $pre_a_uses && $pre_a_ready -le $pre_a_uses ]]
    else
        [[ ${pre_a_issues[$name]} -eq 0 && $pre_a_ready -eq 0 && $pre_a_uses -eq 0 ]]
    fi
    ticks[$name]=$(awk '$1 == "simTicks" { print $2; exit }' "$run/stats.txt")
    [[ ${ticks[$name]} =~ ^[1-9][0-9]*$ ]]
    fingerprints[$name]=$(grep '^CG_FINGERPRINT ' "$run/restore.log" | sha256sum | awk '{print $1}')
    terminals[$name]=$(grep '^CG_LOGICAL16_RMW_TERMINAL ' "$run/restore.log" | sha256sum | awk '{print $1}')
    configs[$name]=$(normalize_config "$run/config.ini")
    {
        printf 'source_commit=%s\ngem5_sha256=%s\nguest_sha256=%s\nselector_sha256=%s\ncheckpoint_sha256=%s\n' "$source_commit" "$(sha256sum "$gem5" | awk '{print $1}')" "$(sha256sum "$guest" | awk '{print $1}')" "$selector_sha" "$checkpoint_sha"
        printf 'config_common_sha256=%s\nsimTicks=%s\nfingerprint_sha256=%s\nterminal_sha256=%s\n' "${configs[$name]}" "${ticks[$name]}" "${fingerprints[$name]}" "${terminals[$name]}"
    } > "$run/provenance.txt"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$arm" "$replica" "${ticks[$name]}" "${fingerprints[$name]}" "${terminals[$name]}" "${configs[$name]}" "${selected[$name]}" "$terminal" "${value_issues[$name]}" "$value_responses" "$fills" "${a_read_issues[$name]}" "$a_read_responses" "${a_write_issues[$name]}" "$a_write_responses" "${pre_a_issues[$name]}" "$pre_a_ready" "$pre_a_uses" > "$run/result.tsv"
}

pids=()
for ((replica = 1; replica <= replicas; replica++)); do
    run_arm control "$replica" & pids+=("$!")
    run_arm treatment "$replica" & pids+=("$!")
done
for pid in "${pids[@]}"; do wait "$pid"; done
for ((replica = 1; replica <= replicas; replica++)); do
    cat "$out/runs/control_r${replica}/result.tsv"
    cat "$out/runs/treatment_r${replica}/result.tsv"
done >> "$out/matrix.tsv"
while IFS=$'\t' read -r arm replica tick fingerprint terminal config select _ value_issue _ _ a_read _ a_write _ pre_a_issue _ _; do
    name="${arm}_r${replica}"
    ticks[$name]=$tick; fingerprints[$name]=$fingerprint
    terminals[$name]=$terminal; configs[$name]=$config
    selected[$name]=$select; value_issues[$name]=$value_issue
    a_read_issues[$name]=$a_read; a_write_issues[$name]=$a_write
    pre_a_issues[$name]=$pre_a_issue
done < <(tail -n +2 "$out/matrix.tsv")
for ((replica = 1; replica <= replicas; replica++)); do
    control="control_r$replica"; treatment="treatment_r$replica"
    [[ ${fingerprints[$control]} == ${fingerprints[$treatment]} && ${terminals[$control]} == ${terminals[$treatment]} && ${configs[$control]} == ${configs[$treatment]} ]]
    [[ ${selected[$control]} -eq ${selected[$treatment]} && ${a_read_issues[$control]} -eq ${a_read_issues[$treatment]} && ${a_write_issues[$control]} -eq ${a_write_issues[$treatment]} ]]
done
{
    printf 'decision=VALID_MEASURED_PAIR\nshared_checkpoint_sha256=%s\n' "$checkpoint_sha"
    for ((replica = 1; replica <= replicas; replica++)); do control="control_r$replica"; treatment="treatment_r$replica"; awk -v r="$replica" -v c="${ticks[$control]}" -v t="${ticks[$treatment]}" 'BEGIN { printf "replica_%s_control_simTicks=%s\\nreplica_%s_treatment_simTicks=%s\\nreplica_%s_speedup=%.9f\\n", r,c,r,t,r,c/t }'; done
} > "$out/decision.txt"
touch "$out/gate.complete"
cat "$out/matrix.tsv"; cat "$out/decision.txt"
echo "PASS CG logical-16 hybrid performance gate out=$out"
