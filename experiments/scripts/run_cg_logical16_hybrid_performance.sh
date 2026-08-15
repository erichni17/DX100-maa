#!/usr/bin/env bash
# Exact shared-checkpoint performance gate for the CG response-bearing handoff.
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "usage: $0 GEM5_BIN EXPECTED_GEM5_SHA256 OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
expected_gem5_sha=$2
out=$(realpath -m "$3")

config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
source_file="$root/benchmarks/NAS/cg/cg.cpp"
cxx=${CXX:-g++}
replicas=${CG_HYBRID_REPLICAS:-2}
timeout_seconds=${CG_HYBRID_TIMEOUT_SECONDS:-0}
allow_dirty_source=${CG_HYBRID_ALLOW_DIRTY_SOURCE:-0}

[[ -x $gem5 ]] || { echo "missing gem5 binary: $gem5" >&2; exit 2; }
[[ $expected_gem5_sha =~ ^[0-9a-f]{64}$ ]] || {
    echo "EXPECTED_GEM5_SHA256 must be 64 lowercase hexadecimal characters" >&2
    exit 2
}
actual_gem5_sha=$(sha256sum "$gem5" | awk '{print $1}')
[[ $actual_gem5_sha == "$expected_gem5_sha" ]] || {
    echo "gem5 provenance preflight failed: expected $expected_gem5_sha, got $actual_gem5_sha" >&2
    exit 1
}
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
[[ $replicas =~ ^[2-9][0-9]*$ ]] || {
    echo "CG_HYBRID_REPLICAS must be at least two" >&2; exit 2;
}
[[ $timeout_seconds =~ ^[0-9]+$ ]] || {
    echo "CG_HYBRID_TIMEOUT_SECONDS must be a non-negative integer" >&2; exit 2;
}
[[ $allow_dirty_source == 0 || $allow_dirty_source == 1 ]] || {
    echo "CG_HYBRID_ALLOW_DIRTY_SOURCE must be zero or one" >&2; exit 2;
}
timeout_command=()
((timeout_seconds == 0)) || timeout_command=(timeout "$timeout_seconds")
source_status=$(git -C "$root" status --short)
if [[ -n $source_status && $allow_dirty_source != 1 ]]; then
    echo "refusing evidence run from a dirty worktree" >&2
    printf '%s\n' "$source_status" >&2
    exit 1
fi

mkdir -p "$out/bin" "$out/input" "$out/checkpoint" "$out/runs"
selector="$out/input/arm.selector"
# This invalid sentinel proves that checkpoint creation did not consume the
# deferred selector.  Only this pathname is embedded in the guest checkpoint.
printf '%s\n' 'checkpoint_pending' > "$selector"
chmod 0444 "$selector"
selector_path=$(realpath "$selector")
printf '%s\n' "$source_status" > "$out/input/source.status"
git -C "$root" diff --binary HEAD > "$out/input/source.diff"
source_diff_sha=$(sha256sum "$out/input/source.diff" | awk '{print $1}')
sha256sum "$selector" > "$out/input/selector.sha256.at_checkpoint"

guest="$out/bin/cg_logical16_hybrid_gate"
"$cxx" -I"$root/benchmarks/API" -I"$root/include" -I"$root/util/m5/src" \
    -std=c++11 -O3 -Wall -Wextra -Werror -Wno-ignored-qualifiers \
    -Wno-unused-parameter -fopenmp -DGEM5 -DMAA -DMAA_VIRTUAL_GATHER \
    -DMAA_GENERAL_VIRTUAL_CONSUMER -DMAA_CONSUMER_TILE_SIZE=4096 \
    -DCG_LOGICAL16_RMW -DCG_FP_ENABLE -DCG_NA=1024 -DNUM_CORES=4 \
    -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$guest"

source_commit=$(git -C "$root" rev-parse HEAD)
guest_sha=$(sha256sum "$guest" | awk '{print $1}')
control_contents='token_stream_ld residual_soa_jit'
treatment_contents='token_stream_ld residual_soa_jit_response_bearing'
control_selector_sha=$(printf '%s\n' "$control_contents" | sha256sum | awk '{print $1}')
treatment_selector_sha=$(printf '%s\n' "$treatment_contents" | sha256sum | awk '{print $1}')
[[ $control_selector_sha != "$treatment_selector_sha" ]]
sha256sum "$gem5" "$guest" "$selector" "$source_file" "$config" "$ramulator" "$0" \
    > "$out/input/artifact_sha256.txt"
{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'source_dirty=%s\n' "$([[ -n $source_status ]] && printf 1 || printf 0)"
    printf 'source_diff_sha256=%s\n' "$source_diff_sha"
    printf 'gem5_expected_sha256=%s\ngem5_actual_sha256=%s\n' "$expected_gem5_sha" "$actual_gem5_sha"
    printf 'guest_sha256=%s\n' "$guest_sha"
    printf 'comparison=one_guest_one_checkpoint_response_bearing_publisher_only\n'
    printf 'immutable_selector_path=%s\n' "$selector_path"
    printf 'control_selector_sha256=%s\n' "$control_selector_sha"
    printf 'treatment_selector_sha256=%s\n' "$treatment_selector_sha"
    printf 'replicas=%s\n' "$replicas"
    printf 'logical_elements=16384\nphysical_tile_elements=4096\n'
    printf 'offset_table_entries=16384\noffset_table_epoch_entries=16384\n'
    printf 'soa_jit_predicate_active_credits=16\nsoa_jit_active_value_owners=32\n'
    printf 'publisher_line_credits_per_stream=8\n'
    printf 'timeout_seconds=%s\nparallel_restores=%s\n' "$timeout_seconds" "$replicas"
    printf 'arm_phases=control_then_treatment\n'
} > "$out/manifest.txt"

# The guest reaches m5_checkpoint before it opens the selector.  Every restore
# therefore consumes this one checkpoint-bound absolute pathname; only its
# contents change, atomically and between phases, after checkpoint creation.
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${timeout_command[@]}" \
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" "$config" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1 \
    --cmd "$guest" --options "MAA_DEFERRED $selector_path" \
    > "$out/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' "$out/checkpoint.log") -eq 1 ]]
! grep -Eq 'CG_LOGICAL16_RMW_SELECTION|CG_FINGERPRINT|ROI End!!!' "$out/checkpoint.log"
cmp -s "$out/input/selector.sha256.at_checkpoint" <(sha256sum "$selector")
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
    --options "MAA_DEFERRED $selector_path"
)

stat_sum() {
    local stats=$1 suffix=$2 allow_absent=${3:-0}
    awk -v suffix="$suffix" -v allow_absent="$allow_absent" '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ ("_" suffix "$") { sum += $2; seen = 1 }
        /^---------- End Simulation Statistics/ && section == 1 {
            if (!seen && allow_absent != 1) exit 1
            printf "%.0f\n", sum
            exit
        }
    ' "$stats"
}

# config.ini contains three run-local redirect paths.  Those paths are not a
# modeled delta.  The selector path must occur exactly once and is identical in
# every arm; normalize only the verified run directory before hashing.
comparable_config_sha() {
    local config_file=$1 run=$2
    awk -v run="$run" -v selector="$selector_path" '
        {
            line = $0
            selector_occurrences += gsub(selector, selector, line)
            run_occurrences += gsub(run, "__CG_RUN_DIR__", line)
            print line
        }
        END {
            if (selector_occurrences != 1 || run_occurrences != 3) {
                printf "config provenance mismatch: selector=%d run_dir=%d\n",
                    selector_occurrences, run_occurrences > "/dev/stderr"
                exit 1
            }
        }
    ' "$config_file" | sha256sum | awk '{print $1}'
}

set_selector_contents() {
    local arm=$1 contents=$2 expected_sha=$3
    local replacement
    replacement=$(mktemp "$out/input/.arm.selector.${arm}.XXXXXX")
    printf '%s\n' "$contents" > "$replacement"
    chmod 0444 "$replacement"
    [[ $(sha256sum "$replacement" | awk '{print $1}') == "$expected_sha" ]]
    mv -f "$replacement" "$selector_path"
    [[ $(realpath "$selector_path") == "$selector_path" ]]
    [[ $(sha256sum "$selector_path" | awk '{print $1}') == "$expected_sha" ]]
    printf '%s\t%s\t%s\n' "$arm" "$selector_path" "$expected_sha" >> "$out/input/selector.transitions.tsv"
}

verify_checkpoint_files() {
    local phase=$1
    local inventory="$out/checkpoint.files.sha256.after_${phase}"
    (
        cd "$out/checkpoint"
        find . -type f -print0 | sort -z | xargs -0 sha256sum
    ) > "$inventory"
    cmp -s "$out/checkpoint.files.sha256" "$inventory"
    [[ $(sha256sum "$out/checkpoint.files.sha256" | awk '{print $1}') == "$checkpoint_sha" ]]
}

header=$'arm\treplica\tsimTicks\tfingerprint_sha256\tterminal_sha256\tconfig_sha256\tselector_sha256\tselected_treatment\tsoa_selected\tterminal_completions\tvalue_read_issues\tvalue_read_responses\tvalue_fills\ta_read_issues\ta_read_responses\ta_write_issues\ta_write_responses\tpublish_issues\tpublish_accepts\tpublish_responses\tpublish_terminals\tpublish_overlap'
printf '%s\n' "$header" > "$out/matrix.tsv"
printf 'arm\tselector_path\tselector_sha256\n' > "$out/input/selector.transitions.tsv"

run_restore() {
    local arm=$1 replica=$2 expected_selector_sha=$3
    local name="${arm}_r${replica}"
    local run="$out/runs/$name"
    local -a command=("$gem5" --outdir="$run" "${common[@]}")
    mkdir -p "$run"
    printf '%q ' "${command[@]}" > "$run/command.txt"; printf '\n' >> "$run/command.txt"
    printf '%s\n' "$selector_path" > "$run/selector.path"
    sha256sum "$selector_path" | awk '{print $1}' > "$run/selector.sha256.before"
    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${timeout_command[@]}" "${command[@]}" > "$run/restore.log" 2>&1
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$run/restore.exit"
    sha256sum "$selector_path" | awk '{print $1}' > "$run/selector.sha256.after"
    [[ $(<"$run/selector.sha256.before") == "$expected_selector_sha" ]] || return 97
    [[ $(<"$run/selector.sha256.after") == "$expected_selector_sha" ]] || return 98
    return "$rc"
}

declare -a phase_pids=() phase_names=()
launch_phase() {
    local arm=$1 expected_selector_sha=$2
    phase_pids=(); phase_names=()
    for ((replica = 1; replica <= replicas; replica++)); do
        run_restore "$arm" "$replica" "$expected_selector_sha" &
        phase_pids+=("$!")
        phase_names+=("${arm}_r${replica}")
    done
}

# Always reap every child and preserve every child's terminal markers.  A
# failed early child is remembered but cannot abandon a later sibling.
wait_phase() {
    local index rc
    phase_wait_failed=0
    for ((index = 0; index < ${#phase_pids[@]}; index++)); do
        if wait "${phase_pids[$index]}"; then
            rc=0
        else
            rc=$?
        fi
        printf '%s\n' "$rc" > "$out/runs/${phase_names[$index]}/wait.exit"
        ((rc == 0)) || phase_wait_failed=1
    done
}

validate_run() {
    local arm=$1 replica=$2 expected_selector_sha=$3
    local name="${arm}_r${replica}"
    local run="$out/runs/$name"
    local expected_treatment expected_producer expected_promotable
    if [[ $arm == control ]]; then
        expected_treatment=residual_soa_jit
        expected_producer=cpu_after_spd_completion
        expected_promotable=0
    else
        expected_treatment=residual_soa_jit_response_bearing
        expected_producer=response_bearing_spd_overlap
        expected_promotable=1
    fi
    [[ -s $run/restore.exit && $(<"$run/restore.exit") -eq 0 ]]
    [[ -s $run/wait.exit && $(<"$run/wait.exit") -eq 0 ]]
    [[ $(<"$run/selector.path") == "$selector_path" ]]
    [[ $(<"$run/selector.sha256.before") == "$expected_selector_sha" ]]
    [[ $(<"$run/selector.sha256.after") == "$expected_selector_sha" ]]
    [[ $(grep -Ec '^CG_LOGICAL16_RMW_SELECTION ' "$run/restore.log") -eq 1 ]]
    local selection_line
    selection_line=$(grep '^CG_LOGICAL16_RMW_SELECTION ' "$run/restore.log")
    [[ $selection_line == *"treatment=$expected_treatment "* ]]
    [[ $selection_line == *"producer=$expected_producer "* ]]
    [[ $selection_line == *"performance_promotable=$expected_promotable result=PASS" ]]
    [[ $(grep -Ec '^CG_VIRTUAL_CONSUMER mode=token_stream_ld logical=16384 consumer=4096$' "$run/restore.log") -eq 1 ]]
    [[ $(grep -Ec '^CG_FINGERPRINT .* result=PASS$' "$run/restore.log") -eq 1 ]]
    [[ $(grep -Ec "^CG_LOGICAL16_RMW_TERMINAL treatment=$expected_treatment .*producer=$expected_producer .*performance_promotable=$expected_promotable result=PASS$" "$run/restore.log") -eq 1 ]]
    [[ $(grep -Fxc 'ROI End!!!' "$run/restore.log") -eq 1 ]]
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' "$run/restore.log") -eq 1 ]]
    [[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' "$run/restore.log") -eq 0 ]]
    [[ -s $run/stats.txt ]]
    for resolved in num_tile_elements=16384 physical_tile_elements=4096 \
        num_offset_table_entries=16384 num_offset_table_epoch_entries=16384 \
        soa_jit_predicate_active_credits=16 soa_jit_active_value_owners=32 \
        soa_jit_value_prefetch_credits=0; do grep -Fqx "$resolved" "$run/config.ini"; done

    local instruction terminal soa_selected value_issues value_responses fills
    local a_read_issues a_read_responses a_write_issues a_write_responses
    local publish_issues publish_accepts publish_responses publish_terminals publish_overlap
    local expected_publish terminal_line full_windows verified_index_words
    instruction=$(stat_sum "$run/stats.txt" IND_SoaJitInstructions)
    terminal=$(stat_sum "$run/stats.txt" IND_SoaJitTerminalCompletions)
    soa_selected=$(stat_sum "$run/stats.txt" IND_SoaJitSelected)
    value_issues=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadIssues)
    value_responses=$(stat_sum "$run/stats.txt" IND_SoaJitValueReadResponses)
    fills=$(stat_sum "$run/stats.txt" IND_SoaJitValueFills)
    a_read_issues=$(stat_sum "$run/stats.txt" IND_SoaJitAReadIssues)
    a_read_responses=$(stat_sum "$run/stats.txt" IND_SoaJitAReadResponses)
    a_write_issues=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteIssues)
    a_write_responses=$(stat_sum "$run/stats.txt" IND_SoaJitAWriteResponses)
    # Publisher statistics use gem5's nozero flag, so absence is the encoded
    # zero for the control arm.  Treatment still fails closed because every
    # publisher count is required to equal a positive predicted ledger value.
    publish_issues=$(stat_sum "$run/stats.txt" STR_PublishIssues 1)
    publish_accepts=$(stat_sum "$run/stats.txt" STR_PublishAccepts 1)
    publish_responses=$(stat_sum "$run/stats.txt" STR_PublishWriteResponses 1)
    publish_terminals=$(stat_sum "$run/stats.txt" STR_PublishTerminals 1)
    publish_overlap=$(stat_sum "$run/stats.txt" STR_PublishOverlapIssues 1)
    [[ $instruction -gt 0 && $instruction -eq $terminal && $soa_selected -gt 0 ]]
    [[ $value_issues -eq $value_responses && $value_responses -eq $fills ]]
    [[ $a_read_issues -eq $a_read_responses && $a_read_issues -eq $a_write_issues && $a_write_issues -eq $a_write_responses ]]
    if [[ $arm == treatment ]]; then
        terminal_line=$(grep '^CG_LOGICAL16_RMW_TERMINAL ' "$run/restore.log")
        full_windows=$(sed -n 's/.* full_windows=\([0-9][0-9]*\).*/\1/p' <<<"$terminal_line")
        verified_index_words=$(sed -n 's/.* verified_index_words=\([0-9][0-9]*\).*/\1/p' <<<"$terminal_line")
        [[ $full_windows =~ ^[1-9][0-9]*$ ]]
        [[ $verified_index_words -eq $((full_windows * 16384)) ]]
        expected_publish=$((instruction * 2048))
        [[ $publish_issues -eq $expected_publish ]]
        [[ $publish_accepts -eq $expected_publish ]]
        [[ $publish_responses -eq $expected_publish ]]
        [[ $publish_terminals -eq $((instruction * 8)) ]]
        [[ $publish_overlap -gt 0 ]]
    else
        [[ $publish_issues -eq 0 && $publish_accepts -eq 0 ]]
        [[ $publish_responses -eq 0 && $publish_terminals -eq 0 ]]
        [[ $publish_overlap -eq 0 ]]
    fi

    local tick fingerprint_sha terminal_sha config_sha
    tick=$(awk '$1 == "simTicks" { print $2; exit }' "$run/stats.txt")
    [[ $tick =~ ^[1-9][0-9]*$ ]]
    fingerprint_sha=$(grep '^CG_FINGERPRINT ' "$run/restore.log" | sha256sum | awk '{print $1}')
    terminal_sha=$(grep '^CG_LOGICAL16_RMW_TERMINAL ' "$run/restore.log" | sha256sum | awk '{print $1}')
    config_sha=$(comparable_config_sha "$run/config.ini" "$run")
    {
        printf 'source_commit=%s\nsource_diff_sha256=%s\n' "$source_commit" "$source_diff_sha"
        printf 'gem5_expected_sha256=%s\ngem5_actual_sha256=%s\n' "$expected_gem5_sha" "$actual_gem5_sha"
        printf 'guest_sha256=%s\nselector_path=%s\nselector_sha256=%s\n' "$guest_sha" "$selector_path" "$expected_selector_sha"
        printf 'selected_treatment=%s\ncheckpoint_sha256=%s\n' "$expected_treatment" "$checkpoint_sha"
        printf 'config_sha256=%s\nsimTicks=%s\nfingerprint_sha256=%s\nterminal_sha256=%s\n' "$config_sha" "$tick" "$fingerprint_sha" "$terminal_sha"
    } > "$run/provenance.txt"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$arm" "$replica" "$tick" "$fingerprint_sha" "$terminal_sha" "$config_sha" \
        "$expected_selector_sha" "$expected_treatment" "$soa_selected" "$terminal" \
        "$value_issues" "$value_responses" "$fills" "$a_read_issues" "$a_read_responses" \
        "$a_write_issues" "$a_write_responses" "$publish_issues" "$publish_accepts" \
        "$publish_responses" "$publish_terminals" "$publish_overlap" > "$run/result.tsv"
}

validate_phase() {
    local arm=$1 expected_selector_sha=$2 replica rc run validator_pid
    phase_validation_failed=0
    for ((replica = 1; replica <= replicas; replica++)); do
        run="$out/runs/${arm}_r${replica}"
        (set -e; validate_run "$arm" "$replica" "$expected_selector_sha") > "$run/validation.log" 2>&1 &
        validator_pid=$!
        if wait "$validator_pid"; then
            rc=0
        else
            rc=$?
        fi
        printf '%s\n' "$rc" > "$run/validation.exit"
        if ((rc == 0)); then
            cat "$run/result.tsv" >> "$out/matrix.tsv"
        else
            printf 'validation failed for %s_r%s (rc=%s)\n' "$arm" "$replica" "$rc" >&2
            phase_validation_failed=1
        fi
    done
}

run_phase() {
    local arm=$1 contents=$2 expected_selector_sha=$3
    set_selector_contents "$arm" "$contents" "$expected_selector_sha"
    launch_phase "$arm" "$expected_selector_sha"
    wait_phase
    verify_checkpoint_files "$arm"
    [[ $(sha256sum "$selector_path" | awk '{print $1}') == "$expected_selector_sha" ]]
    validate_phase "$arm" "$expected_selector_sha"
    printf 'wait_failed=%s\nvalidation_failed=%s\nselector_sha256=%s\n' \
        "$phase_wait_failed" "$phase_validation_failed" "$expected_selector_sha" > "$out/${arm}.phase.status"
    ((phase_wait_failed == 0 && phase_validation_failed == 0))
}

run_phase control "$control_contents" "$control_selector_sha"
run_phase treatment "$treatment_contents" "$treatment_selector_sha"
verify_checkpoint_files final

declare -A ticks fingerprints terminals configs selectors treatments soa_selected
declare -A value_issues a_read_issues a_write_issues publish_issues publish_accepts
declare -A publish_responses publish_terminals publish_overlap
while IFS=$'\t' read -r arm replica tick fingerprint terminal config_sha selector_sha treatment soa_select _ value_issue _ _ a_read _ a_write _ publish_issue publish_accept publish_response publish_terminal publish_over; do
    name="${arm}_r${replica}"
    ticks[$name]=$tick; fingerprints[$name]=$fingerprint
    terminals[$name]=$terminal; configs[$name]=$config_sha
    selectors[$name]=$selector_sha; treatments[$name]=$treatment
    soa_selected[$name]=$soa_select; value_issues[$name]=$value_issue
    a_read_issues[$name]=$a_read; a_write_issues[$name]=$a_write
    publish_issues[$name]=$publish_issue; publish_accepts[$name]=$publish_accept
    publish_responses[$name]=$publish_response; publish_terminals[$name]=$publish_terminal
    publish_overlap[$name]=$publish_over
done < <(tail -n +2 "$out/matrix.tsv")

reference_fingerprint=${fingerprints[control_r1]}
reference_config=${configs[control_r1]}
reference_control_terminal=${terminals[control_r1]}
reference_treatment_terminal=${terminals[treatment_r1]}
for ((replica = 1; replica <= replicas; replica++)); do
    for arm in control treatment; do
        name="${arm}_r${replica}"
        [[ ${fingerprints[$name]} == "$reference_fingerprint" ]]
        [[ ${configs[$name]} == "$reference_config" ]]
    done
    control="control_r$replica"; treatment="treatment_r$replica"
    [[ ${treatments[$control]} == residual_soa_jit ]]
    [[ ${treatments[$treatment]} == residual_soa_jit_response_bearing ]]
    [[ ${treatments[$control]} != "${treatments[$treatment]}" ]]
    [[ ${selectors[$control]} == "$control_selector_sha" ]]
    [[ ${selectors[$treatment]} == "$treatment_selector_sha" ]]
    [[ ${terminals[$control]} == "$reference_control_terminal" ]]
    [[ ${terminals[$treatment]} == "$reference_treatment_terminal" ]]
    [[ ${soa_selected[$control]} -eq ${soa_selected[$treatment]} ]]
    [[ ${value_issues[$control]} -eq ${value_issues[$treatment]} ]]
    [[ ${a_read_issues[$control]} -eq ${a_read_issues[$treatment]} ]]
    [[ ${a_write_issues[$control]} -eq ${a_write_issues[$treatment]} ]]
    [[ ${publish_issues[$control]} -eq 0 && ${publish_issues[$treatment]} -gt 0 ]]
    [[ ${publish_accepts[$treatment]} -eq ${publish_issues[$treatment]} ]]
    [[ ${publish_responses[$treatment]} -eq ${publish_issues[$treatment]} ]]
    [[ ${publish_terminals[$treatment]} -gt 0 && ${publish_overlap[$treatment]} -gt 0 ]]
    [[ ${ticks[$treatment]} -le ${ticks[$control]} ]] || {
        echo "response-bearing candidate is slower in replica $replica" >&2
        exit 1
    }
done
{
    printf 'decision=PERFORMANCE_PROMOTABLE\nshared_checkpoint_sha256=%s\n' "$checkpoint_sha"
    printf 'gem5_sha256=%s\nguest_sha256=%s\n' "$actual_gem5_sha" "$guest_sha"
    printf 'control_selector_sha256=%s\ntreatment_selector_sha256=%s\n' "$control_selector_sha" "$treatment_selector_sha"
    printf 'fingerprint_sha256=%s\nconfig_sha256=%s\n' "$reference_fingerprint" "$reference_config"
    for ((replica = 1; replica <= replicas; replica++)); do
        control="control_r$replica"; treatment="treatment_r$replica"
        awk -v r="$replica" -v c="${ticks[$control]}" -v t="${ticks[$treatment]}" \
            'BEGIN { printf "replica_%s_control_simTicks=%s\nreplica_%s_treatment_simTicks=%s\nreplica_%s_speedup=%.9f\n", r,c,r,t,r,c/t }'
    done
} > "$out/decision.txt"
touch "$out/gate.complete"
cat "$out/matrix.tsv"; cat "$out/decision.txt"
echo "PASS CG logical-16 hybrid performance gate out=$out"
