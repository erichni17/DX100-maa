#!/usr/bin/env bash
# Exact candidate-only compact-write-retirement A/B on two small kernels.
# Frozen checkpoints and inputs are read-only; no native or full arm is run.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
source_gem5=$(realpath "${1:?usage: $0 GEM5 OUT}")
out=${2:?usage: $0 GEM5 OUT}
config=$root/configs/deprecated/example/se.py
ramulator=$root/ext/ramulator2/ramulator2/example_gem5_config.yaml
frozen_ramulator=/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/input/libramulator.so

readonly expected_ramulator_sha=76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753
readonly sssp_root=/data1/nier/dx100-runs/2026-08-24-sssp-old-result-small-23e924da-r3
readonly hashjoin_root=/data1/nier/dx100-runs/2026-08-24-hashjoin-hybrid-small-a77f77f1
readonly hashjoin_guest_source=/data1/nier/worktrees/codex-coordination/sessions/hybrid-shared-hotpath-20260824-063057-b94e79a6/evidence/context64-small-ab-r2/inputs/hashjoin
sssp_guest_source=$sssp_root/bin/sssp_maa_2G_old_result_hybrid_fp
sssp_graph_source=$sssp_root/graph/sssp_old_result_hybrid_small.wsg

[[ ! -e $out ]] || { echo "output already exists: $out" >&2; exit 2; }
[[ -x $source_gem5 && -x $sssp_guest_source &&
   -x $hashjoin_guest_source ]] || {
    echo "missing executable input" >&2
    exit 2
}
[[ -z $(git -C "$root" status --porcelain --untracked-files=all) ]] || {
    echo "source worktree is not entirely clean" >&2
    exit 2
}
source_commit=$(git -C "$root" rev-parse HEAD)
source_tree=$(git -C "$root" rev-parse 'HEAD^{tree}')
source_archive_sha=$(git -C "$root" archive --format=tar HEAD | \
    sha256sum | awk '{print $1}')
[[ $(sha256sum "$frozen_ramulator" | awk '{print $1}') == \
    $expected_ramulator_sha ]]
[[ $(sha256sum "$sssp_guest_source" | awk '{print $1}') == \
    b92252492af0fbae8b3a27d2e57d403cbbc2f03b830090ae767f50cac8904c3c ]]
[[ $(sha256sum "$sssp_graph_source" | awk '{print $1}') == \
    3fc71246c10bb765d1f67ac15e9fb30561ca70a89a95f8104f85c91fd2954d23 ]]
[[ $(sha256sum "$hashjoin_guest_source" | awk '{print $1}') == \
    9137ca242beb2b5a451ca592021047dfdf6da5f35efc53f34844c7d87de9f299 ]]

mkdir -p "$out/inputs"
cp -- "$source_gem5" "$out/inputs/gem5.opt"
cp -- "$sssp_guest_source" "$out/inputs/sssp"
cp -- "$sssp_graph_source" "$out/inputs/sssp.wsg"
cp -- "$hashjoin_guest_source" "$out/inputs/hashjoin"
chmod 0555 "$out/inputs/gem5.opt" "$out/inputs/sssp" \
    "$out/inputs/hashjoin"
chmod 0444 "$out/inputs/sssp.wsg"
gem5=$out/inputs/gem5.opt
sssp_guest=$out/inputs/sssp
sssp_graph=$out/inputs/sssp.wsg
hashjoin_guest=$out/inputs/hashjoin
gem5_sha=$(sha256sum "$gem5" | awk '{print $1}')
[[ $gem5_sha == $(sha256sum "$source_gem5" | awk '{print $1}') ]]
export LD_LIBRARY_PATH="$(dirname "$frozen_ramulator"):${LD_LIBRARY_PATH:-}"
resolved_ramulator=$(ldd "$gem5" | awk '$1 == "libramulator.so" {print $3}')
[[ $(realpath "$resolved_ramulator") == $(realpath "$frozen_ramulator") ]]

checkpoint_identity() {
    local checkpoint=$1
    find "$checkpoint" -type f -printf '%P\0' | sort -z |
        while IFS= read -r -d '' relative; do
            sha256sum "$checkpoint/$relative" | sed "s#  $checkpoint/#  #"
        done | sha256sum | awk '{print $1}'
}

first_stat_sum() {
    local stats=$1 suffix=$2
    awk -v suffix="$suffix" '
        /^---------- Begin Simulation Statistics/ { active=1; next }
        /^---------- End Simulation Statistics/ && active {
            if (!seen) exit 2
            printf "%.0f\n", sum
            complete=1
            exit
        }
        active && $1 ~ ("_" suffix "$") { sum += $2; seen=1 }
        END { if (!complete) exit 2 }
    ' "$stats"
}

first_simticks() {
    awk '
        /^---------- Begin Simulation Statistics/ { active=1; next }
        /^---------- End Simulation Statistics/ && active {
            if (!found) exit 2
            print value
            complete=1
            exit
        }
        active && $1 == "simTicks" { value=$2; found++ }
        END { if (!complete || found != 1) exit 2 }
    ' "$1"
}

for checkpoint in "$sssp_root/checkpoint" \
                  "$hashjoin_root/PRO/checkpoint"; do
    [[ -d $checkpoint ]]
done
sssp_checkpoint_before=$(checkpoint_identity "$sssp_root/checkpoint")
hashjoin_checkpoint_before=$(checkpoint_identity "$hashjoin_root/PRO/checkpoint")

common_cache=(
    --sys-clock=3.2GHz --cpu-clock=3.2GHz
    --caches --l1d_size=32kB --l1d_assoc=8 --l1d_mshrs=16
    --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8
    --l1i_mshrs=16 --l1i_write_buffers=8 --l2cache --l2_size=256kB
    --l2_assoc=4 --l2_mshrs=32 --l2_write_buffers=16 --l3cache
    --l3_size=8MB --l3_assoc=16 --l3_mshrs=256
    --l3_write_buffers=128 --l3_ports=4 --cacheline_size=64
    --mem-type=Ramulator2 --ramulator-config="$ramulator" --mem-channels=2
    --maa --maa_num_maas=1 --maa_num_indirect_units_per_maa=4
    --maa_num_tile_elements=16384 --maa_physical_tile_elements=4096
    --maa_num_offset_table_entries=16384
    --maa_num_offset_table_epoch_entries=16384
    --maa_num_initial_row_table_slices=32
    --maa_soa_jit_active_contexts=8
    --maa_soa_jit_active_value_owners=64
    --maa_soa_jit_value_cache_enable
    --maa_soa_jit_pre_a_value_lookahead
)

run_sssp() {
    local arm_name=$1
    shift
    local arm=$out/sssp/$arm_name run=$out/sssp/$arm_name/run
    mkdir -p "$run"
    local command=(
        "$gem5" --listener-mode=off --outdir="$run" "$config"
        --cpu-type=X86O3CPU -r 1 -n 4 --mem-size=2GB
        --checkpoint-dir="$sssp_root/checkpoint"
        "${common_cache[@]}"
        --maa_soa_jit_old_result_pressure_policy=densest
        --maa_soa_jit_old_result_partial_credits=4
        "$@" --cmd="$sssp_guest"
        --options="-f $sssp_graph -n 1 -r 0 -d 1 -v"
    )
    printf '%q ' "${command[@]}" >"$arm/command"
    printf '\n' >>"$arm/command"
    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${command[@]}" \
        >"$run/run.log" 2>"$run/run.err"
    local rc=$?
    set -e
    printf '%s\n' "$rc" >"$arm/gem5.rc"
}

run_hashjoin() {
    local arm_name=$1
    shift
    local arm=$out/hashjoin_pro/$arm_name
    local run=$out/hashjoin_pro/$arm_name/run
    mkdir -p "$run"
    local command=(
        "$gem5" --listener-mode=off --outdir="$run" "$config"
        --cpu-type=X86O3CPU -r 1 -n 4 --mem-size=2GB
        --checkpoint-dir="$hashjoin_root/PRO/checkpoint"
        "${common_cache[@]}"
        --l1d-hwp-type=StridePrefetcher
        --l1i-hwp-type=StridePrefetcher
        --l2-hwp-type=StridePrefetcher
        --maa_ncbus_width=32 --maa_l2_uncacheable --maa_l3_uncacheable
        "$@" --cmd="$hashjoin_guest"
        --options="-a PRO -n 4 -r 65536 -s 65536 -x 12345 -y 54321"
    )
    printf '%q ' "${command[@]}" >"$arm/command"
    printf '\n' >>"$arm/command"
    set +e
    OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${command[@]}" \
        >"$run/run.log" 2>"$run/run.err"
    local rc=$?
    set -e
    printf '%s\n' "$rc" >"$arm/gem5.rc"
}

run_pair() {
    local kernel=$1
    "run_$kernel" baseline &
    local baseline_pid=$!
    "run_$kernel" compact --maa_soa_jit_compact_write_retirement &
    local compact_pid=$!
    local rc=0
    wait "$baseline_pid" || rc=1
    wait "$compact_pid" || rc=1
    return "$rc"
}

run_pair sssp
run_pair hashjoin

field() {
    local line=$1 name=$2
    awk -v name="$name" '{
        for (i=1; i<=NF; ++i) {
            split($i, pair, "=")
            if (pair[1] == name) { print pair[2]; exit }
        }
    }' <<<"$line"
}

validate_common() {
    local kernel=$1 arm_name=$2 enabled=$3
    local arm=$out/$kernel/$arm_name run=$out/$kernel/$arm_name/run
    local log=$run/run.log stats=$run/stats.txt
    [[ $(<"$arm/gem5.rc") == 0 && -s $stats ]]
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
        "$log" || true) -eq 1 ]]
    [[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
        "$log" || true) -eq 0 ]]
    [[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
        "$run/run.err" || true) -eq 0 ]]
    rg -q 'soa_jit_active_contexts=8' "$run/config.ini"
    rg -q "soa_jit_compact_write_retirement=$enabled" "$run/config.ini"
    rg -q 'num_tile_elements=16384' "$run/config.ini"
    rg -q 'physical_tile_elements=4096' "$run/config.ini"
}

validate_compact_accounting() {
    local stats=$1 arm_name=$2 terminals=$3
    local enabled credits hwm stalls bits bytes payload
    enabled=$(first_stat_sum "$stats" IND_SoaJitCompactWriteRetirementEnabled)
    credits=$(first_stat_sum "$stats" IND_SoaJitCompactWriteRetirementCredits)
    hwm=$(first_stat_sum "$stats" IND_SoaJitCompactWriteRetirementCreditHighWater)
    stalls=$(first_stat_sum "$stats" IND_SoaJitCompactWriteRetirementStalls)
    bits=$(first_stat_sum "$stats" IND_SoaJitCompactWriteRetirementPersistentBits)
    bytes=$(first_stat_sum "$stats" IND_SoaJitCompactWriteRetirementPersistentBytes)
    payload=$(first_stat_sum "$stats" \
        IND_SoaJitCompactWriteTransientPayloadHighWaterBytes)
    if [[ $arm_name == baseline ]]; then
        [[ $enabled -eq 0 && $credits -eq 0 && $hwm -eq 0 &&
           $stalls -eq 0 && $bits -eq 0 && $bytes -eq 0 && $payload -eq 0 ]]
    else
        [[ $enabled -eq $terminals ]]
        [[ $credits -eq $((terminals * 8)) ]]
        [[ $bits -eq $((terminals * 1168)) ]]
        [[ $bytes -eq $((terminals * 146)) ]]
        [[ $hwm -gt 0 && $hwm -le $((terminals * 8)) ]]
        [[ $payload -eq $((hwm * 64)) ]]
    fi
}

validate_sssp() {
    local arm_name=$1 enabled=$2 kernel=sssp
    local log=$out/sssp/$arm_name/run/run.log
    local stats=$out/sssp/$arm_name/run/stats.txt
    validate_common "$kernel" "$arm_name" "$enabled"
    [[ $(grep -Fxc 'ROI End!!!' "$log" || true) -eq 1 ]]
    [[ $(grep -Fxc 'SSSP_FINGERPRINT vertices=69633 reached=69633 unreachable=0 distance_sum=135168 max_distance=2 hash_a=a0531a7ddb9387df hash_b=39f1ea63bc8817e8 triangle_violations=0 missing_predecessors=0 nonpositive_weights=0 negative_distances=0 result=PASS' "$log" || true) -eq 1 ]]
    local terminal
    terminal=$(grep '^SSSP_OLD_RESULT_HYBRID_TERMINAL ' "$log")
    [[ $(field "$terminal" eligible_windows) -eq 4 ]]
    [[ $(field "$terminal" routed_windows) -eq 4 ]]
    [[ $(field "$terminal" old_result_words) -eq 65536 ]]
    [[ $(field "$terminal" legacy_words) -eq 0 ]]
    [[ $(field "$terminal" logical_reorder_words) -eq 16384 ]]
    [[ $(field "$terminal" physical_spd_words) -eq 4096 ]]
    [[ $(field "$terminal" host_spd_reads) -eq 0 ]]
    [[ $(field "$terminal" hidden_result_payload_bytes) -eq 0 ]]
    [[ $(field "$terminal" counts_close) -eq 1 ]]
    [[ $(first_stat_sum "$stats" IND_SoaJitInstructions) -eq 4 ]]
    [[ $(first_stat_sum "$stats" IND_SoaJitTerminalCompletions) -eq 4 ]]
    [[ $(first_stat_sum "$stats" IND_SoaJitSelected) -eq 65536 ]]
    [[ $(first_stat_sum "$stats" IND_SoaJitOldResultCaptures) -eq 65536 ]]
    [[ $(first_stat_sum "$stats" IND_SoaJitOldResultWriteIssues) -eq \
       $(first_stat_sum "$stats" IND_SoaJitOldResultWriteResponses) ]]
    [[ $(first_stat_sum "$stats" IND_SoaJitAReadIssues) -eq \
       $(first_stat_sum "$stats" IND_SoaJitAReadResponses) ]]
    [[ $(first_stat_sum "$stats" IND_SoaJitAWriteIssues) -eq \
       $(first_stat_sum "$stats" IND_SoaJitAWriteResponses) ]]
    [[ $(first_stat_sum "$stats" IND_SoaJitPreAValueIssues) -eq \
       $(first_stat_sum "$stats" IND_SoaJitPreAValueUses) ]]
    validate_compact_accounting "$stats" "$arm_name" 4
}

validate_hashjoin() {
    local arm_name=$1 enabled=$2 kernel=hashjoin_pro
    local log=$out/hashjoin_pro/$arm_name/run/run.log
    local stats=$out/hashjoin_pro/$arm_name/run/stats.txt
    validate_common "$kernel" "$arm_name" "$enabled"
    [[ $(grep -Fxc 'HASHJOIN_HYBRID_RESULT result=65536' "$log" || true) -eq 1 ]]
    local marker
    marker=$(grep '^HASHJOIN_HYBRID_SOA_JIT ' "$log")
    [[ $(field "$marker" first_eligible) -eq 8 ]]
    [[ $(field "$marker" first_routed) -eq 8 ]]
    [[ $(field "$marker" eligible) -eq 8 ]]
    [[ $(field "$marker" routed) -eq 8 ]]
    [[ $(field "$marker" physical_spd_elements) -eq 4096 ]]
    [[ $(field "$marker" logical_reorder_elements) -eq 16384 ]]
    [[ $(first_stat_sum "$stats" IND_SoaJitInstructions) -eq 8 ]]
    [[ $(first_stat_sum "$stats" IND_SoaJitTerminalCompletions) -eq 8 ]]
    [[ $(first_stat_sum "$stats" IND_SoaJitSelected) -eq 131072 ]]
    [[ $(first_stat_sum "$stats" IND_SoaJitAliasesApplied) -eq 131072 ]]
    [[ $(first_stat_sum "$stats" IND_SoaJitAReadIssues) -eq \
       $(first_stat_sum "$stats" IND_SoaJitAReadResponses) ]]
    [[ $(first_stat_sum "$stats" IND_SoaJitAWriteIssues) -eq \
       $(first_stat_sum "$stats" IND_SoaJitAWriteResponses) ]]
    [[ $(first_stat_sum "$stats" IND_BoundedGlobalMergeFallbacks) -eq 0 ]]
    validate_compact_accounting "$stats" "$arm_name" 8
}

printf 'kernel\tarm\tsimTicks\tcontext_stalls\tcompact_stalls\tcompact_credit_hwm\ta_reads\ta_writes\told_result_writes\n' >"$out/results.tsv"
for kernel in sssp hashjoin_pro; do
    for arm_name in baseline compact; do
        enabled=false
        [[ $arm_name == compact ]] && enabled=true
        if [[ $kernel == sssp ]]; then
            validate_sssp "$arm_name" "$enabled"
        else
            validate_hashjoin "$arm_name" "$enabled"
        fi
        stats=$out/$kernel/$arm_name/run/stats.txt
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$kernel" "$arm_name" "$(first_simticks "$stats")" \
            "$(first_stat_sum "$stats" IND_SoaJitContextStalls)" \
            "$(first_stat_sum "$stats" IND_SoaJitCompactWriteRetirementStalls)" \
            "$(first_stat_sum "$stats" IND_SoaJitCompactWriteRetirementCreditHighWater)" \
            "$(first_stat_sum "$stats" IND_SoaJitAReadIssues)" \
            "$(first_stat_sum "$stats" IND_SoaJitAWriteIssues)" \
            "$(first_stat_sum "$stats" IND_SoaJitOldResultWriteIssues)" \
            >>"$out/results.tsv"
    done
done

decision=accept
reason=both_nonregressing_and_one_improves_at_least_0_5_pct
meaningful=0
for kernel in sssp hashjoin_pro; do
    baseline=$(awk -v k="$kernel" '$1 == k && $2 == "baseline" {print $3}' \
        "$out/results.tsv")
    compact=$(awk -v k="$kernel" '$1 == k && $2 == "compact" {print $3}' \
        "$out/results.tsv")
    if [[ $compact -gt $baseline ]]; then
        decision=reject
        reason=at_least_one_kernel_regressed
    fi
    if [[ $((1000 * (baseline - compact))) -ge $((5 * baseline)) ]]; then
        meaningful=1
    fi
done
if [[ $decision == accept && $meaningful -ne 1 ]]; then
    decision=reject
    reason=no_kernel_improved_at_least_0_5_pct
fi

sssp_checkpoint_after=$(checkpoint_identity "$sssp_root/checkpoint")
hashjoin_checkpoint_after=$(checkpoint_identity "$hashjoin_root/PRO/checkpoint")
[[ $sssp_checkpoint_after == "$sssp_checkpoint_before" ]]
[[ $hashjoin_checkpoint_after == "$hashjoin_checkpoint_before" ]]

{
    printf 'schema=dx100.hybrid_compact_write_retirement_ab.v1\n'
    printf 'candidate=fixed_8_credit_compact_a_write_retirement\n'
    printf 'candidate_only=1\nnative_reruns=0\nfull_run_roots_touched=0\n'
    printf 'source_commit=%s\nsource_tree=%s\n' \
        "$source_commit" "$source_tree"
    printf 'source_archive_sha256=%s\n' "$source_archive_sha"
    printf 'offline_dependency_source=%s\n' \
        '/data1/nier/worktrees/DX100-virtualization-line-handoff-20260812'
    printf 'ramulator_argparse_gitlink=997da9255618311d1fcb0135ce86022729d1f1cb\n'
    printf 'ramulator_argparse_directory_sha256=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n'
    printf 'ramulator_spdlog_gitlink=ad0e89cbfb4d0c1ce4d097e134eb7be67baebb36\n'
    printf 'ramulator_spdlog_directory_sha256=f2cef6ed58f83957b8b71aa11a0bf2e307c666b8bfd6a5ebb098cc40c030a3d8\n'
    printf 'ramulator_yaml_cpp_gitlink=0579ae3d976091d7d664aa9d2527e0d0cff25763\n'
    printf 'ramulator_yaml_cpp_directory_sha256=2b978d137ff52e3b8595f751afd97479e0062bcc9ad60cda57e06905b21d2823\n'
    printf 'util_m5op_s_sha256=fe20d70d689c341ee614121d7aac1431b81d2178943a113ea1aa1d7c5ef50c69\n'
    printf 'gem5_path=%s\ngem5_sha256=%s\n' "$gem5" "$gem5_sha"
    printf 'ramulator_library_path=%s\nramulator_library_sha256=%s\n' \
        "$frozen_ramulator" "$expected_ramulator_sha"
    printf 'sssp_checkpoint=%s\nsssp_checkpoint_sha256=%s\n' \
        "$sssp_root/checkpoint" "$sssp_checkpoint_before"
    printf 'hashjoin_checkpoint=%s\nhashjoin_checkpoint_sha256=%s\n' \
        "$hashjoin_root/PRO/checkpoint" "$hashjoin_checkpoint_before"
    printf 'logical_elements=16384\nphysical_spd_elements=4096\n'
    printf 'active_contexts=8\nactive_value_owners=64\n'
    printf 'value_cache=true\npre_a=true\n'
    printf 'baseline_compact_write_retirement=false\n'
    printf 'treatment_compact_write_retirement=true\n'
    printf 'persistent_tracker_bits_per_indirect_unit=1168\n'
    printf 'persistent_tracker_bytes_per_indirect_unit=146\n'
    printf 'persistent_tracker_bytes_four_units=584\n'
    printf 'transient_response_credit_tag_bits_per_packet=3\n'
    printf 'max_transient_response_credit_tag_bits_per_indirect_unit=24\n'
    printf 'max_transient_response_credit_tag_bytes_per_indirect_unit=3\n'
    printf 'max_transient_packet_payload_bytes_per_indirect_unit=512\n'
    printf 'sender_state_mapping=credit_tag_indexes_persistent_tracker\n'
    printf 'sender_state_duplicate_fields=generation_sequence_address_are_tracker_validation_metadata\n'
    printf 'wall_timeout=none\nperformance_metric=first_simTicks\n'
    printf 'meaningful_improvement_threshold_pct=0.5\n'
} >"$out/manifest.txt"
printf 'decision=%s\nreason=%s\n' "$decision" "$reason" >"$out/decision.txt"
printf 'terminal=pass\ndecision=%s\n' "$decision" >"$out/gate.complete"
find "$out" -type f ! -name hashes.sha256 -print0 | sort -z | \
    xargs -0 sha256sum >"$out/hashes.sha256"
cat "$out/results.tsv"
echo "HYBRID_COMPACT_WRITE_RETIREMENT_AB_${decision^^} out=$out"
