#!/usr/bin/env bash
set -euo pipefail

# Bash reads scripts incrementally. Execute a private snapshot so edits to this
# runner cannot change an already-running experiment.
if [[ ${DX100_FROZEN_RUNNER:-0} != 1 ]]; then
    runner_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
    frozen_runner=$(mktemp /tmp/dx100-vt-consumer-runner.XXXXXX.sh)
    cp -- "${BASH_SOURCE[0]}" "$frozen_runner"
    chmod 0555 "$frozen_runner"
    exec env \
        DX100_FROZEN_RUNNER=1 \
        DX100_FROZEN_RUNNER_PATH="$frozen_runner" \
        DX100_RUNNER_ROOT="$runner_root" \
        "$frozen_runner" "$@"
fi

if [[ -n ${DX100_FROZEN_RUNNER_PATH:-} ]]; then
    trap 'rm -f -- "$DX100_FROZEN_RUNNER_PATH"' EXIT
fi

if [[ $# -ne 4 ]]; then
    echo "usage: $0 GEM5_BIN TEST_BIN CASE OUTDIR" >&2
    exit 2
fi

[[ -n ${DX100_RUNNER_ROOT:-} ]] || {
    echo "frozen runner is missing DX100_RUNNER_ROOT" >&2
    exit 2
}
root=$(realpath "$DX100_RUNNER_ROOT")
source "$root/experiments/scripts/isoarea_pingpong_layout.sh"
gem5=$(realpath "$1")
binary=$(realpath "$2")
case_name=$3
out=$(realpath -m "$4")
overlap=0
polluted=0
transparent_spd_mode=0
direct_retirement=0
debug_flags=${MAA_DEBUG_FLAGS:-MAAVirtualTrace}
require_physical_trace=${MAA_REQUIRE_PHYSICAL_RECORD_TRACE:-0}
require_source_issue_digest=${MAA_REQUIRE_SOURCE_ISSUE_DIGEST:-0}
shared_checkpoint=${DX100_SHARED_CHECKPOINT_DIR:-}
shared_selector=${DX100_SHARED_TREATMENT_FILE:-}
shared_checkpoint_log=${DX100_SHARED_CHECKPOINT_LOG:-}
frozen_ramulator_library=${DX100_FROZEN_RAMULATOR_LIBRARY:-}
ramulator_provenance=${DX100_RAMULATOR_PROVENANCE_FILE:-}
gem5_source_commit=${DX100_GEM5_SOURCE_COMMIT:-$(git -C "$root" rev-parse HEAD)}
gem5_provenance=${DX100_GEM5_PROVENANCE_FILE:-}
extra_maa_args_file=${DX100_EXTRA_MAA_ARGS_FILE:-}
grow_order=${MAA_VIRTUAL_GROW_ORDER:-0}
row_slices=${MAA_ROW_TABLE_SLICES:-16}
row_rows=${MAA_ROW_TABLE_ROWS_PER_SLICE:-64}
row_entries=${MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW:-8}
offset_entries=${MAA_OFFSET_TABLE_ENTRIES:-0}
offset_epoch_entries=${MAA_OFFSET_TABLE_EPOCH_ENTRIES:-0}
response_slots=${MAA_VIRTUAL_RESPONSE_SLOTS:-96}
response_word_pool=${MAA_VIRTUAL_RESPONSE_WORD_POOL:-480}
words_per_cycle=${MAA_VIRTUAL_WORDS_PER_CYCLE:-4}
max_outstanding_writes=${MAA_VIRTUAL_MAX_OUTSTANDING_WRITES:-64}
combine_slots=${MAA_VIRTUAL_COMBINE_SLOTS:-384}
combine_words=${MAA_VIRTUAL_COMBINE_WORDS:-4096}
combine_ways=${MAA_VIRTUAL_COMBINE_WAYS:-4}
combine_victim_policy=${MAA_VIRTUAL_COMBINE_VICTIM_POLICY:-0}
combine_banks=${MAA_VIRTUAL_COMBINE_BANKS:-0}
index_partitions=${MAA_VIRTUAL_INDEX_PARTITIONS:-1}
index_range_passes=${MAA_VIRTUAL_INDEX_RANGE_PASSES:-0}
index_range_policy=${MAA_VIRTUAL_INDEX_RANGE_POLICY:-0}
index_descriptor_spool=${MAA_VIRTUAL_INDEX_DESCRIPTOR_SPOOL:-0}
descriptor_spool_read_ahead=${MAA_VIRTUAL_DESCRIPTOR_SPOOL_READ_AHEAD:-0}
bounded_global_merge=${MAA_VIRTUAL_BOUNDED_GLOBAL_MERGE:-0}
descriptor_spool_variant=${MAA_DESCRIPTOR_SPOOL_VARIANT:-resident_first}
index_range_boundaries=${MAA_VIRTUAL_INDEX_RANGE_BOUNDARIES:-}
index_force_cache=${MAA_VIRTUAL_INDEX_FORCE_CACHE:-0}
partition_keep_combiner=${MAA_VIRTUAL_PARTITION_KEEP_COMBINER:-0}
index_filter_words_per_cycle=${MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE:-4}
require_index_filter_wait=${MAA_REQUIRE_INDEX_FILTER_WAIT:-0}
index_buffer_lines=4
# IND_VirtIndexWordHighWater is a sum of per-instruction-unit peaks. This
# runner has four units and 16 FP32 words per cache line.
index_hwm_capacity=$((index_buffer_lines * 4 * 16))
[[ $grow_order == 0 || $grow_order == 1 ]] || {
    echo "MAA_VIRTUAL_GROW_ORDER must be 0 or 1" >&2
    exit 2
}
[[ $row_slices -gt 0 && $row_rows -gt 0 && $row_entries -gt 0 ]] || {
    echo "row-table dimensions must be positive" >&2
    exit 2
}
[[ $words_per_cycle -ge 0 ]] || {
    echo "MAA_VIRTUAL_WORDS_PER_CYCLE must be nonnegative" >&2
    exit 2
}
[[ $max_outstanding_writes -gt 0 ]] || {
    echo "MAA_VIRTUAL_MAX_OUTSTANDING_WRITES must be positive" >&2
    exit 2
}
[[ $index_partitions -gt 0 && $index_partitions -le 64 ]] || {
    echo "virtual index partitions must be in [1,64]" >&2
    exit 2
}
[[ $index_range_passes == 0 || $index_range_passes == 1 ]] || {
    echo "MAA_VIRTUAL_INDEX_RANGE_PASSES must be 0 or 1" >&2
    exit 2
}
[[ $index_range_policy -ge 0 && $index_range_policy -le 3 ]] || {
    echo "MAA_VIRTUAL_INDEX_RANGE_POLICY must be in [0,3]" >&2
    exit 2
}
[[ $index_range_passes == 1 || $index_range_policy == 0 ]] || {
    echo "nonzero range policy requires range passes" >&2
    exit 2
}
[[ $index_descriptor_spool == 0 || $index_descriptor_spool == 1 ]] || {
    echo "MAA_VIRTUAL_INDEX_DESCRIPTOR_SPOOL must be 0 or 1" >&2
    exit 2
}
[[ $descriptor_spool_read_ahead == 0 ||
   $descriptor_spool_read_ahead == 1 ]] || {
    echo "MAA_VIRTUAL_DESCRIPTOR_SPOOL_READ_AHEAD must be 0 or 1" >&2
    exit 2
}
[[ $descriptor_spool_read_ahead == 0 ||
   $index_descriptor_spool == 1 ]] || {
    echo "descriptor spool read-ahead requires descriptor spooling" >&2
    exit 2
}
[[ $bounded_global_merge == 0 || $bounded_global_merge == 1 ]] || {
    echo "MAA_VIRTUAL_BOUNDED_GLOBAL_MERGE must be 0 or 1" >&2
    exit 2
}
[[ $bounded_global_merge == 0 ||
   ($index_descriptor_spool == 1 &&
    $descriptor_spool_read_ahead == 0) ]] || {
    echo "bounded global merge requires non-read-ahead descriptor spooling" >&2
    exit 2
}
[[ $index_descriptor_spool == 0 ||
   ($index_range_passes == 1 && $index_range_policy == 3) ]] || {
    echo "descriptor spool requires adaptive translated-grow range passes" >&2
    exit 2
}
[[ $descriptor_spool_variant == resident_first ||
   $descriptor_spool_variant == ab_reference ]] || {
    echo "MAA_DESCRIPTOR_SPOOL_VARIANT must be resident_first or ab_reference" >&2
    exit 2
}
index_range_boundary_values=()
if [[ -n $index_range_boundaries ]]; then
    IFS=',' read -r -a index_range_boundary_values \
        <<< "$index_range_boundaries"
fi
if [[ $index_range_policy == 2 ]]; then
    [[ ${#index_range_boundary_values[@]} -eq \
       $((index_partitions + 1)) ]] || {
        echo "oracle policy requires partitions+1 boundaries" >&2
        exit 2
    }
    for ((i = 0; i < ${#index_range_boundary_values[@]}; ++i)); do
        [[ ${index_range_boundary_values[$i]} =~ ^(0[xX][0-9a-fA-F]+|[0-9]+)$ ]] || {
            echo "invalid oracle boundary: ${index_range_boundary_values[$i]}" >&2
            exit 2
        }
        if ((i > 0)); then
            ((index_range_boundary_values[i - 1] <
              index_range_boundary_values[i])) || {
                echo "oracle boundaries must be strictly increasing" >&2
                exit 2
            }
        fi
    done
elif [[ ${#index_range_boundary_values[@]} -ne 0 ]]; then
    echo "explicit boundaries require range policy 2" >&2
    exit 2
fi
[[ $index_force_cache == 0 || $index_force_cache == 1 ]] || {
    echo "MAA_VIRTUAL_INDEX_FORCE_CACHE must be 0 or 1" >&2
    exit 2
}
[[ $partition_keep_combiner == 0 || $partition_keep_combiner == 1 ]] || {
    echo "MAA_VIRTUAL_PARTITION_KEEP_COMBINER must be 0 or 1" >&2
    exit 2
}
[[ $offset_entries -ge 0 && $offset_epoch_entries -ge 0 ]] || {
    echo "OffsetTable capacities must be nonnegative" >&2
    exit 2
}
resolved_offset_entries=$offset_entries
if [[ $resolved_offset_entries -eq 0 ]]; then
    resolved_offset_entries=16384
fi
resolved_offset_epoch_entries=$offset_epoch_entries
if [[ $resolved_offset_epoch_entries -eq 0 ]]; then
    resolved_offset_epoch_entries=$resolved_offset_entries
fi
[[ $index_filter_words_per_cycle -ge 0 ]] || {
    echo "virtual index filter words per cycle must be nonnegative" >&2
    exit 2
}
[[ $require_index_filter_wait == 0 || $require_index_filter_wait == 1 ]] || {
    echo "MAA_REQUIRE_INDEX_FILTER_WAIT must be 0 or 1" >&2
    exit 2
}
[[ $require_physical_trace == 0 || $require_physical_trace == 1 ]] || {
    echo "MAA_REQUIRE_PHYSICAL_RECORD_TRACE must be 0 or 1" >&2
    exit 2
}
[[ $require_source_issue_digest == 0 ||
   $require_source_issue_digest == 1 ]] || {
    echo "MAA_REQUIRE_SOURCE_ISSUE_DIGEST must be 0 or 1" >&2
    exit 2
}
if [[ $require_source_issue_digest == 1 &&
      ",$debug_flags," != *,MAAIssueDigest,* ]]; then
    echo "source-issue validation requires MAAIssueDigest" >&2
    exit 2
fi
if [[ $require_physical_trace == 1 &&
      ",$debug_flags," != *,MAAPhysicalRecordTrace,* ]]; then
    echo "physical-record validation requires MAAPhysicalRecordTrace" >&2
    exit 2
fi
if [[ -n $shared_checkpoint || -n $shared_selector ]]; then
    [[ -n $shared_checkpoint && -n $shared_selector ]] || {
        echo "shared checkpoint directory and treatment file are both required" >&2
        exit 2
    }
    shared_checkpoint=$(realpath "$shared_checkpoint")
    shared_selector=$(realpath -m "$shared_selector")
    [[ -d $shared_checkpoint ]] || {
        echo "shared checkpoint directory does not exist" >&2
        exit 2
    }
    if [[ -n $shared_checkpoint_log ]]; then
        shared_checkpoint_log=$(realpath "$shared_checkpoint_log")
        [[ -f $shared_checkpoint_log ]] || {
            echo "shared checkpoint log does not exist" >&2
            exit 2
        }
        grep -Fq -- "--options 'deferred $shared_selector'" \
            "$shared_checkpoint_log" || {
            echo "shared selector path does not match the frozen checkpoint" \
                >&2
            exit 2
        }
    fi
    [[ -n $frozen_ramulator_library && -n $ramulator_provenance ]] || {
        echo "shared evidence requires frozen Ramulator library/provenance" >&2
        exit 2
    }
fi
[[ $response_slots -gt 0 && $response_word_pool -gt 0 ]] || {
    echo "virtual response capacities must be positive" >&2
    exit 2
}
[[ $combine_slots -gt 0 && $combine_words -gt 0 && $combine_ways -ge 0 &&
   $combine_victim_policy -ge 0 && $combine_victim_policy -le 2 &&
   $combine_banks -ge 0 ]] || {
    echo "virtual combiner capacities must be positive and geometry nonnegative" >&2
    exit 2
}
[[ $combine_ways -eq 0 || $((combine_slots % combine_ways)) -eq 0 ]] || {
    echo "virtual combiner slots must divide evenly into ways" >&2
    exit 2
}
grow_order_args=()
if [[ $grow_order == 1 ]]; then
    grow_order_args+=(--maa_virtual_grow_order)
fi
index_range_args=()
if [[ $index_range_passes == 1 ]]; then
    index_range_args+=(--maa_virtual_index_range_passes)
fi
index_descriptor_spool_args=()
if [[ $index_descriptor_spool == 1 ]]; then
    index_descriptor_spool_args+=(--maa_virtual_index_descriptor_spool)
fi
descriptor_spool_read_ahead_args=()
if [[ $descriptor_spool_read_ahead == 1 ]]; then
    descriptor_spool_read_ahead_args+=(
        --maa_virtual_descriptor_spool_read_ahead
    )
fi
bounded_global_merge_args=()
if [[ $bounded_global_merge == 1 ]]; then
    bounded_global_merge_args+=(--maa_virtual_bounded_global_merge)
fi
index_range_boundary_args=()
if [[ $index_range_policy == 2 ]]; then
    index_range_boundary_args+=(--maa_virtual_index_range_boundaries)
    index_range_boundary_args+=("${index_range_boundary_values[@]}")
fi
index_cache_args=()
if [[ $index_force_cache == 1 ]]; then
    index_cache_args+=(--maa_virtual_index_force_cache)
fi
partition_combiner_args=()
if [[ $partition_keep_combiner == 1 ]]; then
    partition_combiner_args+=(--maa_virtual_partition_keep_combiner)
fi
extra_maa_args=()
if [[ -n $extra_maa_args_file ]]; then
    extra_maa_args_file=$(realpath "$extra_maa_args_file")
    [[ -f $extra_maa_args_file ]] || {
        echo "missing extra MAA argument file: $extra_maa_args_file" >&2
        exit 2
    }
    mapfile -t extra_maa_args < "$extra_maa_args_file"
    [[ ${#extra_maa_args[@]} -gt 0 ]] || {
        echo "extra MAA argument file is empty" >&2
        exit 2
    }
    for argument in "${extra_maa_args[@]}"; do
        [[ $argument == --maa_* && $argument != *[[:space:]]* ]] || {
            echo "invalid extra MAA argument: $argument" >&2
            exit 2
        }
    done
fi
offset_args=()
if [[ $offset_entries -ne 0 ]]; then
    offset_args+=(--maa_num_offset_table_entries "$offset_entries")
fi
if [[ $offset_epoch_entries -ne 0 ]]; then
    offset_args+=(
        --maa_num_offset_table_epoch_entries "$offset_epoch_entries"
    )
fi

case "$case_name" in
native_16k)
    mode=native
    page=16384
    physical=16384
    virtual=0
    direct=0
    reload_only=0
    ;;
native_4k)
    mode=native
    page=4096
    physical=4096
    virtual=0
    direct=0
    reload_only=0
    ;;
native_direct_16k)
    mode=native_direct
    page=16384
    physical=16384
    virtual=0
    direct=1
    reload_only=0
    ;;
native_direct_4k)
    mode=native_direct
    page=4096
    physical=4096
    virtual=0
    direct=1
    reload_only=0
    ;;
paged_16k)
    mode=paged
    page=16384
    physical=16384
    virtual=1
    direct=1
    reload_only=0
    ;;
paged_4k)
    mode=paged
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=0
    ;;
paged_overlap_4k)
    mode=paged_overlap
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=0
    overlap=1
    ;;
transparent_4k)
    mode=transparent
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=0
    ;;
direct_retirement_4k)
    # The binary keeps the deterministic transparent treatment.  Selector 3
    # changes only the MAA consumer transport after producer page WriteResp.
    mode=transparent
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=0
    transparent_spd_mode=3
    direct_retirement=1
    ;;
isoarea_serial_4k)
    mode=transparent
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=0
    transparent_spd_mode=0
    ;;
isoarea_serial_2k)
    mode=transparent
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=0
    transparent_spd_mode=1
    ;;
isoarea_pingpong_2k)
    mode=transparent
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=0
    transparent_spd_mode=2
    ;;
transparent_ready_4k)
    mode=transparent_ready
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=0
    ;;
transparent_displaced_4k)
    mode=transparent_displaced
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=0
    polluted=1
    ;;
transparent_reload_warm_4k)
    mode=transparent_reload_warm
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=1
    ;;
transparent_reload_cold_4k)
    mode=transparent_reload_cold
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=1
    polluted=1
    ;;
paged_displaced_4k)
    mode=paged_displaced
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=0
    polluted=1
    ;;
paged_staged_16k)
    mode=paged_staged
    page=16384
    physical=16384
    virtual=1
    direct=0
    reload_only=0
    ;;
paged_staged_conditional_16k)
    mode=paged_staged_conditional
    page=16384
    physical=16384
    virtual=1
    direct=0
    reload_only=0
    ;;
paged_reload_warm_4k)
    mode=paged_reload_warm
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=1
    ;;
paged_reload_cold_4k)
    mode=paged_reload_cold
    page=4096
    physical=4096
    virtual=1
    direct=1
    reload_only=1
    polluted=1
    ;;
*)
    echo "unknown consumer case: $case_name" >&2
    exit 2
    ;;
esac

if [[ $index_partitions -ne 1 &&
      ( $virtual -ne 1 || $direct -ne 1 || $reload_only -eq 1 ) ]]; then
    echo "index partitioning requires an active direct virtual gather" >&2
    exit 2
fi

if [[ -e $out ]]; then
    echo "refusing to overwrite existing output path: $out" >&2
    exit 2
fi
source_status=$(git -C "$root" status --short)
[[ -z $source_status ]] || {
    echo "refusing evidence run from a dirty tracked/untracked source tree" >&2
    printf '%s\n' "$source_status" >&2
    exit 1
}
mkdir -p "$out"

config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
ramulator_root="$root/ext/ramulator2/ramulator2"
ramulator_library=${frozen_ramulator_library:-$ramulator_root/libramulator.so}
ramulator_library=$(realpath "$ramulator_library")
[[ -f $ramulator_library ]] || {
    echo "missing Ramulator library: $ramulator_library" >&2
    exit 1
}
if [[ -n $ramulator_provenance ]]; then
    ramulator_provenance=$(realpath "$ramulator_provenance")
else
    ramulator_provenance="$out/ramulator_provenance_fallback.txt"
    sha256sum "$ramulator_library" > "$ramulator_provenance"
fi
[[ -f $ramulator_provenance ]] || {
    echo "missing Ramulator provenance: $ramulator_provenance" >&2
    exit 1
}
ramulator_library_dir=$(dirname "$ramulator_library")
LD_LIBRARY_PATH="$ramulator_library_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    ldd "$gem5" > "$out/gem5.ldd.txt"
loaded_ramulator=$(awk '$1 == "libramulator.so" { print $3 }' \
    "$out/gem5.ldd.txt")
[[ -n $loaded_ramulator && $(realpath "$loaded_ramulator") == \
    "$ramulator_library" ]] || {
    echo "gem5 does not resolve the requested frozen libramulator.so" >&2
    exit 1
}
{
    printf 'case=%s\n' "$case_name"
    printf 'mode=%s\n' "$mode"
    printf 'logical_tile_elements=16384\n'
    printf 'page_elements=%s\n' "$page"
    printf 'physical_tile_elements=%s\n' "$physical"
    printf 'direct_retirement=%s\n' "$direct_retirement"
    if [[ $direct_retirement -eq 1 ]]; then
        printf 'direct_retirement_scope=terminal_fp64_mul_dense_store\n'
    else
        printf 'direct_retirement_scope=disabled\n'
    fi
    printf 'row_table_slices=%s\n' "$row_slices"
    printf 'row_table_rows_per_slice=%s\n' "$row_rows"
    printf 'row_table_entries_per_subslice_row=%s\n' "$row_entries"
    printf 'offset_table_entries=%s\n' "$offset_entries"
    printf 'offset_table_epoch_entries=%s\n' "$offset_epoch_entries"
    printf 'virtual_grow_order=%s\n' "$grow_order"
    printf 'virtual_response_slots=%s\n' "$response_slots"
    printf 'virtual_response_word_pool=%s\n' "$response_word_pool"
    printf 'virtual_words_per_cycle=%s\n' "$words_per_cycle"
    printf 'virtual_max_outstanding_writes=%s\n' \
        "$max_outstanding_writes"
    printf 'virtual_combine_slots=%s\n' "$combine_slots"
    printf 'virtual_combine_words=%s\n' "$combine_words"
    printf 'virtual_combine_ways=%s\n' "$combine_ways"
    printf 'virtual_combine_victim_policy=%s\n' "$combine_victim_policy"
    printf 'virtual_combine_banks=%s\n' "$combine_banks"
    printf 'virtual_index_partitions=%s\n' "$index_partitions"
    printf 'virtual_index_range_passes=%s\n' "$index_range_passes"
    printf 'virtual_index_range_policy=%s\n' "$index_range_policy"
    printf 'virtual_index_descriptor_spool=%s\n' \
        "$index_descriptor_spool"
    printf 'virtual_descriptor_spool_read_ahead=%s\n' \
        "$descriptor_spool_read_ahead"
    printf 'virtual_bounded_global_merge=%s\n' "$bounded_global_merge"
    printf 'descriptor_spool_variant=%s\n' "$descriptor_spool_variant"
    printf 'virtual_index_range_boundaries=%s\n' \
        "${index_range_boundaries:-none}"
    printf 'virtual_index_force_cache=%s\n' "$index_force_cache"
    printf 'virtual_partition_keep_combiner=%s\n' \
        "$partition_keep_combiner"
    printf 'virtual_index_filter_words_per_cycle=%s\n' \
        "$index_filter_words_per_cycle"
    printf 'require_index_filter_wait=%s\n' "$require_index_filter_wait"
    printf 'cache_pollution_bytes=%s\n' \
        "$((polluted * 32 * 1024 * 1024))"
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'gem5_source_commit=%s\n' "$gem5_source_commit"
    printf 'gem5_provenance=%s\n' "${gem5_provenance:-none}"
    printf 'extra_maa_args_file=%s\n' "${extra_maa_args_file:-none}"
    printf 'baseline_commit=6e84c2c4a4c9b008f0efb78314c7ac1b7f828b55\n'
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'timeout=none\n'
    printf 'shared_checkpoint=%s\n' "${shared_checkpoint:-none}"
    printf 'shared_treatment_file=%s\n' "${shared_selector:-none}"
    printf 'shared_checkpoint_log=%s\n' "${shared_checkpoint_log:-none}"
    printf 'physical_record_schema=dx100.physical_admission.v1\n'
    printf 'source_issue_digest_required=%s\n' \
        "$require_source_issue_digest"
    printf 'frozen_ramulator_library=%s\n' "$ramulator_library"
    printf 'ramulator_provenance=%s\n' "$ramulator_provenance"
} > "$out/manifest.txt"
git -C "$root" status --short > "$out/source_status.txt"
git -C "$root" diff --binary > "$out/source.diff"
snapshot="$out/source_snapshot"
mkdir -p "$snapshot"
cp -- "$config" "$snapshot/se.py"
cp -- "$ramulator" "$snapshot/ramulator.yaml"
cp -- "$(realpath "$0")" "$snapshot/run_virtual_tile_consumer_case.sh"
cp -- "$root/benchmarks/API/test_virtual_tile_consumer.cpp" \
    "$snapshot/test_virtual_tile_consumer.cpp"
cp -- "$root/benchmarks/API/MAA_gem5.hpp" "$snapshot/MAA_gem5.hpp"
cp -- "$root/src/mem/MAA/IndirectAccess.cc" "$snapshot/IndirectAccess.cc"
cp -- "$root/src/mem/MAA/IndirectAccess.hh" "$snapshot/IndirectAccess.hh"
cp -- "$root/src/mem/MAA/BoundedRangePass.hh" \
    "$snapshot/BoundedRangePass.hh"
cp -- "$root/src/mem/MAA/BoundedDescriptorSpool.hh" \
    "$snapshot/BoundedDescriptorSpool.hh"
cp -- "$root/src/mem/MAA/BoundedFourRunMerge.hh" \
    "$snapshot/BoundedFourRunMerge.hh"
cp -- "$root/src/mem/MAA/BoundedQuantileRanges.hh" \
    "$snapshot/BoundedQuantileRanges.hh"
cp -- "$root/src/mem/MAA/BoundedMetadataLedger.hh" \
    "$snapshot/BoundedMetadataLedger.hh"
cp -- "$root/src/mem/MAA/Tables.cc" "$snapshot/Tables.cc"
cp -- "$root/src/mem/MAA/Tables.hh" "$snapshot/Tables.hh"
cp -- "$root/src/mem/MAA/TransparentSPDController.hh" \
    "$snapshot/TransparentSPDController.hh"
cp -- "$root/src/mem/MAA/HybridConsumerPipeline.hh" \
    "$snapshot/HybridConsumerPipeline.hh"
cp -- "$root/src/mem/MAA/MAA.cc" "$snapshot/MAA.cc"
cp -- "$root/src/mem/MAA/MAA.hh" "$snapshot/MAA.hh"
cp -- "$root/src/mem/MAA/Port.cc" "$snapshot/Port.cc"
cp -- "$root/src/mem/MAA/CacheSidePort.cc" "$snapshot/CacheSidePort.cc"
cp -- "$root/src/mem/MAA/IF.cc" "$snapshot/IF.cc"
cp -- "$root/src/mem/MAA/IF.hh" "$snapshot/IF.hh"
cp -- "$root/src/mem/MAA/StreamAccess.cc" "$snapshot/StreamAccess.cc"
cp -- "$root/src/mem/MAA/StreamAccess.hh" "$snapshot/StreamAccess.hh"
cp -- "$root/src/mem/MAA/ALU.cc" "$snapshot/ALU.cc"
cp -- "$root/src/mem/MAA/ALU.hh" "$snapshot/ALU.hh"
cp -- "$root/src/mem/MAA/MAA.py" "$snapshot/MAA.py"
cp -- "$root/configs/common/MAAConfig.py" "$snapshot/MAAConfig.py"
cp -- "$root/configs/common/Options.py" "$snapshot/Options.py"
cp -- "$root/src/mem/MAA/CpuSidePort.cc" "$snapshot/CpuSidePort.cc"
cp -- "$root/tests/virtual_tile/bounded_range_pass_test.cc" \
    "$snapshot/bounded_range_pass_test.cc"
cp -- "$root/tests/virtual_tile/bounded_descriptor_spool_test.cc" \
    "$snapshot/bounded_descriptor_spool_test.cc"
cp -- "$root/tests/maa/bounded_four_run_merge_test.cc" \
    "$snapshot/bounded_four_run_merge_test.cc"
cp -- "$root/tests/virtual_tile/bounded_quantile_ranges_test.cc" \
    "$snapshot/bounded_quantile_ranges_test.cc"
cp -- "$root/tests/virtual_tile/bounded_metadata_ledger_test.cc" \
    "$snapshot/bounded_metadata_ledger_test.cc"
cp -- "$root/experiments/tests/test_bounded_range_live_contract.py" \
    "$snapshot/test_bounded_range_live_contract.py"
cp -- "$root/experiments/tests/test_descriptor_spool_live_contract.py" \
    "$snapshot/test_descriptor_spool_live_contract.py"
cp -- "$root/experiments/tests/test_descriptor_filter_accounting.py" \
    "$snapshot/test_descriptor_filter_accounting.py"
cp -- "$root/experiments/scripts/run_bounded_range_pass_unit.sh" \
    "$snapshot/run_bounded_range_pass_unit.sh"
cp -- "$root/experiments/scripts/run_bounded_descriptor_spool_unit.sh" \
    "$snapshot/run_bounded_descriptor_spool_unit.sh"
cp -- "$root/tests/maa/run_bounded_four_run_merge_unit.sh" \
    "$snapshot/run_bounded_four_run_merge_unit.sh"
cp -- "$root/experiments/scripts/run_true_4k_reorder_matrix.sh" \
    "$snapshot/run_true_4k_reorder_matrix.sh"
cp -- "$root/experiments/scripts/run_true_4k_descriptor_spool_matrix.sh" \
    "$snapshot/run_true_4k_descriptor_spool_matrix.sh"
cp -- "$root/experiments/analysis/hybrid_overhead_attribution.py" \
    "$snapshot/hybrid_overhead_attribution.py"
{
    printf 'MAA_DEBUG_FLAGS=%q ' "$debug_flags"
    printf 'MAA_REQUIRE_PHYSICAL_RECORD_TRACE=%q ' "$require_physical_trace"
    printf 'MAA_REQUIRE_SOURCE_ISSUE_DIGEST=%q ' \
        "$require_source_issue_digest"
    printf 'DX100_SHARED_CHECKPOINT_DIR=%q ' "${shared_checkpoint:-}"
    printf 'DX100_SHARED_TREATMENT_FILE=%q ' "${shared_selector:-}"
    printf 'DX100_SHARED_CHECKPOINT_LOG=%q ' "${shared_checkpoint_log:-}"
    printf 'DX100_FROZEN_RAMULATOR_LIBRARY=%q ' "$ramulator_library"
    printf 'DX100_RAMULATOR_PROVENANCE_FILE=%q ' "$ramulator_provenance"
    printf 'DX100_GEM5_SOURCE_COMMIT=%q ' "$gem5_source_commit"
    printf 'DX100_GEM5_PROVENANCE_FILE=%q ' "$gem5_provenance"
    printf 'MAA_DESCRIPTOR_SPOOL_VARIANT=%q ' "$descriptor_spool_variant"
    printf 'MAA_VIRTUAL_DESCRIPTOR_SPOOL_READ_AHEAD=%q ' \
        "$descriptor_spool_read_ahead"
    printf 'MAA_VIRTUAL_BOUNDED_GLOBAL_MERGE=%q ' \
        "$bounded_global_merge"
    printf '%q %q %q %q %q\n' "${DX100_FROZEN_RUNNER_PATH:-$0}" \
        "$gem5" "$binary" "$case_name" "$out"
} > "$out/invocation.sh.txt"
sha256sum "$gem5" "$binary" "$snapshot/se.py" \
    "$snapshot/ramulator.yaml" "$ramulator_library" \
    "$ramulator_provenance" "$out/gem5.ldd.txt" \
    "$snapshot/run_virtual_tile_consumer_case.sh" \
    "$snapshot/test_virtual_tile_consumer.cpp" \
    "$snapshot/MAA_gem5.hpp" \
    "$snapshot/IndirectAccess.cc" "$snapshot/IndirectAccess.hh" \
    "$snapshot/BoundedRangePass.hh" \
    "$snapshot/BoundedDescriptorSpool.hh" \
    "$snapshot/BoundedFourRunMerge.hh" \
    "$snapshot/BoundedQuantileRanges.hh" \
    "$snapshot/BoundedMetadataLedger.hh" \
    "$snapshot/Tables.cc" "$snapshot/Tables.hh" \
    "$snapshot/TransparentSPDController.hh" \
    "$snapshot/HybridConsumerPipeline.hh" \
    "$snapshot/MAA.cc" "$snapshot/MAA.hh" \
    "$snapshot/Port.cc" "$snapshot/CacheSidePort.cc" \
    "$snapshot/IF.cc" "$snapshot/IF.hh" \
    "$snapshot/StreamAccess.cc" "$snapshot/StreamAccess.hh" \
    "$snapshot/ALU.cc" "$snapshot/ALU.hh" \
    "$snapshot/MAA.py" "$snapshot/MAAConfig.py" "$snapshot/Options.py" \
    "$snapshot/hybrid_overhead_attribution.py" \
    "$snapshot/CpuSidePort.cc" \
    "$snapshot/bounded_range_pass_test.cc" \
    "$snapshot/bounded_descriptor_spool_test.cc" \
    "$snapshot/bounded_four_run_merge_test.cc" \
    "$snapshot/bounded_quantile_ranges_test.cc" \
    "$snapshot/bounded_metadata_ledger_test.cc" \
    "$snapshot/test_bounded_range_live_contract.py" \
    "$snapshot/test_descriptor_spool_live_contract.py" \
    "$snapshot/test_descriptor_filter_accounting.py" \
    "$snapshot/run_bounded_range_pass_unit.sh" \
    "$snapshot/run_bounded_descriptor_spool_unit.sh" \
    "$snapshot/run_bounded_four_run_merge_unit.sh" \
    "$snapshot/run_true_4k_reorder_matrix.sh" \
    "$snapshot/run_true_4k_descriptor_spool_matrix.sh" \
    "$out/source.diff" "$out/source_status.txt" "$out/invocation.sh.txt" \
    > "$out/artifact_sha256.txt"
if [[ -n $extra_maa_args_file ]]; then
    sha256sum "$extra_maa_args_file" >> "$out/artifact_sha256.txt"
fi
if [[ -n $gem5_provenance ]]; then
    gem5_provenance=$(realpath "$gem5_provenance")
    [[ -f $gem5_provenance ]] || {
        echo "missing gem5 provenance: $gem5_provenance" >&2
        exit 1
    }
    sha256sum "$gem5_provenance" >> "$out/artifact_sha256.txt"
fi

checkpoint_dir="$out/checkpoint"
workload_options="$mode $page"
if [[ -z $shared_checkpoint ]]; then
    set +e
    LD_LIBRARY_PATH="$ramulator_library_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    /usr/bin/time -f 'checkpoint_wall=%e checkpoint_rss_kb=%M' \
        "$gem5" --listener-mode=off --outdir="$checkpoint_dir" \
        "$config" --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
        --max-checkpoints=1 --cmd "$binary" --options "$workload_options" \
        > "$out/checkpoint.log" 2>&1
    checkpoint_rc=$?
    set -e
    printf '%s\n' "$checkpoint_rc" > "$out/checkpoint.exit"
    [[ $checkpoint_rc -eq 0 ]] || {
        echo "checkpoint failed with rc=$checkpoint_rc" >&2
        exit 1
    }
    grep -Eq "VIRTUAL_TILE_CONSUMER_LAYOUT mode=${mode} page_elements=${page} logical_elements=16384 mem_size=2147483648" \
        "$out/checkpoint.log" || {
        echo "binary/config consumer contract mismatch" >&2
        exit 1
    }
else
    checkpoint_dir="$shared_checkpoint"
    workload_options="deferred $shared_selector"
    selector_tmp="$shared_selector.tmp.$$"
    printf '%s %s\n' "$mode" "$page" > "$selector_tmp"
    mv -- "$selector_tmp" "$shared_selector"
    cp -- "$shared_selector" "$out/treatment.txt"
    (
        cd "$shared_checkpoint"
        find . -type f -print0 | sort -z | xargs -0 sha256sum
    ) > "$out/shared_checkpoint_files.sha256"
    shared_checkpoint_digest=$(sha256sum \
        "$out/shared_checkpoint_files.sha256" | awk '{print $1}')
    printf '%s  shared_checkpoint_files.sha256\n' \
        "$shared_checkpoint_digest" \
        > "$out/shared_checkpoint_identity.sha256"
    printf '%s\n' "$shared_checkpoint" > "$out/checkpoint.path"
    printf '0\n' > "$out/checkpoint.exit"
fi

layout_log="$out/checkpoint.log"
layout_mode="$mode"
layout_page="$page"
if [[ -n $shared_checkpoint ]]; then
    layout_log=${shared_checkpoint_log:-$(dirname "$shared_checkpoint")/shared-checkpoint.log}
    layout_mode=deferred
    layout_page=0
fi
isoarea_validate_layout "$layout_log" "$layout_mode" "$layout_page" || {
    echo "binary/config consumer contract mismatch" >&2
    exit 1
}
if [[ $direct_retirement -eq 1 ]]; then
    grep -Fqx \
        'VIRTUAL_TILE_CONSUMER_ALIGNMENT backing_mod64=0 destination_mod64=0' \
        "$layout_log" || {
        echo "direct retirement requires an aligned frozen workload" >&2
        exit 1
    }
fi

set +e
LD_LIBRARY_PATH="$ramulator_library_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
/usr/bin/time -f 'restore_wall=%e restore_rss_kb=%M' \
    "$gem5" --listener-mode=off --outdir="$out/run" \
    --debug-flags="$debug_flags" --debug-file=virtual_trace.log "$config" \
    --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB \
    --checkpoint-dir="$checkpoint_dir" \
    --sys-clock 3.2GHz --cpu-clock 3.2GHz \
    --caches --l1d_size=32kB --l1d_assoc=8 \
    --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8 \
    --l1i_size=32kB --l1i_assoc=8 \
    --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16 --l1i_write_buffers=8 \
    --l2cache --l2_size=256kB --l2_assoc=4 \
    --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16 \
    --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256 \
    --l3_write_buffers=128 --l3_ports=4 --cacheline_size=64 \
    --mem-type Ramulator2 --ramulator-config "$ramulator" --mem-channels=1 \
    --maa --maa_num_tile_elements=16384 \
    --maa_physical_tile_elements="$physical" \
    --maa_transparent_spd_mode="$transparent_spd_mode" \
    --maa_num_initial_row_table_slices="$row_slices" \
    --maa_num_row_table_rows_per_slice="$row_rows" \
    --maa_num_row_table_entries_per_subslice_row="$row_entries" \
    "${offset_args[@]}" \
    "${grow_order_args[@]}" \
    --maa_virtual_combine_slots="$combine_slots" \
    --maa_virtual_combine_words="$combine_words" \
    --maa_virtual_combine_ways="$combine_ways" \
    --maa_virtual_combine_victim_policy="$combine_victim_policy" \
    --maa_virtual_combine_banks="$combine_banks" \
    --maa_virtual_response_slots="$response_slots" \
    --maa_virtual_response_word_pool="$response_word_pool" \
    --maa_virtual_words_per_cycle="$words_per_cycle" \
    --maa_virtual_max_outstanding_writes="$max_outstanding_writes" \
    --maa_virtual_masked_writes \
    --maa_virtual_index_buffer_lines="$index_buffer_lines" \
    --maa_virtual_index_partitions="$index_partitions" \
    --maa_virtual_index_range_policy="$index_range_policy" \
    "${index_range_boundary_args[@]}" \
    "${index_range_args[@]}" \
    "${index_descriptor_spool_args[@]}" \
    "${descriptor_spool_read_ahead_args[@]}" \
    "${bounded_global_merge_args[@]}" \
    "${index_cache_args[@]}" \
    "${partition_combiner_args[@]}" \
    "${extra_maa_args[@]}" \
    --maa_virtual_index_filter_words_per_cycle="$index_filter_words_per_cycle" \
    --cmd "$binary" --options "$workload_options" \
    > "$out/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" > "$out/restore.exit"
[[ $restore_rc -eq 0 ]] || {
    echo "restore failed with rc=$restore_rc" >&2
    exit 1
}

config_ini="$out/run/config.ini"
# config.ini records the user-facing zero sentinel; the C++ constructor
# resolves it to the logical tile size. Mechanism gates below use the resolved
# values.
for expected in \
    "transparent_spd_mode=$transparent_spd_mode" \
    "num_initial_row_table_slices=$row_slices" \
    "num_row_table_rows_per_slice=$row_rows" \
    "num_row_table_entries_per_subslice_row=$row_entries" \
    "num_offset_table_entries=$offset_entries" \
    "num_offset_table_epoch_entries=$offset_epoch_entries" \
    "virtual_index_partitions=$index_partitions" \
    "virtual_index_range_passes=$([[ $index_range_passes -eq 1 ]] && echo true || echo false)" \
    "virtual_index_range_policy=$index_range_policy" \
    "virtual_index_descriptor_spool=$([[ $index_descriptor_spool -eq 1 ]] && echo true || echo false)" \
    "virtual_descriptor_spool_read_ahead=$([[ $descriptor_spool_read_ahead -eq 1 ]] && echo true || echo false)" \
    "virtual_bounded_global_merge=$([[ $bounded_global_merge -eq 1 ]] && echo true || echo false)" \
    "virtual_index_force_cache=$([[ $index_force_cache -eq 1 ]] && echo true || echo false)" \
    "virtual_partition_keep_combiner=$([[ $partition_keep_combiner -eq 1 ]] && echo true || echo false)" \
    "virtual_index_filter_words_per_cycle=$index_filter_words_per_cycle" \
    "virtual_words_per_cycle=$words_per_cycle" \
    "virtual_max_outstanding_writes=$max_outstanding_writes" \
    "reconfigure_row_table=false"; do
    grep -Fqx "$expected" "$config_ini" || {
        echo "missing resolved row-table treatment: $expected" >&2
        exit 1
    }
done

result_count=$(grep -Ec \
    "^VIRTUAL_TILE_CONSUMER_RESULT mode=${mode} page_elements=${page} hash=[0-9]+ errors=0$" \
    "$out/restore.log" || true)
roi_count=$(grep -Fxc 'ROI Ended' "$out/restore.log" || true)
fatal_count=$(grep -Eic \
    'panic|fatal|assert|abort|segmentation fault|error:' \
    "$out/restore.log" || true)
m5_exit_count=$(grep -Ec \
    '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' \
    "$out/restore.log" || true)
stats_begin_count=$(grep -Fxc -- \
    '---------- Begin Simulation Statistics ----------' \
    "$out/run/stats.txt" || true)
stats_end_count=$(grep -Fxc -- \
    '---------- End Simulation Statistics   ----------' \
    "$out/run/stats.txt" || true)
[[ $result_count -eq 1 && $roi_count -eq 1 && $fatal_count -eq 0 && \
   $m5_exit_count -eq 1 && $stats_begin_count -gt 0 && \
   $stats_begin_count -eq $stats_end_count ]] || {
    printf 'invalid completion: result=%s roi=%s fatal=%s m5_exit=%s stats=%s/%s\n' \
        "$result_count" "$roi_count" "$fatal_count" "$m5_exit_count" \
        "$stats_begin_count" "$stats_end_count" >&2
    exit 1
}
if [[ -n $shared_checkpoint ]]; then
    treatment_count=$(grep -Fxc \
        "VIRTUAL_TILE_CONSUMER_TREATMENT mode=${mode} page_elements=${page} source=deferred_file_v1" \
        "$out/restore.log" || true)
    [[ $treatment_count -eq 1 ]] || {
        echo "shared checkpoint did not consume the exact treatment" >&2
        exit 1
    }
fi
pollution_count=$(grep -Fxc 'VIRTUAL_TILE_CONSUMER_POLLUTION bytes=33554432' \
    "$out/restore.log" || true)
[[ $pollution_count -eq $polluted ]] || {
    echo "invalid cache-pollution evidence: $pollution_count/$polluted" >&2
    exit 1
}
output_hash=$(sed -nE \
    "s/^VIRTUAL_TILE_CONSUMER_RESULT mode=${mode} page_elements=${page} hash=([0-9]+) errors=0$/\\1/p" \
    "$out/restore.log")

read -r ticks insts index_line_reads index_words index_hwm \
    index_filter_words index_filter_cycles index_filter_wait_events \
    index_filter_wait_cycles \
    write_issues write_completions \
    pages_ready pages_ready_early first_page_cycles all_page_cycles \
    page_span_cycles \
    indirect_spd_reads stream_spd_reads stream_writes alu_compute \
    page_ready_signals page_wait_reads page_wait_deferrals \
    page_wait_responses \
    l3_read_hits l3_read_misses l3_write_requests \
    memory_bytes_read memory_bytes_written cpu_cycles \
    rt_cache_lines rt_rows rt_unique_cache_lines rt_unique_rows \
    source_reads response_slot_hwm response_word_hwm \
    response_pool_stalls < <(
    awk '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 == "simTicks" { ticks = $2 }
        section == 1 && $1 == "simInsts" { insts = $2 }
        section == 1 && $1 ~ /IND_VirtIndexLineReads$/ { il += $2 }
        section == 1 && $1 ~ /IND_VirtIndexWords$/ { iw += $2 }
        section == 1 && $1 ~ /IND_VirtIndexWordHighWater$/ { hw += $2 }
        section == 1 && $1 ~ /IND_VirtIndexFilterWords$/ { ifw += $2 }
        section == 1 && $1 ~ /IND_VirtIndexFilterCycles$/ { ifc += $2 }
        section == 1 && $1 ~ /IND_VirtIndexFilterWaitEvents$/ { ife += $2 }
        section == 1 && $1 ~ /IND_VirtIndexFilterWaitCycles$/ { ifx += $2 }
        section == 1 && $1 ~ /IND_VirtWriteIssues$/ { wi += $2 }
        section == 1 && $1 ~ /IND_VirtWriteCompletions$/ { wc += $2 }
        section == 1 && $1 ~ /IND_VirtPagesReady$/ { pr += $2 }
        section == 1 && $1 ~ /IND_VirtPagesReadyBeforeSourceDrain$/ { pe += $2 }
        section == 1 && $1 ~ /IND_VirtFirstPageReadyCycles$/ { pf += $2 }
        section == 1 && $1 ~ /IND_VirtAllPagesReadyCycles$/ { pa += $2 }
        section == 1 && $1 ~ /IND_VirtPageReadySpanCycles$/ { ps += $2 }
        section == 1 && $1 ~ /IND_CyclesSPDReadAccess$/ { ir += $2 }
        section == 1 && $1 ~ /STR_CyclesSPDReadAccess$/ { sr += $2 }
        section == 1 && $1 == "system.maa.numInst_STRWR" { sw += $2 }
        section == 1 && $1 ~ /ALU_CyclesCompute$/ { ac += $2 }
        section == 1 && $1 == "system.maa.virtual_page_ready_signals" { prs = $2 }
        section == 1 && $1 == "system.maa.virtual_page_wait_reads" { pwr = $2 }
        section == 1 && $1 == "system.maa.virtual_page_wait_deferrals" { pwd = $2 }
        section == 1 && $1 == "system.maa.virtual_page_wait_responses" { pws = $2 }
        section == 1 && $1 == "system.l3.ReadReq_T.hits::maa" { lh = $2 }
        section == 1 && $1 == "system.l3.ReadReq_T.misses::maa" { lm = $2 }
        section == 1 && $1 == "system.l3.WriteReq_T.accesses::maa" { lw = $2 }
        section == 1 && $1 == "system.mem_ctrls.bytesRead::maa" { mb = $2 }
        section == 1 && $1 == "system.mem_ctrls.bytesWritten::maa" { mw = $2 }
        section == 1 && $1 == "system.switch_cpus0.numCycles" { cc = $2 }
        section == 1 && $1 ~ /IND_NumCacheLineInserted$/ { rc += $2 }
        section == 1 && $1 ~ /IND_NumRowsInserted$/ { rr += $2 }
        section == 1 && $1 ~ /IND_NumUniqueCacheLineInserted$/ { ruc += $2 }
        section == 1 && $1 ~ /IND_NumUniqueRowsInserted$/ { rur += $2 }
        section == 1 && $1 ~ /IND_LoadsCacheHitResponding$/ { lc += $2 }
        section == 1 && $1 ~ /IND_LoadsCacheHitAccessing$/ { la += $2 }
        section == 1 && $1 ~ /IND_LoadsMemAccessing$/ { lmema += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolLineReads$/ { dl += $2 }
        section == 1 && $1 ~ /IND_VirtResponseSlotHighWater$/ { rsh += $2 }
        section == 1 && $1 ~ /IND_VirtResponseWordHighWater$/ { rwh += $2 }
        section == 1 && $1 ~ /IND_VirtResponseWordPoolStalls$/ { rps += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            print ticks + 0, insts + 0, il + 0, iw + 0, hw + 0,
                  ifw + 0, ifc + 0, ife + 0, ifx + 0,
                  wi + 0, wc + 0, pr + 0, pe + 0, pf + 0, pa + 0,
                  ps + 0, ir + 0, sr + 0, sw + 0, ac + 0,
                  prs + 0, pwr + 0, pwd + 0, pws + 0,
                  lh + 0, lm + 0, lw + 0, mb + 0, mw + 0, cc + 0, rc + 0,
                  rr + 0, ruc + 0, rur + 0,
                  lc + la + lmema - il - dl,
                  rsh + 0, rwh + 0, rps + 0
            exit
        }
    ' "$out/run/stats.txt"
)
read -r rt_full offset_epoch_drains build_rounds fill_cycles \
    request_cycles < <(
    awk '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ /IND_NumRTFull$/ { rf += $2 }
        section == 1 && $1 ~ /IND_NumOTEpochDrain$/ { od += $2 }
        section == 1 && $1 ~ /IND_VirtBuildRounds$/ { br += $2 }
        section == 1 && $1 ~ /IND_CyclesFill$/ { fc += $2 }
        section == 1 && $1 ~ /IND_CyclesRequest$/ { qc += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            print rf + 0, od + 0, br + 0, fc + 0, qc + 0
            exit
        }
    ' "$out/run/stats.txt"
)
read -r bounded_summary_lines bounded_summary_words bounded_summary_records \
    bounded_summary_probes bounded_summary_visits bounded_plan_bytes \
    bounded_replay_lines bounded_replay_words bounded_replay_passes \
    bounded_replay_drains bounded_replay_max_epoch bounded_word_entries \
    bounded_offset_entries bounded_row_directories bounded_row_lines \
    bounded_metadata_bytes < <(
    awk '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ /IND_BoundedSummaryLineReads$/ { sl += $2 }
        section == 1 && $1 ~ /IND_BoundedSummaryWords$/ { sw += $2 }
        section == 1 && $1 ~ /IND_BoundedSummaryRecords$/ { sr += $2 }
        section == 1 && $1 ~ /IND_BoundedSummaryHashProbes$/ { sp += $2 }
        section == 1 && $1 ~ /IND_BoundedSummaryReductionVisits$/ { sv += $2 }
        section == 1 && $1 ~ /IND_BoundedSummaryPlanBytes$/ { pb += $2 }
        section == 1 && $1 ~ /IND_BoundedReplayLineReads$/ { rl += $2 }
        section == 1 && $1 ~ /IND_BoundedReplayWords$/ { rw += $2 }
        section == 1 && $1 ~ /IND_BoundedReplayPasses$/ { rp += $2 }
        section == 1 && $1 ~ /IND_BoundedReplayDrains$/ { rd += $2 }
        section == 1 && $1 ~ /IND_BoundedReplayMaxEpochAdmissions$/ { rm += $2 }
        section == 1 && $1 ~ /IND_BoundedWordEntries$/ { we += $2 }
        section == 1 && $1 ~ /IND_BoundedOffsetLinkEntries$/ { oe += $2 }
        section == 1 && $1 ~ /IND_BoundedRowDirectoryEntries$/ { dr += $2 }
        section == 1 && $1 ~ /IND_BoundedRowLineEntries$/ { le += $2 }
        section == 1 && $1 ~ /IND_BoundedReorderMetadataBytes$/ { mb += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            print sl + 0, sw + 0, sr + 0, sp + 0, sv + 0, pb + 0,
                  rl + 0, rw + 0, rp + 0, rd + 0, rm + 0, we + 0,
                  oe + 0, dr + 0, le + 0, mb + 0
            exit
        }
    ' "$out/run/stats.txt"
)
read -r bounded_bucket_lines bounded_bucket_words \
    descriptor_filter_retry_inspections descriptor_final_flush_stalls \
    descriptor_b_scans descriptor_resident_populations \
    descriptor_resident_descriptors descriptor_external_descriptors \
    descriptor_external_segments \
    descriptor_write_lines descriptor_write_bytes descriptor_write_acks \
    descriptor_read_lines descriptor_read_bytes descriptor_write_stalls \
    descriptor_read_stalls descriptor_write_hwm descriptor_staging_entries \
    descriptor_control_bytes descriptor_backing_bytes \
    descriptor_overlap_opportunities descriptor_next_pass_read_issues \
    descriptor_next_pass_read_responses descriptor_useful_prefetched_lines \
    descriptor_demand_waits_avoided descriptor_prefetch_occupancy_line_cycles \
    descriptor_prefetch_occupancy_hwm descriptor_wasted_prefetched_lines \
    descriptor_boundary_wait_events descriptor_boundary_wait_cycles \
    descriptor_within_pass_wait_events \
    descriptor_within_pass_wait_cycles < <(
    awk '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ /IND_BoundedBucketLineReads$/ { bl += $2 }
        section == 1 && $1 ~ /IND_BoundedBucketWords$/ { bw += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolFilterRetryInspections$/ { fri += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolFinalFlushStalls$/ { ffs += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolBScans$/ { bs += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolResidentPopulations$/ { rp += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolResidentDescriptors$/ { rd += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolExternalDescriptors$/ { ed += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolExternalSegments$/ { es += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolLineWrites$/ { wl += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolWriteBytes$/ { wb += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolWriteAcks$/ { wa += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolLineReads$/ { rl += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolReadBytes$/ { rb += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolWriteCreditStalls$/ { ws += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolReadCreditStalls$/ { rs += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolWriteHighWater$/ { wh += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolStagingEntries$/ { se += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolControlBytes$/ { cb += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolBackingBytes$/ { bb += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolOverlapOpportunities$/ { oo += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolNextPassReadIssues$/ { ni += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolNextPassReadResponses$/ { nr += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolUsefulPrefetchedLines$/ { ul += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolDemandWaitsAvoided$/ { da += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolPrefetchOccupancyLineCycles$/ { oc += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolPrefetchOccupancyHighWater$/ { oh += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolWastedPrefetchedLines$/ { wlost += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolBoundaryDemandWaitEvents$/ { be += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolBoundaryDemandWaitCycles$/ { bc += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolWithinPassDemandWaitEvents$/ { we += $2 }
        section == 1 && $1 ~ /IND_DescriptorSpoolWithinPassDemandWaitCycles$/ { wc += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            print bl + 0, bw + 0, fri + 0, ffs + 0, bs + 0,
                  rp + 0, rd + 0, ed + 0, es + 0,
                  wl + 0, wb + 0, wa + 0,
                  rl + 0, rb + 0, ws + 0, rs + 0, wh + 0,
                  se + 0, cb + 0, bb + 0, oo + 0, ni + 0, nr + 0,
                  ul + 0, da + 0, oc + 0, oh + 0, wlost + 0,
                  be + 0, bc + 0, we + 0, wc + 0
            exit
        }
    ' "$out/run/stats.txt"
)
read -r global_populations global_active_hwm global_descriptor_records \
    global_descriptor_bytes global_sort_read_lines \
    global_sorted_write_lines global_sort_comparisons \
    global_merge_read_lines global_merge_comparisons global_head_hwm \
    global_a_line_issues global_coalesced global_row_groups \
    global_admissions global_retirements global_run_write_acks \
    global_terminal_acks global_fallbacks global_control_bytes \
    global_backing_bytes < <(
    awk '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ /IND_BoundedGlobalMergePopulations$/ { p += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeActiveHWM$/ { ah += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeDescriptorRecords$/ { dr += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeDescriptorBytes$/ { db += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeSortReadLines$/ { sr += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeSortedWriteLines$/ { sw += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeSortComparisons$/ { sc += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeMergeReadLines$/ { mr += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeMergeComparisons$/ { mc += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeMergeHeadHWM$/ { hh += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeALineIssues$/ { ai += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeCoalesced$/ { co += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeRowGroups$/ { rg += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeAdmissions$/ { ad += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeRetirements$/ { rt += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeRunWriteAcks$/ { ra += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeTerminalAcks$/ { ta += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeFallbacks$/ { fb += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeControlBytes$/ { cb += $2 }
        section == 1 && $1 ~ /IND_BoundedGlobalMergeBackingBytes$/ { bb += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            print p + 0, ah + 0, dr + 0, db + 0, sr + 0, sw + 0,
                  sc + 0, mr + 0, mc + 0, hh + 0, ai + 0, co + 0,
                  rg + 0, ad + 0, rt + 0, ra + 0, ta + 0, fb + 0,
                  cb + 0, bb + 0
            exit
        }
    ' "$out/run/stats.txt"
)
descriptor_filter_predicate_retries=0
descriptor_filter_grow_retries=0
descriptor_final_flush_trace_stalls=0
descriptor_unclassified_write_stalls=0
if [[ $index_descriptor_spool -eq 1 ]]; then
    trace="$out/run/virtual_trace.log"
    descriptor_filter_predicate_retries=$(grep -Ec \
        'event=descriptor_spool_filter_retry schema=1 .*source=predicate_bucket .*reason=write_credit$' \
        "$trace" || true)
    descriptor_filter_grow_retries=$(grep -Ec \
        'event=descriptor_spool_filter_retry schema=1 .*source=grow_bucket .*reason=write_credit$' \
        "$trace" || true)
    descriptor_final_flush_trace_stalls=$(grep -Ec \
        'event=descriptor_spool_final_flush_stall schema=1 .*reason=write_credit b_reinspection=0$' \
        "$trace" || true)
    [[ $descriptor_filter_retry_inspections -eq \
       $((descriptor_filter_predicate_retries + \
          descriptor_filter_grow_retries)) && \
       $descriptor_filter_retry_inspections -le $descriptor_write_stalls ]] || {
        echo "unattributed descriptor filter retries: stats=$descriptor_filter_retry_inspections predicate=$descriptor_filter_predicate_retries grow=$descriptor_filter_grow_retries final_flush=$descriptor_final_flush_stalls/$descriptor_final_flush_trace_stalls write_credit_stalls=$descriptor_write_stalls" >&2
        exit 1
    }
    if [[ $descriptor_spool_variant == resident_first ]]; then
        [[ $descriptor_final_flush_stalls -eq \
           $descriptor_final_flush_trace_stalls && \
           $descriptor_write_stalls -eq \
           $((descriptor_filter_retry_inspections + \
              descriptor_final_flush_stalls)) ]] || {
            echo "resident-first final-flush accounting is not closed" >&2
            exit 1
        }
    else
        # The accepted 59ad3fbb reference predates the dedicated final-flush
        # stat. Preserve its retry semantics and expose the residual stalls
        # explicitly instead of misclassifying them as B re-inspections.
        [[ $descriptor_final_flush_stalls -eq 0 && \
           $descriptor_final_flush_trace_stalls -eq 0 && \
           $descriptor_b_scans -eq 0 && \
           $descriptor_resident_populations -eq 0 && \
           $descriptor_resident_descriptors -eq 0 && \
           $descriptor_external_descriptors -eq 0 && \
           $descriptor_external_segments -eq 0 ]] || {
            echo "ab reference unexpectedly reported resident-first counters" >&2
            exit 1
        }
        descriptor_unclassified_write_stalls=$((
            descriptor_write_stalls - descriptor_filter_retry_inspections
        ))
    fi
else
    [[ $descriptor_filter_retry_inspections -eq 0 && \
       $descriptor_final_flush_stalls -eq 0 && \
       $descriptor_b_scans -eq 0 && \
       $descriptor_resident_populations -eq 0 && \
       $descriptor_resident_descriptors -eq 0 && \
       $descriptor_external_descriptors -eq 0 && \
       $descriptor_external_segments -eq 0 ]] || {
        echo "non-descriptor arm reported resident-first spool counters" >&2
        exit 1
    }
fi

global_complete_count=$(grep -Ec \
    'event=bounded_global_merge_complete schema=1 .* populations=4 .* fallback=0 .* mode=timing$' \
    "$out/run/virtual_trace.log" || true)
if [[ $bounded_global_merge -eq 1 ]]; then
    [[ $global_complete_count -eq 1 &&
       $global_populations -eq 4 && $global_active_hwm -le 4096 &&
       $global_descriptor_records -eq 16384 &&
       $global_descriptor_bytes -eq 98304 &&
       $global_sort_read_lines -eq 1152 &&
       $global_sorted_write_lines -eq 1536 &&
       $global_sort_comparisons -gt 0 &&
       $global_merge_read_lines -eq 1536 &&
       $global_merge_comparisons -gt 0 &&
       $global_head_hwm -gt 0 && $global_head_hwm -le 4 &&
       $((global_a_line_issues + global_coalesced)) -eq 16384 &&
       $global_admissions -eq 16384 &&
       $global_retirements -eq 16384 &&
       $global_run_write_acks -eq 1536 &&
       $global_terminal_acks -gt 0 && $global_fallbacks -eq 0 &&
       $global_backing_bytes -eq 98304 ]] || {
        echo "bounded global merge mechanism gate failed" >&2
        exit 1
    }
else
    [[ $global_complete_count -eq 0 && $global_populations -eq 0 &&
       $global_descriptor_records -eq 0 && $global_fallbacks -eq 0 ]] || {
        echo "bounded global merge activated in control case" >&2
        exit 1
    }
fi
read -r dram_reads dram_writes dram_acts dram_pres < <(
    awk '
        $1 == "CH0_num_RD_commands_T:" { rd = $2 }
        $1 ~ /^CH0_num_WR_commands(_T)?:$/ { wr = $2 }
        $1 == "CH0_num_ACT_commands_T:" { act = $2 }
        $1 == "CH0_num_PRE_commands_T:" { pre = $2 }
        END { print rd + 0, wr + 0, act + 0, pre + 0 }
    ' "$out/restore.log"
)
read -r fill_sim_ticks request_sim_ticks < <(
    awk '
        /event=indirect_stage_summary schema=2/ {
            delete value
            for (i = 1; i <= NF; ++i) {
                split($i, kv, "=")
                value[kv[1]] = kv[2]
            }
            fill += value["fill_sim_ticks"]
            request += value["request_sim_ticks"]
        }
        END { print fill + 0, request + 0 }
    ' "$out/run/virtual_trace.log"
)
read -r direct_descriptors direct_producer_acks direct_read_issues \
    direct_read_responses direct_alu_issues direct_alu_completions \
    direct_write_issues direct_write_responses direct_credit_hwm \
    direct_credit_stalls direct_address_stalls direct_retries \
    direct_overlap_ticks direct_active_stage_hwm direct_fallbacks \
    direct_payload_bytes direct_control_bytes < <(
    awk '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 == "system.maa.direct_retirement_descriptors" { d = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_producer_acks" { pa = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_read_issues" { ri = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_read_responses" { rr = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_alu_issues" { ai = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_alu_completions" { ac = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_write_issues" { wi = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_write_responses" { wr = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_credit_high_water" { ch = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_credit_stalls" { cs = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_address_stalls" { as = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_retries" { re = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_overlap_ticks" { ot = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_active_stage_high_water" { ah = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_fallbacks" { fb = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_payload_bytes" { pb = $2 }
        section == 1 && $1 == "system.maa.direct_retirement_control_bytes" { cb = $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            print d + 0, pa + 0, ri + 0, rr + 0, ai + 0, ac + 0,
                  wi + 0, wr + 0, ch + 0, cs + 0, as + 0, re + 0,
                  ot + 0, ah + 0, fb + 0, pb + 0, cb + 0
            exit
        }
    ' "$out/run/stats.txt"
)
if [[ $direct_retirement -eq 1 ]]; then
    [[ $ticks -gt 0 && $insts -gt 0 && $direct_alu_issues -gt 0 && \
       $dram_reads -gt 0 && $fill_sim_ticks -gt 0 && \
       $request_sim_ticks -gt 0 ]] || {
        echo "missing first-ROI direct-retirement activity" >&2
        exit 1
    }
else
    [[ $ticks -gt 0 && $insts -gt 0 && $stream_spd_reads -gt 0 && \
       $stream_writes -gt 0 && $alu_compute -gt 0 && $dram_reads -gt 0 && \
       $fill_sim_ticks -gt 0 && $request_sim_ticks -gt 0 ]] || {
    echo "missing first-ROI performance or consumer activity" >&2
    exit 1
}
fi
if [[ $reload_only -eq 1 ]]; then
    [[ $index_words -eq 0 && $write_issues -eq 0 && \
       $write_completions -eq 0 && $indirect_spd_reads -eq 0 ]] || {
        echo "reload-only window includes gather activity" >&2
        exit 1
    }
    if [[ $case_name == transparent_reload_warm_4k ||
          $case_name == transparent_reload_cold_4k ]]; then
        trace="$out/run/virtual_trace.log"
        transparent_submits=$(grep -c 'event=transparent_submit' "$trace" || true)
        transparent_issues=$(grep -c 'event=transparent_issue' "$trace" || true)
        transparent_completes=$(grep -c 'event=transparent_complete' "$trace" || true)
        transparent_retires=$(grep -c 'event=transparent_retire' "$trace" || true)
        [[ $transparent_submits -eq 1 && $transparent_issues -eq 12 && \
           $transparent_completes -eq 12 && $transparent_retires -eq 1 ]] || {
            echo "invalid reload-only transparent trace: submit=$transparent_submits issue=$transparent_issues complete=$transparent_completes retire=$transparent_retires" >&2
            exit 1
        }
    fi
elif [[ $virtual -eq 1 ]]; then
    [[ $write_issues -gt 0 && $write_issues -eq $write_completions ]] || {
        echo "unbalanced virtual retirement: $write_issues/$write_completions" >&2
        exit 1
    }
    expected_pages=$((16384 / physical))
    [[ $pages_ready -eq $expected_pages && $first_page_cycles -gt 0 && \
       $all_page_cycles -ge $first_page_cycles && \
       $page_span_cycles -eq $((all_page_cycles - first_page_cycles)) && \
       $page_ready_signals -eq $expected_pages ]] || {
        echo "invalid virtual page readiness: pages=$pages_ready/$expected_pages first=$first_page_cycles all=$all_page_cycles span=$page_span_cycles" >&2
        exit 1
    }
    trace="$out/run/virtual_trace.log"
    trace_pages=$(grep -c 'event=page_ready' "$trace" || true)
    [[ $trace_pages -eq $expected_pages ]] || {
        echo "invalid virtual page trace count: $trace_pages/$expected_pages" >&2
        exit 1
    }
    if [[ $direct_retirement -eq 1 ]]; then
        direct_lines=$((16384 * 8 / 64))
        direct_credits=16
        direct_payload=1024
        direct_submits=$(grep -c 'event=direct_retirement_submit schema=1 ' "$trace" || true)
        direct_ack_trace=$(grep -c 'event=direct_retirement_producer_ack schema=1 ' "$trace" || true)
        direct_issue_trace=$(grep -c 'event=direct_retirement_issue schema=1 ' "$trace" || true)
        direct_response_trace=$(grep -c 'event=direct_retirement_response schema=1 ' "$trace" || true)
        direct_alu_issue_trace=$(grep -c 'event=direct_retirement_alu_issue schema=1 ' "$trace" || true)
        direct_alu_complete_trace=$(grep -c 'event=direct_retirement_alu_complete schema=1 ' "$trace" || true)
        direct_summary_trace=$(grep -c 'event=direct_retirement_summary schema=1 ' "$trace" || true)
        direct_retire_trace=$(grep -c 'event=direct_retirement_retire schema=1 ' "$trace" || true)
        read -r trace_payload_bytes trace_control_bytes trace_total_bytes \
            trace_backing_bytes < <(
            awk '
                /event=direct_retirement_submit schema=1/ {
                    delete value
                    for (i = 1; i <= NF; ++i) {
                        split($i, kv, "=")
                        value[kv[1]] = kv[2]
                    }
                    print value["payload_bytes"] + 0,
                          value["control_bytes"] + 0,
                          value["total_bytes"] + 0,
                          value["backing_span_bytes"] + 0
                    exit
                }
            ' "$trace"
        )
        [[ $direct_descriptors -eq 1 && \
           $direct_producer_acks -eq $expected_pages && \
           $direct_read_issues -eq $direct_lines && \
           $direct_read_responses -eq $direct_lines && \
           $direct_alu_issues -eq $direct_lines && \
           $direct_alu_completions -eq $direct_lines && \
           $direct_write_issues -eq $direct_lines && \
           $direct_write_responses -eq $direct_lines && \
           $direct_credit_hwm -eq $direct_credits && \
           $direct_fallbacks -eq 0 && \
           $direct_payload_bytes -eq $direct_payload && \
           $direct_control_bytes -gt 0 && \
           $trace_payload_bytes -eq $direct_payload_bytes && \
           $trace_control_bytes -eq $direct_control_bytes && \
           $trace_total_bytes -eq \
               $((direct_payload_bytes + direct_control_bytes)) && \
           $trace_backing_bytes -eq 131072 && \
           $direct_submits -eq 1 && \
           $direct_ack_trace -eq $expected_pages && \
           $direct_issue_trace -eq $((direct_lines * 2)) && \
           $direct_response_trace -eq $((direct_lines * 2)) && \
           $direct_alu_issue_trace -eq $direct_lines && \
           $direct_alu_complete_trace -eq $direct_lines && \
           $direct_summary_trace -eq 1 && $direct_retire_trace -eq 1 ]] || {
            echo "direct-retirement closure failed: descriptor=$direct_descriptors acks=$direct_producer_acks reads=$direct_read_issues/$direct_read_responses alu=$direct_alu_issues/$direct_alu_completions writes=$direct_write_issues/$direct_write_responses hwm=$direct_credit_hwm fallback=$direct_fallbacks trace=$direct_submits/$direct_ack_trace/$direct_issue_trace/$direct_response_trace/$direct_alu_issue_trace/$direct_alu_complete_trace/$direct_summary_trace/$direct_retire_trace" >&2
            exit 1
        }
        grep -Eq "event=direct_retirement_submit schema=1 .*scope=terminal_fp64_mul_dense_store credits=${direct_credits} payload_bytes=${direct_payload} control_bytes=[1-9][0-9]* total_bytes=[1-9][0-9]* backing_span_bytes=131072 private_page_payload_bytes=0$" "$trace" && \
        grep -Eq "event=direct_retirement_summary schema=1 .*reads=${direct_lines} computes=${direct_lines} writes=${direct_lines} credit_high_water=${direct_credits} .*fallback_count=0$" "$trace" && \
        grep -Eq "event=direct_retirement_retire schema=1 .*final_write_responses=${direct_lines}$" "$trace" || {
            echo "direct-retirement trace lacks exact no-private-payload or final-WriteResp proof" >&2
            exit 1
        }
        {
            printf 'descriptors\tproducer_acks\tread_issues\tread_responses\talu_issues\talu_completions\twrite_issues\twrite_responses\tcredit_hwm\tcredit_stalls\taddress_stalls\tretries\toverlap_ticks\tactive_stage_hwm\tfallbacks\tpayload_bytes\tcontrol_bytes\n'
            printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
                "$direct_descriptors" "$direct_producer_acks" \
                "$direct_read_issues" "$direct_read_responses" \
                "$direct_alu_issues" "$direct_alu_completions" \
                "$direct_write_issues" "$direct_write_responses" \
                "$direct_credit_hwm" "$direct_credit_stalls" \
                "$direct_address_stalls" "$direct_retries" \
                "$direct_overlap_ticks" "$direct_active_stage_hwm" \
                "$direct_fallbacks" "$direct_payload_bytes" \
                "$direct_control_bytes"
        } > "$out/direct_retirement.tsv"
    fi
    if [[ $case_name == transparent_4k ||
          $case_name == transparent_ready_4k ||
          $case_name == transparent_displaced_4k ||
          $case_name == isoarea_serial_4k ||
          $case_name == isoarea_serial_2k ||
          $case_name == isoarea_pingpong_2k ]]; then
        expected_chunks=4
        [[ $transparent_spd_mode -eq 0 ]] || expected_chunks=8
        expected_actions=$((expected_chunks * 3))
        transparent_submits=$(grep -c 'event=transparent_submit' "$trace" || true)
        transparent_issues=$(grep -c 'event=transparent_issue' "$trace" || true)
        transparent_completes=$(grep -c 'event=transparent_complete' "$trace" || true)
        transparent_retires=$(grep -c 'event=transparent_retire' "$trace" || true)
        [[ $transparent_submits -eq 1 && \
           $transparent_issues -eq $expected_actions && \
           $transparent_completes -eq $expected_actions && \
           $transparent_retires -eq 1 ]] || {
            echo "invalid transparent controller trace: submit=$transparent_submits issue=$transparent_issues complete=$transparent_completes retire=$transparent_retires" >&2
            exit 1
        }
        awk '
            /event=transparent_issue/ {
                for (i = 2; i <= NF; ++i) {
                    split($i, kv, "=")
                    value[kv[1]] = kv[2]
                }
                print value["page"], value["action"]
                delete value
            }
        ' OFS='\t' "$trace" > "$out/transparent_issue_order.tsv"
        awk -v chunks="$expected_chunks" '
            { seen[$1 ":" $2]++ }
            END {
                for (p = 0; p < chunks; ++p)
                    for (a = 1; a <= 3; ++a)
                        if (seen[p ":" a] != 1)
                            exit 1
            }
        ' "$out/transparent_issue_order.tsv" || {
            echo "transparent page/action set is incomplete or duplicated" >&2
            exit 1
        }
    fi
    {
        printf 'tick\tunit\tpage\tready_count\ttotal_pages'
        printf '\tissued_words\tcompleted_words\tsources_drained\n'
        awk '
            /event=page_ready/ {
                sub(/:$/, "", $1)
                for (i = 3; i <= NF; ++i) {
                    split($i, kv, "=")
                    value[kv[1]] = kv[2]
                }
                split(value["pages"], pages, "/")
                print $1, value["unit"], value["page"], pages[1], pages[2],
                      value["issued"], value["completed"],
                      value["sources_drained"]
                delete value
                delete pages
            }
        ' OFS='\t' "$trace"
    } > "$out/page_readiness.tsv"
    if [[ $direct -eq 1 ]]; then
        actual_index_partitions=$index_partitions
        expected_index_words=$((16384 * index_partitions))
        if [[ $index_range_policy -eq 3 ]]; then
            actual_index_partitions=$bounded_replay_passes
            if [[ $index_descriptor_spool -eq 1 ]]; then
                expected_index_words=$((bounded_summary_words + bounded_bucket_words))
            else
                expected_index_words=$((bounded_summary_words + bounded_replay_words))
            fi
            [[ $bounded_summary_words -eq 16384 &&
               $bounded_summary_records -gt 0 &&
               $bounded_summary_records -le 64 &&
               $bounded_plan_bytes -gt 0 &&
               $actual_index_partitions -ge 4 &&
               $actual_index_partitions -le $index_partitions &&
               $bounded_replay_max_epoch -le 4096 &&
               $bounded_word_entries -le 4096 &&
               $bounded_offset_entries -le 4096 &&
               $bounded_row_lines -le 4096 &&
               $bounded_row_directories -gt 0 &&
               $bounded_metadata_bytes -gt 0 ]] || {
                echo "invalid adaptive grow-plan counters" >&2
                exit 1
            }
            if [[ $index_descriptor_spool -eq 1 ]]; then
                [[ $bounded_bucket_words -eq 16384 &&
                   $bounded_replay_words -eq 0 &&
                   $bounded_replay_lines -eq 0 &&
                   $((bounded_summary_lines + bounded_bucket_lines)) -eq $index_line_reads &&
                   $descriptor_write_acks -eq $descriptor_write_lines &&
                   $descriptor_read_lines -eq $descriptor_write_lines &&
                   $descriptor_read_bytes -eq $descriptor_write_bytes &&
                   $descriptor_write_hwm -gt 0 &&
                   $descriptor_write_hwm -le 16 &&
                   $descriptor_control_bytes -gt 0 &&
                   $descriptor_control_bytes -le 4096 ]] || {
                    echo "invalid common finite descriptor-spool counters" >&2
                    exit 1
                }
                if [[ $descriptor_spool_variant == resident_first ]]; then
                    [[ $descriptor_b_scans -eq 2 &&
                       $descriptor_resident_populations -eq 1 &&
                       $descriptor_resident_descriptors -eq 4096 &&
                       $descriptor_external_descriptors -eq 12288 &&
                       $descriptor_external_segments -eq 3 &&
                       $descriptor_write_lines -eq 1152 &&
                       $descriptor_write_bytes -eq 73728 &&
                       $descriptor_backing_bytes -eq 73728 &&
                       $descriptor_staging_entries -eq 35 ]] || {
                        echo "invalid resident-first descriptor-spool counters" >&2
                        exit 1
                    }
                else
                    [[ $descriptor_write_lines -eq 2048 &&
                       $descriptor_write_bytes -eq 131072 &&
                       $descriptor_backing_bytes -eq 131328 &&
                       $descriptor_staging_entries -eq 32 ]] || {
                        echo "invalid ab-reference descriptor-spool counters" >&2
                        exit 1
                    }
                fi
                if [[ $descriptor_spool_read_ahead -eq 1 ]]; then
                    [[ $descriptor_overlap_opportunities -eq 3 &&
                       $descriptor_next_pass_read_issues -gt 0 &&
                       $descriptor_next_pass_read_issues -le 12 &&
                       $descriptor_next_pass_read_responses -eq $descriptor_next_pass_read_issues &&
                       $descriptor_useful_prefetched_lines -eq $descriptor_next_pass_read_issues &&
                       $descriptor_demand_waits_avoided -le $descriptor_useful_prefetched_lines &&
                       $descriptor_prefetch_occupancy_line_cycles -gt 0 &&
                       $descriptor_prefetch_occupancy_hwm -gt 0 &&
                       $descriptor_prefetch_occupancy_hwm -le 4 &&
                       $descriptor_wasted_prefetched_lines -eq 0 ]] || {
                        echo "descriptor read-ahead accounting did not close" >&2
                        exit 1
                    }
                else
                    [[ $descriptor_overlap_opportunities -eq 0 &&
                       $descriptor_next_pass_read_issues -eq 0 &&
                       $descriptor_next_pass_read_responses -eq 0 &&
                       $descriptor_useful_prefetched_lines -eq 0 &&
                       $descriptor_demand_waits_avoided -eq 0 &&
                       $descriptor_prefetch_occupancy_line_cycles -eq 0 &&
                       $descriptor_prefetch_occupancy_hwm -eq 0 &&
                       $descriptor_wasted_prefetched_lines -eq 0 ]] || {
                        echo "disabled descriptor read-ahead leaked counters" >&2
                        exit 1
                    }
                fi
            else
                [[ $bounded_bucket_words -eq 0 &&
                   $bounded_replay_words -eq $((16384 * actual_index_partitions)) &&
                   $((bounded_summary_lines + bounded_replay_lines)) -eq $index_line_reads &&
                   $descriptor_write_lines -eq 0 &&
                   $descriptor_read_lines -eq 0 ]] || {
                    echo "invalid adaptive replay counters" >&2
                    exit 1
                }
            fi
            trace="$out/run/virtual_trace.log"
            accepted_plans=$(grep -Ec \
                'event=bounded_grow_summary_complete .*fallback=none plan_result=accepted ' \
                "$trace" || true)
            translated_begins=$(grep -Ec \
                'event=bounded_range_begin .*range_policy=3 key=translated_dram_grow ' \
                "$trace" || true)
            iteration_fallbacks=$(grep -Ec \
                'event=bounded_grow_summary_complete .*fallback=iteration_ranges' \
                "$trace" || true)
            [[ $accepted_plans -eq 1 && $translated_begins -eq 1 && \
               $iteration_fallbacks -eq 0 ]] || {
                echo "adaptive grow plan did not remain physical: accepted=$accepted_plans translated=$translated_begins fallbacks=$iteration_fallbacks" >&2
                exit 1
            }
        fi
        [[ $index_words -eq $expected_index_words && $index_hwm -gt 0 && \
           $index_hwm -le $index_hwm_capacity ]] || {
            echo "invalid bounded index evidence: $index_words/$index_hwm" >&2
            exit 1
        }
        [[ $indirect_spd_reads -eq 0 ]] || {
            echo "direct-index gather used $indirect_spd_reads SPD read cycles" >&2
            exit 1
        }
        if [[ $index_partitions -gt 1 ]]; then
            expected_filter_words=$expected_index_words
            if [[ $index_descriptor_spool -eq 1 ]]; then
                # A full-line write-credit denial deliberately re-examines
                # the same B word. Final staged-line flush stalls do not.
                expected_filter_words=$((expected_filter_words + \
                    descriptor_filter_retry_inspections))
            else
                expected_filter_words=$((expected_filter_words + rt_full + \
                    offset_epoch_drains))
            fi
            [[ $index_filter_words -eq $expected_filter_words ]] || {
                echo "invalid partition-filter inspections: $index_filter_words/$expected_filter_words" >&2
                exit 1
            }
            if [[ $index_filter_words_per_cycle -gt 0 ]]; then
                [[ $index_filter_cycles -gt 0 ]] || {
                    echo "partition filter was configured but charged no cycles" >&2
                    exit 1
                }
                if [[ $require_index_filter_wait -eq 1 ]]; then
                    [[ $index_filter_wait_events -gt 0 && \
                       $index_filter_wait_cycles -gt 0 ]] || {
                        echo "partition filter did not produce a required scheduler wait" >&2
                        exit 1
                    }
                fi
            else
                [[ $index_filter_cycles -eq 0 && \
                   $index_filter_wait_events -eq 0 && \
                   $index_filter_wait_cycles -eq 0 ]] || {
                    echo "unlimited partition filter charged cycles" >&2
                    exit 1
                }
            fi
        else
            [[ $index_filter_words -eq 0 && $index_filter_cycles -eq 0 && \
               $index_filter_wait_events -eq 0 && \
               $index_filter_wait_cycles -eq 0 ]] || {
                echo "single-pass case activated partition filter" >&2
                exit 1
            }
        fi
    else
        [[ $index_words -eq 0 && $indirect_spd_reads -gt 0 ]] || {
            echo "staged-index gather did not use the expected SPD path" >&2
            exit 1
        }
    fi
else
    if [[ $direct -eq 1 ]]; then
        [[ $index_words -eq 16384 && $index_hwm -gt 0 && \
           $index_hwm -le $index_hwm_capacity && \
           $indirect_spd_reads -eq 0 ]] || {
            echo "invalid native direct-index evidence: words=$index_words hwm=$index_hwm spd=$indirect_spd_reads" >&2
            exit 1
        }
    else
        [[ $index_words -eq 0 ]] || {
            echo "native staged case activated direct-index machinery" >&2
            exit 1
        }
    fi
    [[ $write_issues -eq 0 && $write_completions -eq 0 && \
       $pages_ready -eq 0 ]] || {
        echo "native case activated virtual machinery" >&2
        exit 1
    }
fi

feeder_descriptor_discards=0
feeder_predicate_discards=0
feeder_partition_discards=0
feeder_summary_discards=0
if [[ $direct -eq 1 && $reload_only -eq 0 ]]; then
    trace="$out/run/virtual_trace.log"
    feeder_descriptor_discards=$(grep -c \
        'event=index_feeder_discard .*poisoned=1 poison=0xd15ca4d reason=descriptor_inserted private=direct_index_words' \
        "$trace" || true)
    feeder_partition_discards=$(grep -c \
        'event=index_feeder_discard .*poisoned=0 poison=0x0 reason=partition_rejected private=direct_index_words' \
        "$trace" || true)
    feeder_predicate_discards=$(grep -c \
        'event=index_feeder_discard .*poisoned=0 poison=0x0 reason=predicate_rejected private=direct_index_words' \
        "$trace" || true)
    feeder_summary_discards=$(grep -c \
        'event=index_feeder_discard .*poisoned=0 poison=0x0 reason=summary_observed private=direct_index_words' \
        "$trace" || true)
    expected_descriptor_discards=16384
    expected_summary_discards=0
    if [[ $index_range_policy -eq 3 ]]; then
        expected_summary_discards=16384
        if [[ $index_descriptor_spool -eq 1 ]]; then
            # The timed bucket scan consumes all B words. Resident-first's
            # fixed replay decoder does not re-enter the B feeder; the
            # accepted ab reference feeder consumes all records again.
            expected_descriptor_discards=32768
            if [[ $descriptor_spool_variant == resident_first ]]; then
                expected_descriptor_discards=16384
            fi
        fi
    fi
    if [[ $index_descriptor_spool -eq 1 ]]; then
        expected_partition_discards=0
    else
        expected_partition_discards=$((index_words - expected_descriptor_discards - expected_summary_discards))
    fi
    [[ $feeder_descriptor_discards -eq $expected_descriptor_discards && \
       $feeder_predicate_discards -eq 0 && \
       $feeder_summary_discards -eq $expected_summary_discards && \
       $feeder_partition_discards -eq $expected_partition_discards ]] || {
        echo "invalid private index-feeder discard evidence: inserted=$feeder_descriptor_discards/$expected_descriptor_discards summary=$feeder_summary_discards/$expected_summary_discards predicate=$feeder_predicate_discards/0 partition=$feeder_partition_discards/$expected_partition_discards" >&2
        exit 1
    }
fi

physical_records=0
physical_record_sha256=none
bounded_summary_histogram_sha256=none
if [[ $index_range_policy -eq 3 ]]; then
    trace="$out/run/virtual_trace.log"
    awk '
        /event=bounded_grow_histogram_record/ {
            delete value
            for (i = 1; i <= NF; ++i) {
                split($i, kv, "=")
                value[kv[1]] = kv[2]
            }
            print value["grow"], value["count"]
        }
    ' OFS='\t' "$trace" | sort -n -k1,1 \
        > "$out/translated_grow_histogram.tsv"
    read -r histogram_records histogram_population < <(
        awk '{ records++; population += $2 }
             END { print records + 0, population + 0 }' \
            "$out/translated_grow_histogram.tsv"
    )
    histogram_unique=$(awk '
        NR > 1 && $1 == previous { duplicate = 1 }
        { previous = $1 }
        END { print duplicate ? 0 : 1 }
    ' "$out/translated_grow_histogram.tsv")
    [[ $histogram_records -eq $bounded_summary_records && \
       $histogram_population -eq 16384 && $histogram_unique -eq 1 ]] || {
        echo "invalid translated-grow histogram: records=$histogram_records/$bounded_summary_records population=$histogram_population/16384 unique=$histogram_unique" >&2
        exit 1
    }
    bounded_summary_histogram_sha256=$(sha256sum \
        "$out/translated_grow_histogram.tsv" | awk '{ print $1 }')
fi
if [[ $require_physical_trace -eq 1 ]]; then
    python3 "$root/experiments/analysis/hybrid_overhead_attribution.py" \
        validate-physical "$out/run/virtual_trace.log" \
        --expected-records 16384 --aperture-slices 16 \
        --records-output "$out/physical_admission_records.jsonl" \
        --output "$out/physical_validation.json"
    physical_records=16384
    physical_record_sha256=$(sed -nE \
        's/^  "record_sha256": "([0-9a-f]{64})",$/\1/p' \
        "$out/physical_validation.json")
    [[ ${#physical_record_sha256} -eq 64 ]] || {
        echo "physical-record validator did not emit a hash" >&2
        exit 1
    }
fi
if [[ $index_range_policy -eq 3 ]]; then
    [[ $require_physical_trace -eq 1 && $physical_records -eq 16384 && \
       ${#bounded_summary_histogram_sha256} -eq 64 ]] || {
        echo "physical grow arm lacks authenticated admissions or histogram" >&2
        exit 1
    }
fi

source_issue_records=0
source_issue_requests=0
source_issue_sha256=none
if [[ $require_source_issue_digest -eq 1 ]]; then
    awk '
        / unit=[0-9]+ instruction_tick=[0-9]+ count=[0-9]+ fnv=0x[[:xdigit:]]+ mix=0x[[:xdigit:]]+$/ {
            delete value
            for (i = 1; i <= NF; ++i) {
                split($i, kv, "=")
                if (kv[1] == "count" || kv[1] == "fnv" || kv[1] == "mix")
                    value[kv[1]] = kv[2]
            }
            if (value["count"] == "" || value["fnv"] == "" ||
                value["mix"] == "")
                exit 2
            print value["count"], value["fnv"], value["mix"]
        }
    ' OFS='\t' "$out/run/virtual_trace.log" | LC_ALL=C sort \
        > "$out/source_issue_multiset.tsv"
    source_issue_records=$(wc -l < "$out/source_issue_multiset.tsv")
    source_issue_requests=$(awk '{ total += $1 } END { print total + 0 }' \
        "$out/source_issue_multiset.tsv")
    [[ $source_issue_records -gt 0 && $source_issue_requests -gt 0 ]] || {
        echo "source-issue digest evidence is empty" >&2
        exit 1
    }
    source_issue_sha256=$(sha256sum "$out/source_issue_multiset.tsv" | \
        awk '{ print $1 }')
fi

if [[ $overlap -eq 1 ]]; then
    [[ $page_wait_reads -eq $pages_ready && \
       $page_wait_responses -eq $pages_ready && \
       $page_wait_deferrals -gt 0 ]] || {
        echo "invalid virtual page waits: reads=$page_wait_reads deferrals=$page_wait_deferrals responses=$page_wait_responses pages=$pages_ready" >&2
        exit 1
    }
else
    [[ $page_wait_reads -eq 0 && $page_wait_deferrals -eq 0 && \
       $page_wait_responses -eq 0 ]] || {
        echo "unexpected virtual page waits in non-overlap case" >&2
        exit 1
    }
fi

headers=(case output_hash simTicks fill_sim_ticks request_sim_ticks
    fill_cycles request_cycles simInsts index_line_reads index_words
    index_hwm feeder_descriptor_discards feeder_predicate_discards
    feeder_partition_discards feeder_summary_discards
    physical_records physical_record_sha256 bounded_summary_histogram_sha256
    source_issue_records source_issue_requests source_issue_sha256
    index_filter_words index_filter_cycles index_filter_wait_events
    index_filter_wait_cycles descriptor_spool_filter_retry_inspections
    descriptor_spool_filter_predicate_retries
    descriptor_spool_filter_grow_retries
    descriptor_spool_final_flush_stalls
    descriptor_spool_unclassified_write_stalls write_issues
    write_completions indirect_spd_reads pages_ready
    pages_ready_before_source_drain first_page_ready_cycles
    all_pages_ready_cycles page_ready_span_cycles stream_spd_reads
    stream_writes alu_compute_cycles page_ready_signals page_wait_reads
    page_wait_deferrals page_wait_responses l3_read_hits_maa
    l3_read_misses_maa l3_write_requests_maa memory_bytes_read_maa
    memory_bytes_written_maa cpu_cycles row_table_slices
    row_table_rows_per_slice row_table_entries_per_subslice_row
    virtual_grow_order virtual_index_partitions virtual_index_range_passes
    virtual_index_range_policy virtual_index_descriptor_spool
    virtual_descriptor_spool_read_ahead
    virtual_bounded_global_merge
    descriptor_spool_variant
    virtual_index_range_boundaries
    virtual_index_force_cache virtual_partition_keep_combiner
    offset_table_entries offset_table_epoch_entries
    transparent_spd_mode
    virtual_index_filter_words_per_cycle require_index_filter_wait
    response_slots response_word_pool
    virtual_words_per_cycle virtual_max_outstanding_writes
    row_table_cache_lines
    row_table_rows_inserted row_table_unique_cache_lines
    row_table_unique_rows source_reads response_slot_hwm response_word_hwm
    response_pool_stalls row_table_full_events offset_epoch_drains
    virtual_build_rounds dram_reads dram_writes
    dram_activates dram_precharges bounded_summary_line_reads
    bounded_summary_words bounded_summary_records bounded_summary_hash_probes
    bounded_summary_reduction_visits bounded_summary_plan_bytes
    bounded_replay_line_reads bounded_replay_words bounded_replay_passes
    bounded_replay_drains bounded_replay_max_epoch_admissions
    bounded_word_entries bounded_offset_entries bounded_row_directory_entries
    bounded_row_line_entries bounded_reorder_metadata_bytes
    bounded_bucket_line_reads bounded_bucket_words
    descriptor_spool_b_scans descriptor_spool_resident_populations
    descriptor_spool_resident_descriptors
    descriptor_spool_external_descriptors descriptor_spool_external_segments
    descriptor_spool_line_writes descriptor_spool_write_bytes
    descriptor_spool_write_acks descriptor_spool_line_reads
    descriptor_spool_read_bytes descriptor_spool_write_credit_stalls
    descriptor_spool_read_credit_stalls descriptor_spool_write_high_water
    descriptor_spool_staging_entries descriptor_spool_control_bytes
    descriptor_spool_backing_bytes descriptor_spool_overlap_opportunities
    descriptor_spool_next_pass_read_issues
    descriptor_spool_next_pass_read_responses
    descriptor_spool_useful_prefetched_lines
    descriptor_spool_demand_waits_avoided
    descriptor_spool_prefetch_occupancy_line_cycles
    descriptor_spool_prefetch_occupancy_high_water
    descriptor_spool_wasted_prefetched_lines
    descriptor_spool_boundary_demand_wait_events
    descriptor_spool_boundary_demand_wait_cycles
    descriptor_spool_within_pass_demand_wait_events
    descriptor_spool_within_pass_demand_wait_cycles
    bounded_global_populations bounded_global_active_hwm
    bounded_global_descriptor_records bounded_global_descriptor_bytes
    bounded_global_sort_read_lines bounded_global_sorted_write_lines
    bounded_global_sort_comparisons bounded_global_merge_read_lines
    bounded_global_merge_comparisons bounded_global_head_hwm
    bounded_global_a_line_issues bounded_global_coalesced
    bounded_global_row_groups bounded_global_admissions
    bounded_global_retirements bounded_global_run_write_acks
    bounded_global_terminal_acks bounded_global_fallbacks
    bounded_global_control_bytes bounded_global_backing_bytes)
values=("$case_name" "$output_hash" "$ticks" "$fill_sim_ticks"
    "$request_sim_ticks" "$fill_cycles" "$request_cycles" "$insts" "$index_line_reads"
    "$index_words" "$index_hwm" "$feeder_descriptor_discards"
    "$feeder_predicate_discards" "$feeder_partition_discards"
    "$feeder_summary_discards"
    "$physical_records" "$physical_record_sha256"
    "$bounded_summary_histogram_sha256"
    "$source_issue_records" "$source_issue_requests" "$source_issue_sha256"
    "$index_filter_words" "$index_filter_cycles"
    "$index_filter_wait_events" "$index_filter_wait_cycles"
    "$descriptor_filter_retry_inspections"
    "$descriptor_filter_predicate_retries"
    "$descriptor_filter_grow_retries"
    "$descriptor_final_flush_stalls"
    "$descriptor_unclassified_write_stalls"
    "$write_issues" "$write_completions"
    "$indirect_spd_reads" "$pages_ready" "$pages_ready_early"
    "$first_page_cycles" "$all_page_cycles" "$page_span_cycles"
    "$stream_spd_reads" "$stream_writes" "$alu_compute"
    "$page_ready_signals" "$page_wait_reads" "$page_wait_deferrals"
    "$page_wait_responses" "$l3_read_hits" "$l3_read_misses"
    "$l3_write_requests" "$memory_bytes_read" "$memory_bytes_written"
    "$cpu_cycles" "$row_slices" "$row_rows"
    "$row_entries" "$grow_order" "$index_partitions" "$index_range_passes"
    "$index_range_policy" "$index_descriptor_spool"
    "$descriptor_spool_read_ahead"
    "$bounded_global_merge"
    "$descriptor_spool_variant"
    "${index_range_boundaries:-none}"
    "$index_force_cache" "$partition_keep_combiner" \
    "$resolved_offset_entries" "$resolved_offset_epoch_entries"
    "$transparent_spd_mode"
    "$index_filter_words_per_cycle" "$require_index_filter_wait"
    "$response_slots" "$response_word_pool"
    "$words_per_cycle" "$max_outstanding_writes"
    "$rt_cache_lines" "$rt_rows" "$rt_unique_cache_lines" "$rt_unique_rows"
    "$source_reads" "$response_slot_hwm" "$response_word_hwm"
    "$response_pool_stalls" "$rt_full" "$offset_epoch_drains"
    "$build_rounds" "$dram_reads" "$dram_writes"
    "$dram_acts" "$dram_pres" "$bounded_summary_lines"
    "$bounded_summary_words" "$bounded_summary_records"
    "$bounded_summary_probes" "$bounded_summary_visits"
    "$bounded_plan_bytes" "$bounded_replay_lines" "$bounded_replay_words"
    "$bounded_replay_passes" "$bounded_replay_drains"
    "$bounded_replay_max_epoch" "$bounded_word_entries"
    "$bounded_offset_entries" "$bounded_row_directories"
    "$bounded_row_lines" "$bounded_metadata_bytes"
    "$bounded_bucket_lines" "$bounded_bucket_words"
    "$descriptor_b_scans" "$descriptor_resident_populations"
    "$descriptor_resident_descriptors" "$descriptor_external_descriptors"
    "$descriptor_external_segments"
    "$descriptor_write_lines" "$descriptor_write_bytes"
    "$descriptor_write_acks" "$descriptor_read_lines"
    "$descriptor_read_bytes" "$descriptor_write_stalls"
    "$descriptor_read_stalls" "$descriptor_write_hwm"
    "$descriptor_staging_entries" "$descriptor_control_bytes"
    "$descriptor_backing_bytes" "$descriptor_overlap_opportunities"
    "$descriptor_next_pass_read_issues"
    "$descriptor_next_pass_read_responses"
    "$descriptor_useful_prefetched_lines"
    "$descriptor_demand_waits_avoided"
    "$descriptor_prefetch_occupancy_line_cycles"
    "$descriptor_prefetch_occupancy_hwm"
    "$descriptor_wasted_prefetched_lines"
    "$descriptor_boundary_wait_events" "$descriptor_boundary_wait_cycles"
    "$descriptor_within_pass_wait_events"
    "$descriptor_within_pass_wait_cycles"
    "$global_populations" "$global_active_hwm"
    "$global_descriptor_records" "$global_descriptor_bytes"
    "$global_sort_read_lines" "$global_sorted_write_lines"
    "$global_sort_comparisons" "$global_merge_read_lines"
    "$global_merge_comparisons" "$global_head_hwm"
    "$global_a_line_issues" "$global_coalesced" "$global_row_groups"
    "$global_admissions" "$global_retirements" "$global_run_write_acks"
    "$global_terminal_acks" "$global_fallbacks" "$global_control_bytes"
    "$global_backing_bytes")
if [[ $index_range_passes -eq 1 ]]; then
    actual_index_partitions=$index_partitions
    expected_index_words=$((16384 * index_partitions))
    expected_partition_discards=$((16384 * (index_partitions - 1)))
    expected_descriptor_discards=16384
    range_backing=llc_index_rescan
    range_begin_schema=1
    if [[ $index_range_policy -eq 3 ]]; then
        actual_index_partitions=$bounded_replay_passes
        range_begin_schema=2
        if [[ $index_descriptor_spool -eq 1 ]]; then
            expected_index_words=$((bounded_summary_words + bounded_bucket_words))
            expected_partition_discards=0
            expected_descriptor_discards=32768
            if [[ $descriptor_spool_variant == resident_first ]]; then
                expected_descriptor_discards=16384
            fi
            range_backing=llc_descriptor_spool
        else
            expected_index_words=$((bounded_summary_words + bounded_replay_words))
            expected_partition_discards=$((bounded_replay_words - 16384))
        fi
    fi
    range_begin_count=$(grep -Ec \
        "event=bounded_range_begin schema=${range_begin_schema} .* logical=16384 .* backing=${range_backing} .*combiner=retained" \
        "$out/run/virtual_trace.log" || true)
    range_pass_count=$(grep -Ec \
        'event=bounded_range_pass_complete schema=1 ' \
        "$out/run/virtual_trace.log" || true)
    range_complete_count=$(grep -Ec \
        'event=bounded_range_complete schema=1 .* logical=16384 admitted=16384 retired=16384 duplicate_admissions=0 duplicate_retirements=0 missing=0 ' \
        "$out/run/virtual_trace.log" || true)
    uncached_index_responses=$(grep -Ec \
        'event=index_line_response schema=2 .* cached=0$' \
        "$out/run/virtual_trace.log" || true)
    descriptor_response_pattern='event=descriptor_spool_read_response schema=1 .* cached=1$'
    uncached_descriptor_response_pattern='event=descriptor_spool_read_response schema=1 .* cached=0$'
    descriptor_complete_pattern='event=descriptor_spool_complete schema=1 .* descriptors=16384 write_lines=2048 write_acks=2048 read_lines=2048 read_responses=2048 .* staging_entries=32 .* fallback=none$'
    if [[ $descriptor_spool_variant == resident_first ]]; then
        descriptor_response_pattern='event=descriptor_spool_read_response schema=2 .* cached=1 mode=(demand|next_pass_read_ahead) before_demand=[01]$'
        uncached_descriptor_response_pattern='event=descriptor_spool_read_response schema=2 .* cached=0 mode=(demand|next_pass_read_ahead) before_demand=[01]$'
        descriptor_complete_pattern='event=descriptor_spool_complete schema=2 .* b_scans=2 descriptors=16384 resident_pass=0 resident_descriptors=4096 external_descriptors=12288 external_segments=3 descriptor_bytes=6 payload_bytes=73728 write_lines=1152 write_acks=1152 read_lines=1152 read_responses=1152 .* prefetch_occupancy=0 .* wasted_lines=0 .* fallback=none$'
    fi
    descriptor_response_count=$(grep -Ec \
        "$descriptor_response_pattern" \
        "$out/run/virtual_trace.log" || true)
    uncached_descriptor_responses=$(grep -Ec \
        "$uncached_descriptor_response_pattern" \
        "$out/run/virtual_trace.log" || true)
    descriptor_complete_count=$(grep -Ec \
        "$descriptor_complete_pattern" \
        "$out/run/virtual_trace.log" || true)
    expected_descriptor_complete_count=$index_descriptor_spool
    [[ $resolved_offset_entries -gt 0 &&
       $resolved_offset_entries -le 4096 &&
       $resolved_offset_epoch_entries -gt 0 &&
       $resolved_offset_epoch_entries -le 4096 &&
       $((row_slices * row_rows * row_entries)) -le 4096 &&
       $index_force_cache -eq 1 && $partition_keep_combiner -eq 1 &&
       $grow_order -eq 1 &&
       $index_words -eq $expected_index_words &&
       $feeder_descriptor_discards -eq $expected_descriptor_discards &&
       $feeder_predicate_discards -eq 0 &&
       $feeder_partition_discards -eq $expected_partition_discards &&
       $range_begin_count -eq 1 &&
       $range_pass_count -eq $actual_index_partitions &&
       $range_complete_count -eq 1 &&
       $descriptor_complete_count -eq $expected_descriptor_complete_count &&
       $descriptor_response_count -eq $descriptor_read_lines &&
       $uncached_descriptor_responses -eq 0 &&
       $uncached_index_responses -eq 0 ]] || {
        echo "bounded range-pass closure failed" >&2
        exit 1
    }
fi
[[ ${#headers[@]} -eq ${#values[@]} ]] || {
    echo "result schema/value length mismatch" >&2
    exit 1
}
{
    (IFS=$'\t'; echo "${headers[*]}")
    (IFS=$'\t'; echo "${values[*]}")
} > "$out/result.tsv"
if [[ -z $shared_checkpoint ]]; then
    (
        cd "$checkpoint_dir"
        find . -type f -print0 | sort -z | xargs -0 sha256sum
    ) > "$out/checkpoint_files.sha256"
    checkpoint_digest=$(sha256sum "$out/checkpoint_files.sha256" |
        awk '{print $1}')
    printf '%s  checkpoint_files.sha256\n' "$checkpoint_digest" \
        > "$out/checkpoint_identity.sha256"
else
    cp -- "$out/shared_checkpoint_identity.sha256" \
        "$out/checkpoint_identity.sha256"
fi
run_artifacts=(
    "$out/result.tsv"
    "$out/restore.log"
    "$out/run/stats.txt"
    "$out/run/virtual_trace.log"
    "$out/checkpoint_identity.sha256"
)
if [[ $direct_retirement -eq 1 ]]; then
    run_artifacts+=("$out/direct_retirement.tsv")
fi
sha256sum "${run_artifacts[@]}" > "$out/run_artifact_sha256.txt"
touch "$out/virtual_tile_consumer_case.pass"
cat "$out/result.tsv"
