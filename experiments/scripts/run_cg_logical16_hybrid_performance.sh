#!/usr/bin/env bash
# Exact shared-checkpoint performance gate for the CG response-bearing handoff.
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
checkpoint_selector="$out/input/checkpoint.selector"
control_selector="$out/input/control.selector"
treatment_selector="$out/input/treatment.selector"
# CG reaches m5_checkpoint before parsing a selector. This neutral selector
# is therefore checkpoint-identical to both post-restore producer choices.
printf '%s\n' 'token_stream_ld residual_soa_jit' > "$checkpoint_selector"
printf '%s\n' 'token_stream_ld residual_soa_jit' > "$control_selector"
printf '%s\n' 'token_stream_ld residual_soa_jit_response_bearing' > "$treatment_selector"
chmod 0444 "$checkpoint_selector" "$control_selector" "$treatment_selector"

guest="$out/bin/cg_logical16_hybrid_gate"
"$cxx" -I"$root/benchmarks/API" -I"$root/include" -I"$root/util/m5/src" \
    -std=c++11 -O3 -Wall -Wextra -Werror -Wno-ignored-qualifiers \
    -Wno-unused-parameter -fopenmp -DGEM5 -DMAA -DMAA_VIRTUAL_GATHER \
    -DMAA_GENERAL_VIRTUAL_CONSUMER -DMAA_CONSUMER_TILE_SIZE=4096 \
    -DCG_LOGICAL16_RMW -DCG_FP_ENABLE -DCG_NA=1024 -DNUM_CORES=4 \
    -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$guest"

source_commit=$(git -C "$root" rev-parse HEAD)
sha256sum "$gem5" "$guest" "$checkpoint_selector" "$control_selector" "$treatment_selector" "$source_file" "$config" "$ramulator" "$0" \
    > "$out/input/artifact_sha256.txt"
checkpoint_selector_sha=$(sha256sum "$checkpoint_selector" | awk '{print $1}')
control_selector_sha=$(sha256sum "$control_selector" | awk '{print $1}')
treatment_selector_sha=$(sha256sum "$treatment_selector" | awk '{print $1}')
{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'comparison=one_guest_one_checkpoint_response_bearing_publisher_only\n'
    printf 'checkpoint_selector_sha256=%s\n' "$checkpoint_selector_sha"
    printf 'control_selector_sha256=%s\n' "$control_selector_sha"
    printf 'treatment_selector_sha256=%s\n' "$treatment_selector_sha"
    printf 'replicas=%s\n' "$replicas"
    printf 'logical_elements=16384\nphysical_tile_elements=4096\n'
    printf 'offset_table_entries=16384\noffset_table_epoch_entries=16384\n'
    printf 'soa_jit_predicate_active_credits=16\nsoa_jit_active_value_owners=32\n'
    printf 'publisher_line_credits_per_stream=8\n'
    printf 'timeout_seconds=%s\nparallel_restores=%s\n' "$timeout_seconds" "$((replicas * 2))"
} > "$out/manifest.txt"

# CG consumes its selector only after restore, so the checkpoint is identical
# and treatment-neutral while the control/treatment producer differs only then.
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${timeout_command[@]}" \
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" "$config" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1 \
    --cmd "$guest" --options "MAA_DEFERRED $checkpoint_selector" \
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
    --cmd "$guest" --checkpoint-dir "$out/checkpoint"
)

stat_sum() {
    local stats=$1 suffix=$2
    awk -v suffix="$suffix" '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ ("_" suffix "$") { sum += $2 }
        /^---------- End Simulation Statistics/ && section == 1 { printf "%.0f\n", sum; exit }
    ' "$stats"
}

header=$'arm\treplica\tsimTicks\tfingerprint_sha256\tterminal_sha256\tconfig_sha256\tselected\tterminal_completions\tvalue_read_issues\tvalue_read_responses\tvalue_fills\ta_read_issues\ta_read_responses\ta_write_issues\ta_write_responses\tpublish_issues\tpublish_accepts\tpublish_responses\tpublish_terminals\tpublish_overlap'
printf '%s\n' "$header" > "$out/matrix.tsv"
declare -A ticks fingerprints terminals configs selected value_issues a_read_issues a_write_issues publish_issues publish_accepts publish_responses publish_terminals publish_overlap

run_arm() {
    local arm=$1 replica=$2
    local name="${arm}_r${replica}"
    local run="$out/runs/$name"
    local selector
    if [[ $arm == control ]]; then
        selector=$control_selector
    else
        selector=$treatment_selector
    fi
    local -a command=("$gem5" --outdir="$run" "${common[@]}" --options "MAA_DEFERRED $selector")
    mkdir -p "$run"
    printf '%q ' "${command[@]}" > "$run/command.txt"; printf '\n' >> "$run/command.txt"
    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${timeout_command[@]}" "${command[@]}" > "$run/restore.log" 2>&1
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$run/restore.exit"
    [[ $rc -eq 0 ]]
    [[ $(grep -Ec '^CG_FINGERPRINT .* result=PASS$' "$run/restore.log") -eq 1 ]]
    if [[ $arm == control ]]; then
        [[ $(grep -Ec '^CG_LOGICAL16_RMW_TERMINAL treatment=residual_soa_jit .*producer=cpu_after_spd_completion .*performance_promotable=0 result=PASS$' "$run/restore.log") -eq 1 ]]
    else
        [[ $(grep -Ec '^CG_LOGICAL16_RMW_TERMINAL treatment=residual_soa_jit_response_bearing .*producer=response_bearing_spd_overlap .*performance_promotable=1 result=PASS$' "$run/restore.log") -eq 1 ]]
    fi
    [[ $(grep -Fxc 'ROI End!!!' "$run/restore.log") -eq 1 ]]
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' "$run/restore.log") -eq 1 ]]
    [[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' "$run/restore.log") -eq 0 ]]
    for resolved in num_tile_elements=16384 physical_tile_elements=4096 \
        num_offset_table_entries=16384 num_offset_table_epoch_entries=16384 \
        soa_jit_predicate_active_credits=16 soa_jit_active_value_owners=32 \
        soa_jit_value_prefetch_credits=0; do grep -Fqx "$resolved" "$run/config.ini"; done
    cmp -s "$out/checkpoint.files.sha256" <(cd "$out/checkpoint" && find . -type f -print0 | sort -z | xargs -0 sha256sum)

    local instruction terminal value_responses fills a_read_responses a_write_responses expected_publish
    instruction=$(stat_sum "$run/stats.txt" IND_SoaJitInstructions); terminal=$(stat_sum "$run/stats.txt" IND_SoaJitTerminalCompletions)
    selected[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitSelected)
    value_issues[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadIssues); value_responses=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadResponses); fills=$(stat_sum "$run/stats.txt" IND_SoaJitValueFills)
    a_read_issues[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitAReadIssues); a_read_responses=$(stat_sum "$run/stats.txt" IND_SoaJitAReadResponses)
    a_write_issues[$name]=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteIssues); a_write_responses=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteResponses)
    publish_issues[$name]=$(stat_sum "$run/stats.txt" STR_PublishIssues); publish_accepts[$name]=$(stat_sum "$run/stats.txt" STR_PublishAccepts)
    publish_responses[$name]=$(stat_sum "$run/stats.txt" STR_PublishWriteResponses); publish_terminals[$name]=$(stat_sum "$run/stats.txt" STR_PublishTerminals)
    publish_overlap[$name]=$(stat_sum "$run/stats.txt" STR_PublishOverlapIssues)
    [[ $instruction -gt 0 && $instruction -eq $terminal && ${selected[$name]} -gt 0 ]]
    [[ ${value_issues[$name]} -eq $value_responses && $value_responses -eq $fills ]]
    [[ ${a_read_issues[$name]} -eq $a_read_responses && ${a_read_issues[$name]} -eq ${a_write_issues[$name]} && ${a_write_issues[$name]} -eq $a_write_responses ]]
    if [[ $arm == treatment ]]; then
        expected_publish=$((instruction * 2048))
        [[ ${publish_issues[$name]} -eq $expected_publish ]]
        [[ ${publish_accepts[$name]} -eq $expected_publish ]]
        [[ ${publish_responses[$name]} -eq $expected_publish ]]
        [[ ${publish_terminals[$name]} -eq $((instruction * 8)) ]]
        [[ ${publish_overlap[$name]} -gt 0 ]]
    else
        [[ ${publish_issues[$name]} -eq 0 && ${publish_accepts[$name]} -eq 0 ]]
        [[ ${publish_responses[$name]} -eq 0 && ${publish_terminals[$name]} -eq 0 ]]
        [[ ${publish_overlap[$name]} -eq 0 ]]
    fi
    ticks[$name]=$(awk '$1 == "simTicks" { print $2; exit }' "$run/stats.txt")
    [[ ${ticks[$name]} =~ ^[1-9][0-9]*$ ]]
    fingerprints[$name]=$(grep '^CG_FINGERPRINT ' "$run/restore.log" | sha256sum | awk '{print $1}')
    terminals[$name]=$(grep '^CG_LOGICAL16_RMW_TERMINAL ' "$run/restore.log" | sha256sum | awk '{print $1}')
    configs[$name]=$(sha256sum "$run/config.ini" | awk '{print $1}')
    {
        printf 'source_commit=%s\ngem5_sha256=%s\nguest_sha256=%s\nselector_sha256=%s\ncheckpoint_sha256=%s\n' "$source_commit" "$(sha256sum "$gem5" | awk '{print $1}')" "$(sha256sum "$guest" | awk '{print $1}')" "$(sha256sum "$selector" | awk '{print $1}')" "$checkpoint_sha"
        printf 'config_sha256=%s\nsimTicks=%s\nfingerprint_sha256=%s\nterminal_sha256=%s\n' "${configs[$name]}" "${ticks[$name]}" "${fingerprints[$name]}" "${terminals[$name]}"
    } > "$run/provenance.txt"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$arm" "$replica" "${ticks[$name]}" "${fingerprints[$name]}" "${terminals[$name]}" "${configs[$name]}" "${selected[$name]}" "$terminal" "${value_issues[$name]}" "$value_responses" "$fills" "${a_read_issues[$name]}" "$a_read_responses" "${a_write_issues[$name]}" "$a_write_responses" "${publish_issues[$name]}" "${publish_accepts[$name]}" "${publish_responses[$name]}" "${publish_terminals[$name]}" "${publish_overlap[$name]}" > "$run/result.tsv"
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
while IFS=$'\t' read -r arm replica tick fingerprint terminal config select _ value_issue _ _ a_read _ a_write _ publish_issue publish_accept publish_response publish_terminal publish_over; do
    name="${arm}_r${replica}"
    ticks[$name]=$tick; fingerprints[$name]=$fingerprint
    terminals[$name]=$terminal; configs[$name]=$config
    selected[$name]=$select; value_issues[$name]=$value_issue
    a_read_issues[$name]=$a_read; a_write_issues[$name]=$a_write
    publish_issues[$name]=$publish_issue; publish_accepts[$name]=$publish_accept
    publish_responses[$name]=$publish_response; publish_terminals[$name]=$publish_terminal
    publish_overlap[$name]=$publish_over
done < <(tail -n +2 "$out/matrix.tsv")
for ((replica = 1; replica <= replicas; replica++)); do
    control="control_r$replica"; treatment="treatment_r$replica"
    [[ ${fingerprints[$control]} == ${fingerprints[$treatment]} && ${configs[$control]} == ${configs[$treatment]} ]]
    [[ ${selected[$control]} -eq ${selected[$treatment]} && ${a_read_issues[$control]} -eq ${a_read_issues[$treatment]} && ${a_write_issues[$control]} -eq ${a_write_issues[$treatment]} ]]
    [[ ${publish_issues[$control]} -eq 0 && ${publish_issues[$treatment]} -gt 0 ]]
    [[ ${ticks[$treatment]} -le ${ticks[$control]} ]] || {
        echo "response-bearing candidate is slower in replica $replica" >&2
        exit 1
    }
done
{
    printf 'decision=PERFORMANCE_PROMOTABLE\nshared_checkpoint_sha256=%s\n' "$checkpoint_sha"
    for ((replica = 1; replica <= replicas; replica++)); do control="control_r$replica"; treatment="treatment_r$replica"; awk -v r="$replica" -v c="${ticks[$control]}" -v t="${ticks[$treatment]}" 'BEGIN { printf "replica_%s_control_simTicks=%s\\nreplica_%s_treatment_simTicks=%s\\nreplica_%s_speedup=%.9f\\n", r,c,r,t,r,c/t }'; done
} > "$out/decision.txt"
touch "$out/gate.complete"
cat "$out/matrix.tsv"; cat "$out/decision.txt"
echo "PASS CG logical-16 hybrid performance gate out=$out"
