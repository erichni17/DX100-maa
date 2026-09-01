#!/usr/bin/env bash
set -euo pipefail

# A single-replica, exactly-three-arm SSSP locality screen.  The runner is
# copied into the evidence root before it does any work so an active campaign
# cannot observe later source-tree edits.
if [[ ${SSSP_LOCALITY_FROZEN_RUNNER:-0} != 1 ]]; then
    if [[ $# -ne 1 ]]; then
        echo "usage: $0 OUTDIR" >&2
        exit 2
    fi
    runner_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
    requested_out=$(realpath -m "$1")
    [[ ! -e $requested_out ]] || {
        echo "refusing existing output: $requested_out" >&2
        exit 2
    }
    mkdir -p "$requested_out/provenance"
    frozen_runner="$requested_out/provenance/run_sssp_locality_matched_micro.frozen.sh"
    cp -- "${BASH_SOURCE[0]}" "$frozen_runner"
    chmod 0555 "$frozen_runner"
    exec env SSSP_LOCALITY_FROZEN_RUNNER=1 \
        SSSP_LOCALITY_RUNNER_ROOT="$runner_root" \
        "$frozen_runner" "$requested_out"
fi

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTDIR" >&2
    exit 2
fi

root=$(realpath "${SSSP_LOCALITY_RUNNER_ROOT:?missing frozen runner root}")
out=$(realpath "$1")
gem5=/data1/nier/worktrees/DX100-virtualization-selected-integration-cont-20260826/build/X86/gem5.opt
gem5_sha256=45206b3433449e10b26bbd8ff32281c06e533c101213097a27d50c364ca3c267
frozen_ramulator=/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/input/libramulator.so
frozen_ramulator_sha256=76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
source_file="$root/benchmarks/gapbs/src/sssp.cc"
helper_file="$root/benchmarks/gapbs/src/sssp_coherent_fallback.hh"
admission_file="$root/benchmarks/gapbs/src/sssp_chunk_admission.hh"
fingerprint='SSSP_FINGERPRINT vertices=69633 reached=69633 unreachable=0 distance_sum=135168 max_distance=2 hash_a=a0531a7ddb9387df hash_b=39f1ea63bc8817e8 triangle_violations=0 missing_predecessors=0 nonpositive_weights=0 negative_distances=0 result=PASS'
arms=(native4 native16 hybrid)
campaign_status=FAIL
postprocess_only=${SSSP_LOCALITY_POSTPROCESS_ONLY:-0}
converter="$out/bin/converter"
wel="$out/graph/sssp_locality_matched.wel"
graph="$out/graph/sssp_locality_matched.wsg"

hash_value() {
    sha256sum "$1" | awk '{ print $1 }'
}

require_hash() {
    local path=$1 expected=$2
    [[ -f $path && $(hash_value "$path") == "$expected" ]]
}

write_terminal_state() {
    local rc=$1
    printf '{"terminal":true,"status":"%s","driver_rc":%s}\n' \
        "$campaign_status" "$rc" >"$out/terminal.json"
}

on_exit() {
    local rc=$?
    write_terminal_state "$rc"
}
trap on_exit EXIT

stat_sum() {
    local stats=$1 suffix=$2
    awk -v suffix="$suffix" '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 &&
            ($1 == "system.maa." suffix || $1 ~ ("_" suffix "$")) {
            sum += $2
            found++
        }
        /^---------- End Simulation Statistics/ && section == 1 {
            printf "%.0f\n", found ? sum : 0
            exit
        }
    ' "$stats"
}

stat_exact() {
    local stats=$1 name=$2
    awk -v name="$name" '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 == name { value=$2; found++ }
        /^---------- End Simulation Statistics/ && section == 1 {
            if (found != 1)
                exit 2
            printf "%.0f\n", value
            exit
        }
    ' "$stats"
}

terminal_value() {
    local line=$1 key=$2 token
    for token in $line; do
        if [[ $token == "$key="* ]]; then
            printf '%s\n' "${token#*=}"
            return 0
        fi
    done
    return 1
}

dram_value() {
    local log=$1 command=$2
    awk -v command="$command" '
        BEGIN {
            value[0] = 0
            value[1] = 0
        }
        $1 ~ ("^CH[01]_num_" command "_commands_T:$") {
            channel=$1
            sub(/^CH/, "", channel)
            sub(/_num_.*/, "", channel)
            value[channel]=$2
            found[channel]=1
        }
        END {
            # Ramulator suppresses a zero command counter.  WR is absent on
            # all three completed arms and is therefore an exact zero, while
            # the locality-bearing RD/ACT/PRE counters must exist per channel.
            if (command != "WR" && (!(0 in found) || !(1 in found)))
                exit 2
            print value[0] + value[1]
        }
    ' "$log"
}

hash_checkpoint() {
    local checkpoint=$1 destination=$2
    (
        cd "$checkpoint"
        find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
    ) >"$destination"
}

verify_checkpoint() {
    local checkpoint=$1 manifest=$2
    (cd "$checkpoint" && sha256sum -c "$manifest") >/dev/null
}

run_recorded() {
    local identity=$1 log=$2
    shift 2
    printf '%q ' "$@" >"$identity.command"
    printf '\n' >>"$identity.command"
    "$@" >"$log" 2>&1 &
    local pid=$!
    local start_ticks
    start_ticks=$(awk '{ print $22 }' "/proc/$pid/stat")
    printf 'pid=%s\nproc_start_ticks=%s\n' "$pid" "$start_ticks" \
        >"$identity.process"
    set +e
    wait "$pid"
    local rc=$?
    set -e
    printf 'return_code=%s\n' "$rc" >>"$identity.process"
    [[ $rc -eq 0 ]]
}

if [[ $postprocess_only == 1 ]]; then
    [[ -s $out/artifacts.before.sha256 ]] || {
        echo "missing frozen artifact manifest for postprocessing" >&2
        exit 2
    }
    sha256sum -c "$out/artifacts.before.sha256" >/dev/null
    if [[ ! -f $out/postprocess.recovery.txt ]]; then
        previous_terminal=$(tr -d '\n' <"$out/terminal.json")
        {
            printf 'schema=dx100.sssp.locality_matched_micro.postprocess_recovery.v1\n'
            printf 'reason=ramulator_zero_WR_counter_omitted\n'
            printf 'previous_terminal=%s\n' "$previous_terminal"
            printf 'gem5_reruns=0\ncheckpoint_reruns=0\n'
        } >"$out/postprocess.recovery.txt"
    fi
    {
        printf 'postprocessor_path=%s\npostprocessor_sha256=%s\n' \
            "$0" "$(hash_value "$0")"
        printf 'gem5_reruns=0\ncheckpoint_reruns=0\n'
    } >"$out/postprocess.latest.txt"
    printf '{"terminal":false,"status":"POSTPROCESSING","driver_rc":null}\n' \
        >"$out/terminal.json"
else
require_hash "$gem5" "$gem5_sha256" || {
    echo "missing or mismatched accepted all-safe gem5 binary" >&2
    exit 2
}
require_hash "$frozen_ramulator" "$frozen_ramulator_sha256" || {
    echo "missing or mismatched frozen Ramulator library" >&2
    exit 2
}
[[ -z $(git -C "$root" status --short) ]] || {
    echo "refusing evidence run from a dirty source tree" >&2
    exit 1
}
export LD_LIBRARY_PATH="$(dirname "$frozen_ramulator"):${LD_LIBRARY_PATH:-}"
ldd "$gem5" >"$out/provenance/gem5.ldd.txt"
resolved_ramulator=$(awk '$1 == "libramulator.so" { print $3 }' \
    "$out/provenance/gem5.ldd.txt")
[[ -n $resolved_ramulator &&
   $(realpath "$resolved_ramulator") == $(realpath "$frozen_ramulator") ]] || {
    echo "gem5 does not resolve the frozen Ramulator library" >&2
    exit 2
}

printf '{"terminal":false,"status":"RUNNING","driver_rc":null}\n' \
    >"$out/terminal.json"
printf 'pid=%s\nproc_start_ticks=%s\n' "$$" \
    "$(awk '{ print $22 }' "/proc/$$/stat")" >"$out/driver.process"
mkdir -p "$out/bin" "$out/graph" "$out/arms"
"${CXX:-g++}" -I"$root/benchmarks/gapbs/src" -std=c++11 -O3 \
    -Wall -Wextra -Werror -Wno-unused-parameter -fopenmp \
    "$root/benchmarks/gapbs/src/converter.cc" -o "$converter"

compile_guest() {
    local arm=$1 tile=$2 consumer=$3
    local guest="$out/bin/sssp_${arm}_fp"
    local flags=(
        -I"$root/benchmarks/gapbs/src" -I"$root/benchmarks/API"
        -I"$root/include" -I"$root/util/m5/src"
        -std=c++11 -O3 -Wall -Wextra -Werror
        -Wno-ignored-qualifiers -Wno-unused-parameter -fopenmp
        -DGEM5 -DMAA -DNUM_CORES=4 -DNUM_TILES_PER_CORE=8
        -DTILE_SIZE="$tile" -DMAA_CONSUMER_TILE_SIZE="$consumer"
        -DMAA_MEM_SIZE=0x80000000 -DSSSP_FP_ENABLE=1
    )
    if [[ $arm == hybrid ]]; then
        flags+=(-DSSSP_OLD_RESULT_HYBRID=1)
    fi
    "${CXX:-g++}" "${flags[@]}" "$root/util/m5/src/abi/x86/m5op.S" \
        "$source_file" -o "$guest"
    chmod 0555 "$guest"
}

compile_guest native4 4096 4096
compile_guest native16 16384 16384
compile_guest hybrid 16384 4096

# Directed two-level graph.  The 65,536 second-level edge ordinals are mapped
# by p(e)=16*(e mod 4096)+floor(e/4096).  This is a global bijection.  A 4K
# edge tile therefore presents one destination word in each of 4,096 cache
# lines, whereas a 16K edge tile presents four words in those same lines.
for ((u = 1; u <= 4096; ++u)); do
    printf '0 %d 1\n' "$u"
done >"$wel"
for ((u = 1; u <= 4096; ++u)); do
    for ((lane = 0; lane < 16; ++lane)); do
        edge=$(((u - 1) * 16 + lane))
        permuted=$((16 * (edge % 4096) + edge / 4096))
        destination=$((4097 + permuted))
        printf '%d %d 1\n' "$u" "$destination"
    done
done >>"$wel"
"$converter" -f "$wel" -w -b "$graph" \
    >"$out/graph/converter.log" 2>&1
chmod 0444 "$wel" "$graph"

python3 - "$wel" <<'PY'
import sys

path = sys.argv[1]
leaves = []
with open(path, encoding="utf-8") as stream:
    for line_number, line in enumerate(stream):
        source, destination, weight = map(int, line.split())
        if line_number < 4096:
            assert source == 0 and destination == line_number + 1 and weight == 1
        else:
            leaves.append(destination)
assert len(leaves) == 65536
assert sorted(leaves) == list(range(4097, 69633))
for tile in range(16):
    words = leaves[tile * 4096:(tile + 1) * 4096]
    assert len({(word - 4097) // 16 for word in words}) == 4096
for tile in range(4):
    words = leaves[tile * 16384:(tile + 1) * 16384]
    lines = [(word - 4097) // 16 for word in words]
    assert len(set(lines)) == 4096
    assert all(lines.count(line) == 4 for line in set(lines))
PY

cat >"$out/prediction.txt" <<'PREDICTION'
prediction_recorded_before_any_checkpoint_or_restore=true
replicas=1
arms=native4,native16,hybrid
semantic_graph_edges=69632
semantic_leaf_relaxations=65536
semantic_logical_windows_16k=4
native4_expected_final_rmw_windows=16
native16_expected_final_rmw_windows=4
hybrid_expected_routed_windows=4
hybrid_expected_soa_instructions=4
hybrid_expected_selected_words=65536
hybrid_expected_predicate_rejections=0
hybrid_expected_old_result_captures=65536
hybrid_expected_publish_pages=32
hybrid_expected_publish_words=131072
prediction_native16_vs_native4=lower_cache_line_and_row_routing_and_lower_dram_act_pre
prediction_hybrid_vs_native4=retain_16k_routing_locality_but_add_publish_and_old_result_writes
prediction_hybrid_write_closure=all_A_old_result_and_publish_issues_equal_responses
promotion_screen=all_exact_correctness_and_closure_plus_native16_and_hybrid_each_at_least_1.05x_native4_and_expected_routing_direction
PREDICTION
printf 'prediction_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    >>"$out/prediction.txt"

options="-f $graph -n 1 -r 0 -d 1 -v"
source_commit=$(git -C "$root" rev-parse HEAD)
{
    printf 'schema=dx100.sssp.locality_matched_micro.v1\n'
    printf 'source_commit=%s\n' "$source_commit"
    printf 'replicas=1\narms=native4,native16,hybrid\n'
    printf 'full_app_runs=0\nexternal_native_baseline_reruns=0\n'
    printf 'wall_timeout=none\nsimulated_metric=simTicks\n'
    printf 'graph_permutation=p(e)=16*(e%%4096)+floor(e/4096)\n'
    printf 'graph_vertices=69633\ngraph_edges=69632\nleaf_edges=65536\n'
    printf 'graph_sha256=%s\nwel_sha256=%s\n' \
        "$(hash_value "$graph")" "$(hash_value "$wel")"
    printf 'expected_fingerprint=%s\n' "$fingerprint"
    printf 'gem5_path=%s\ngem5_sha256=%s\n' "$gem5" "$gem5_sha256"
    printf 'ramulator_library_path=%s\nramulator_library_sha256=%s\n' \
        "$frozen_ramulator" "$frozen_ramulator_sha256"
    printf 'config_sha256=%s\nramulator_config_sha256=%s\n' \
        "$(hash_value "$config")" "$(hash_value "$ramulator")"
    printf 'source_sha256=%s\nhelper_sha256=%s\nadmission_sha256=%s\n' \
        "$(hash_value "$source_file")" "$(hash_value "$helper_file")" \
        "$(hash_value "$admission_file")"
    printf 'native4_guest_sha256=%s\n' "$(hash_value "$out/bin/sssp_native4_fp")"
    printf 'native16_guest_sha256=%s\n' "$(hash_value "$out/bin/sssp_native16_fp")"
    printf 'hybrid_guest_sha256=%s\n' "$(hash_value "$out/bin/sssp_hybrid_fp")"
    printf 'cache_surface=l1d32k_8way_stride_l1i32k_8way_stride_l2_256k_4way_stride_l3_8m_16way_4port_cl64\n'
    printf 'memory_surface=Ramulator2_2channels\n'
    printf 'row_table_slices=32\nindirect_units=4\nnum_tiles_per_core=8\n'
} >"$out/manifest.txt"

sha256sum "$gem5" "$frozen_ramulator" "$config" "$ramulator" \
    "$source_file" "$helper_file" "$admission_file" \
    "$out/provenance/run_sssp_locality_matched_micro.frozen.sh" \
    "$out/bin/converter" "$out/bin/sssp_native4_fp" \
    "$out/bin/sssp_native16_fp" "$out/bin/sssp_hybrid_fp" \
    "$wel" "$graph" "$out/prediction.txt" >"$out/artifacts.before.sha256"

arm_tile() {
    case "$1" in
        native4) printf '4096\n' ;;
        native16|hybrid) printf '16384\n' ;;
        *) return 2 ;;
    esac
}

arm_physical() {
    case "$1" in
        native4|hybrid) printf '4096\n' ;;
        native16) printf '16384\n' ;;
        *) return 2 ;;
    esac
}

for arm in "${arms[@]}"; do
    arm_root="$out/arms/$arm/replica-1"
    checkpoint="$arm_root/checkpoint"
    run="$arm_root/run"
    mkdir -p "$checkpoint" "$run"
    guest="$out/bin/sssp_${arm}_fp"
    tile=$(arm_tile "$arm")
    physical=$(arm_physical "$arm")
    checkpoint_cmd=(
        "$gem5" --listener-mode=off --outdir="$checkpoint" "$config"
        --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1
        --cmd "$guest" --options "$options"
    )
    printf '%s\n' "$arm checkpoint" >"$out/current_arm.txt"
    run_recorded "$arm_root/checkpoint" "$arm_root/checkpoint.log" \
        env OMP_PROC_BIND=false OMP_NUM_THREADS=4 "${checkpoint_cmd[@]}"
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
              "$arm_root/checkpoint.log" || true) -eq 1 ]]
    checkpoint_manifest="$arm_root/checkpoint.files.sha256"
    hash_checkpoint "$checkpoint" "$checkpoint_manifest"
    checkpoint_identity=$(hash_value "$checkpoint_manifest")
    printf 'checkpoint_identity_sha256=%s\n' "$checkpoint_identity" \
        >"$arm_root/checkpoint.identity"
    verify_checkpoint "$checkpoint" "$checkpoint_manifest"

    restore_cmd=(
        "$gem5" --listener-mode=off --outdir="$run" "$config"
        --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
        --checkpoint-dir="$checkpoint"
        --sys-clock 3.2GHz --cpu-clock 3.2GHz
        --caches --l1d_size=32kB --l1d_assoc=8
        --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16
        --l1d_write_buffers=8 --l1i_size=32kB --l1i_assoc=8
        --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16
        --l1i_write_buffers=8 --l2cache --l2_size=256kB --l2_assoc=4
        --l2-hwp-type=StridePrefetcher --l2_mshrs=32
        --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
        --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
        --cacheline_size=64 --mem-type Ramulator2
        --ramulator-config "$ramulator" --mem-channels=2
        --maa_ncbus_width=32 --maa --maa_num_maas=1
        --maa_num_indirect_units_per_maa=4
        --maa_num_tile_elements="$tile"
        --maa_physical_tile_elements="$physical"
        --maa_num_offset_table_entries="$tile"
        --maa_num_offset_table_epoch_entries="$tile"
        --maa_num_initial_row_table_slices=32
        --maa_l2_uncacheable --maa_l3_uncacheable
        --maa_soa_jit_old_result_pressure_policy=densest
        --maa_soa_jit_old_result_partial_credits=4
        --maa_soa_jit_predicate_active_credits=1
        --maa_soa_jit_active_contexts=8
        --maa_soa_jit_value_lookahead=1
        --maa_soa_jit_value_cache_enable
        --maa_soa_jit_pre_a_value_lookahead
        --maa_soa_jit_value_prefetch_credits=0
        --maa_soa_jit_active_value_owners=64
        --maa_soa_jit_apply_lanes=1
        --cmd "$guest" --options "$options"
    )
    printf '%s\n' "$arm restore" >"$out/current_arm.txt"
    verify_checkpoint "$checkpoint" "$checkpoint_manifest"
    run_recorded "$arm_root/restore" "$run/restore.log" \
        env OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
        LD_LIBRARY_PATH="$LD_LIBRARY_PATH" "${restore_cmd[@]}"
    verify_checkpoint "$checkpoint" "$checkpoint_manifest"
done
fi

printf 'arm\tsimTicks\tspeedup_vs_native4\tmaa_cycles\tcache_lines\tunique_cache_lines\trows\tunique_rows\tfill_cycles\trequest_cycles\tdram_reads\tdram_writes\tdram_activates\tdram_precharges\tsoa_instructions\tsoa_selected\tsoa_rejected\tsoa_captures\tsoa_a_read_issues\tsoa_a_read_responses\tsoa_a_write_issues\tsoa_a_write_responses\tsoa_old_write_issues\tsoa_old_write_responses\tpublish_issues\tpublish_responses\tpublish_terminals\tcheckpoint_identity\tcorrect\n' \
    >"$out/results.tsv"

declare -A ticks cache_lines rows dram_activates
for arm in "${arms[@]}"; do
    arm_root="$out/arms/$arm/replica-1"
    restore="$arm_root/run/restore.log"
    stats="$arm_root/run/stats.txt"
    [[ -s $stats ]]
    [[ $(grep -Fxc "$fingerprint" "$restore" || true) -eq 1 ]]
    [[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
              "$restore" || true) -eq 1 ]]
    [[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' \
              "$restore" || true) -eq 0 ]]
    [[ $(grep -Fxc 'Starting DeltaStepMAA: 4096 elements (maa-1024)' \
              "$restore" || true) -eq 1 ]]

    ticks[$arm]=$(stat_exact "$stats" simTicks)
    maa_cycles=$(stat_exact "$stats" system.maa.cycles_TOTAL)
    cache_lines[$arm]=$(stat_sum "$stats" IND_NumCacheLineInserted)
    unique_cache_lines=$(stat_sum "$stats" IND_NumUniqueCacheLineInserted)
    rows[$arm]=$(stat_sum "$stats" IND_NumRowsInserted)
    unique_rows=$(stat_sum "$stats" IND_NumUniqueRowsInserted)
    fill_cycles=$(stat_sum "$stats" IND_CyclesFill)
    request_cycles=$(stat_sum "$stats" IND_CyclesRequest)
    dram_reads=$(dram_value "$restore" RD)
    dram_writes=$(dram_value "$restore" WR)
    dram_activates[$arm]=$(dram_value "$restore" ACT)
    dram_precharges=$(dram_value "$restore" PRE)
    soa_instructions=$(stat_sum "$stats" IND_SoaJitInstructions)
    soa_selected=$(stat_sum "$stats" IND_SoaJitSelected)
    soa_rejected=$(stat_sum "$stats" IND_SoaJitPredicateRejected)
    soa_captures=$(stat_sum "$stats" IND_SoaJitOldResultCaptures)
    soa_a_read_issues=$(stat_sum "$stats" IND_SoaJitAReadIssues)
    soa_a_read_responses=$(stat_sum "$stats" IND_SoaJitAReadResponses)
    soa_a_write_issues=$(stat_sum "$stats" IND_SoaJitAWriteIssues)
    soa_a_write_responses=$(stat_sum "$stats" IND_SoaJitAWriteResponses)
    soa_old_write_issues=$(stat_sum "$stats" IND_SoaJitOldResultWriteIssues)
    soa_old_write_responses=$(stat_sum "$stats" IND_SoaJitOldResultWriteResponses)
    publish_issues=$(stat_sum "$stats" STR_PublishIssues)
    publish_responses=$(stat_sum "$stats" STR_PublishWriteResponses)
    publish_terminals=$(stat_sum "$stats" STR_PublishTerminals)
    checkpoint_identity=$(awk -F= '$1 == "checkpoint_identity_sha256" { print $2 }' \
        "$arm_root/checkpoint.identity")
    [[ ${ticks[$arm]} =~ ^[1-9][0-9]*$ && $maa_cycles =~ ^[1-9][0-9]*$ ]]

    if [[ $arm == hybrid ]]; then
        terminal=$(grep '^SSSP_OLD_RESULT_HYBRID_TERMINAL ' "$restore")
        [[ $(grep -c '^SSSP_OLD_RESULT_HYBRID_TERMINAL ' "$restore") -eq 1 ]]
        for expected in eligible_windows=4 routed_windows=4 \
            unsafe_eligible_windows=0 bounds_rejected_windows=0 \
            active_source_rejected_windows=0 cross_owner_rejected_windows=0 \
            index_publish_pages=16 value_publish_pages=16 \
            old_result_words=65536 legacy_words=0 fallback_pages=0 \
            logical_reorder_words=16384 physical_spd_words=4096 \
            host_spd_reads=0 illegal_host_spd_line_starts=0 \
            new_dedicated_payload_bytes=0 hidden_logical_spd_bytes=0 \
            hidden_result_payload_bytes=0 response_closure=1 counts_close=1; do
            [[ $(terminal_value "$terminal" "${expected%%=*}") == \
                "${expected#*=}" ]]
        done
        [[ $soa_instructions -eq 4 && $soa_selected -eq 65536 && \
           $soa_rejected -eq 0 && $soa_captures -eq 65536 ]]
        [[ $soa_a_read_issues -gt 0 &&
           $soa_a_read_issues -eq $soa_a_read_responses &&
           $soa_a_read_issues -eq $soa_a_write_issues &&
           $soa_a_write_issues -eq $soa_a_write_responses ]]
        [[ $soa_old_write_issues -gt 0 &&
           $soa_old_write_issues -eq $soa_old_write_responses ]]
        [[ $publish_issues -eq 8192 && $publish_responses -eq 8192 &&
           $publish_terminals -eq 32 ]]
    else
        [[ $soa_instructions -eq 0 && $soa_selected -eq 0 &&
           $soa_captures -eq 0 && $publish_issues -eq 0 &&
           $publish_responses -eq 0 && $publish_terminals -eq 0 ]]
    fi

    printf '%s\t%s\tPENDING\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\ttrue\n' \
        "$arm" "${ticks[$arm]}" "$maa_cycles" "${cache_lines[$arm]}" \
        "$unique_cache_lines" "${rows[$arm]}" "$unique_rows" \
        "$fill_cycles" "$request_cycles" "$dram_reads" "$dram_writes" \
        "${dram_activates[$arm]}" "$dram_precharges" "$soa_instructions" \
        "$soa_selected" "$soa_rejected" "$soa_captures" \
        "$soa_a_read_issues" "$soa_a_read_responses" "$soa_a_write_issues" \
        "$soa_a_write_responses" "$soa_old_write_issues" \
        "$soa_old_write_responses" "$publish_issues" "$publish_responses" \
        "$publish_terminals" "$checkpoint_identity" >>"$out/results.tsv"
done

python3 - "$out/results.tsv" <<'PY'
import csv
import sys
from pathlib import Path

path = Path(sys.argv[1])
with path.open(newline="", encoding="utf-8") as stream:
    rows = list(csv.DictReader(stream, delimiter="\t"))
native4 = int(next(row["simTicks"] for row in rows if row["arm"] == "native4"))
for row in rows:
    row["speedup_vs_native4"] = f"{native4 / int(row['simTicks']):.9f}"
with path.open("w", newline="", encoding="utf-8") as stream:
    writer = csv.DictWriter(
        stream, fieldnames=rows[0].keys(), delimiter="\t", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
PY

native16_speedup=$(awk -F'\t' '$1 == "native16" { print $3 }' "$out/results.tsv")
hybrid_speedup=$(awk -F'\t' '$1 == "hybrid" { print $3 }' "$out/results.tsv")
launch_supported=false
if awk -v value="$native16_speedup" 'BEGIN { exit !(value >= 1.05) }' &&
   awk -v value="$hybrid_speedup" 'BEGIN { exit !(value >= 1.05) }' &&
   [[ ${cache_lines[native16]} -lt ${cache_lines[native4]} &&
      ${cache_lines[hybrid]} -lt ${cache_lines[native4]} &&
      ${rows[native16]} -lt ${rows[native4]} &&
      ${rows[hybrid]} -lt ${rows[native4]} &&
      ${dram_activates[native16]} -lt ${dram_activates[native4]} &&
      ${dram_activates[hybrid]} -lt ${dram_activates[native4]} ]]; then
    launch_supported=true
fi

{
    printf 'terminal=true\ncorrect=true\nreplicas=1\n'
    printf 'native4_simTicks=%s\nnative16_simTicks=%s\nhybrid_simTicks=%s\n' \
        "${ticks[native4]}" "${ticks[native16]}" "${ticks[hybrid]}"
    printf 'native16_speedup_vs_native4=%s\n' "$native16_speedup"
    printf 'hybrid_speedup_vs_native4=%s\n' "$hybrid_speedup"
    printf 'full_s22_launch_supported=%s\n' "$launch_supported"
    printf 'promotion_scope=screen_only_one_replica_not_architecture_promotion\n'
} >"$out/summary.txt"

sha256sum "$gem5" "$frozen_ramulator" "$config" "$ramulator" \
    "$source_file" "$helper_file" "$admission_file" \
    "$out/provenance/run_sssp_locality_matched_micro.frozen.sh" \
    "$out/bin/converter" "$out/bin/sssp_native4_fp" \
    "$out/bin/sssp_native16_fp" "$out/bin/sssp_hybrid_fp" \
    "$wel" "$graph" "$out/prediction.txt" >"$out/artifacts.after.sha256"
cmp -s "$out/artifacts.before.sha256" "$out/artifacts.after.sha256"
identity_files=("$out/manifest.txt" "$out/prediction.txt" "$out/results.tsv"
    "$out/summary.txt" "$out/artifacts.before.sha256")
if [[ -f $out/postprocess.recovery.txt ]]; then
    identity_files+=("$out/postprocess.recovery.txt")
fi
if [[ -f $out/postprocess.latest.txt ]]; then
    identity_files+=("$out/postprocess.latest.txt")
fi
sha256sum "${identity_files[@]}" >"$out/evidence.identity.sha256"
campaign_status=PASS
rm -f "$out/current_arm.txt"
touch "$out/gate.complete"
cat "$out/summary.txt"
echo SSSP_LOCALITY_MATCHED_MICRO_PASS
