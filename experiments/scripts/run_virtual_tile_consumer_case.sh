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
debug_flags=${MAA_DEBUG_FLAGS:-MAAVirtualTrace}
require_physical_trace=${MAA_REQUIRE_PHYSICAL_RECORD_TRACE:-0}
shared_checkpoint=${DX100_SHARED_CHECKPOINT_DIR:-}
shared_selector=${DX100_SHARED_TREATMENT_FILE:-}
frozen_ramulator_library=${DX100_FROZEN_RAMULATOR_LIBRARY:-}
ramulator_provenance=${DX100_RAMULATOR_PROVENANCE_FILE:-}
grow_order=${MAA_VIRTUAL_GROW_ORDER:-0}
row_slices=${MAA_ROW_TABLE_SLICES:-16}
row_rows=${MAA_ROW_TABLE_ROWS_PER_SLICE:-64}
row_entries=${MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW:-8}
offset_entries=${MAA_OFFSET_TABLE_ENTRIES:-0}
offset_epoch_entries=${MAA_OFFSET_TABLE_EPOCH_ENTRIES:-0}
response_slots=${MAA_VIRTUAL_RESPONSE_SLOTS:-96}
response_word_pool=${MAA_VIRTUAL_RESPONSE_WORD_POOL:-480}
combine_slots=${MAA_VIRTUAL_COMBINE_SLOTS:-384}
combine_words=${MAA_VIRTUAL_COMBINE_WORDS:-4096}
combine_ways=${MAA_VIRTUAL_COMBINE_WAYS:-4}
combine_victim_policy=${MAA_VIRTUAL_COMBINE_VICTIM_POLICY:-0}
combine_banks=${MAA_VIRTUAL_COMBINE_BANKS:-0}
index_partitions=${MAA_VIRTUAL_INDEX_PARTITIONS:-1}
index_range_passes=${MAA_VIRTUAL_INDEX_RANGE_PASSES:-0}
index_force_cache=${MAA_VIRTUAL_INDEX_FORCE_CACHE:-0}
partition_keep_combiner=${MAA_VIRTUAL_PARTITION_KEEP_COMBINER:-0}
index_filter_words_per_cycle=${MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE:-4}
require_index_filter_wait=${MAA_REQUIRE_INDEX_FILTER_WAIT:-0}
[[ $grow_order == 0 || $grow_order == 1 ]] || {
    echo "MAA_VIRTUAL_GROW_ORDER must be 0 or 1" >&2
    exit 2
}
[[ $row_slices -gt 0 && $row_rows -gt 0 && $row_entries -gt 0 ]] || {
    echo "row-table dimensions must be positive" >&2
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
index_cache_args=()
if [[ $index_force_cache == 1 ]]; then
    index_cache_args+=(--maa_virtual_index_force_cache)
fi
partition_combiner_args=()
if [[ $partition_keep_combiner == 1 ]]; then
    partition_combiner_args+=(--maa_virtual_partition_keep_combiner)
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
    printf 'row_table_slices=%s\n' "$row_slices"
    printf 'row_table_rows_per_slice=%s\n' "$row_rows"
    printf 'row_table_entries_per_subslice_row=%s\n' "$row_entries"
    printf 'offset_table_entries=%s\n' "$offset_entries"
    printf 'offset_table_epoch_entries=%s\n' "$offset_epoch_entries"
    printf 'virtual_grow_order=%s\n' "$grow_order"
    printf 'virtual_response_slots=%s\n' "$response_slots"
    printf 'virtual_response_word_pool=%s\n' "$response_word_pool"
    printf 'virtual_combine_slots=%s\n' "$combine_slots"
    printf 'virtual_combine_words=%s\n' "$combine_words"
    printf 'virtual_combine_ways=%s\n' "$combine_ways"
    printf 'virtual_combine_victim_policy=%s\n' "$combine_victim_policy"
    printf 'virtual_combine_banks=%s\n' "$combine_banks"
    printf 'virtual_index_partitions=%s\n' "$index_partitions"
    printf 'virtual_index_range_passes=%s\n' "$index_range_passes"
    printf 'virtual_index_force_cache=%s\n' "$index_force_cache"
    printf 'virtual_partition_keep_combiner=%s\n' \
        "$partition_keep_combiner"
    printf 'virtual_index_filter_words_per_cycle=%s\n' \
        "$index_filter_words_per_cycle"
    printf 'require_index_filter_wait=%s\n' "$require_index_filter_wait"
    printf 'cache_pollution_bytes=%s\n' \
        "$((polluted * 32 * 1024 * 1024))"
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'baseline_commit=d7875f99e6caf1d47bd6010b89112458384aec6c\n'
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'timeout=none\n'
    printf 'shared_checkpoint=%s\n' "${shared_checkpoint:-none}"
    printf 'shared_treatment_file=%s\n' "${shared_selector:-none}"
    printf 'physical_record_schema=dx100.physical_admission.v1\n'
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
cp -- "$root/src/mem/MAA/TransparentSPDController.hh" \
    "$snapshot/TransparentSPDController.hh"
cp -- "$root/src/mem/MAA/MAA.cc" "$snapshot/MAA.cc"
cp -- "$root/src/mem/MAA/MAA.hh" "$snapshot/MAA.hh"
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
cp -- "$root/experiments/analysis/hybrid_overhead_attribution.py" \
    "$snapshot/hybrid_overhead_attribution.py"
{
    printf 'MAA_DEBUG_FLAGS=%q ' "$debug_flags"
    printf 'MAA_REQUIRE_PHYSICAL_RECORD_TRACE=%q ' "$require_physical_trace"
    printf 'DX100_SHARED_CHECKPOINT_DIR=%q ' "${shared_checkpoint:-}"
    printf 'DX100_SHARED_TREATMENT_FILE=%q ' "${shared_selector:-}"
    printf 'DX100_FROZEN_RAMULATOR_LIBRARY=%q ' "$ramulator_library"
    printf 'DX100_RAMULATOR_PROVENANCE_FILE=%q ' "$ramulator_provenance"
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
    "$snapshot/TransparentSPDController.hh" \
    "$snapshot/MAA.cc" "$snapshot/MAA.hh" \
    "$snapshot/IF.cc" "$snapshot/IF.hh" \
    "$snapshot/StreamAccess.cc" "$snapshot/StreamAccess.hh" \
    "$snapshot/ALU.cc" "$snapshot/ALU.hh" \
    "$snapshot/MAA.py" "$snapshot/MAAConfig.py" "$snapshot/Options.py" \
    "$snapshot/hybrid_overhead_attribution.py" \
    "$snapshot/CpuSidePort.cc" \
    "$out/source.diff" "$out/source_status.txt" "$out/invocation.sh.txt" \
    > "$out/artifact_sha256.txt"

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
    printf '%s %s\n' "$mode" "$page" > "$shared_selector"
    cp -- "$shared_selector" "$out/treatment.txt"
    (
        cd "$shared_checkpoint"
        find . -type f -print0 | sort -z | xargs -0 sha256sum
    ) > "$out/shared_checkpoint_files.sha256"
    sha256sum "$out/shared_checkpoint_files.sha256" \
        > "$out/shared_checkpoint_identity.sha256"
    printf '%s\n' "$shared_checkpoint" > "$out/checkpoint.path"
    printf '0\n' > "$out/checkpoint.exit"
fi

layout_log="$out/checkpoint.log"
layout_mode="$mode"
layout_page="$page"
if [[ -n $shared_checkpoint ]]; then
    layout_log="$(dirname "$shared_checkpoint")/shared-checkpoint.log"
    layout_mode=deferred
    layout_page=0
fi
isoarea_validate_layout "$layout_log" "$layout_mode" "$layout_page" || {
    echo "binary/config consumer contract mismatch" >&2
    exit 1
}

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
    --maa_virtual_words_per_cycle=4 \
    --maa_virtual_max_outstanding_writes=64 --maa_virtual_masked_writes \
    --maa_virtual_index_buffer_lines=4 \
    --maa_virtual_index_partitions="$index_partitions" \
    "${index_range_args[@]}" \
    "${index_cache_args[@]}" \
    "${partition_combiner_args[@]}" \
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
for expected in \
    "transparent_spd_mode=$transparent_spd_mode" \
    "num_initial_row_table_slices=$row_slices" \
    "num_row_table_rows_per_slice=$row_rows" \
    "num_row_table_entries_per_subslice_row=$row_entries" \
    "num_offset_table_entries=$resolved_offset_entries" \
    "num_offset_table_epoch_entries=$resolved_offset_epoch_entries" \
    "virtual_index_partitions=$index_partitions" \
    "virtual_index_range_passes=$([[ $index_range_passes -eq 1 ]] && echo true || echo false)" \
    "virtual_index_force_cache=$([[ $index_force_cache -eq 1 ]] && echo true || echo false)" \
    "virtual_partition_keep_combiner=$([[ $partition_keep_combiner -eq 1 ]] && echo true || echo false)" \
    "virtual_index_filter_words_per_cycle=$index_filter_words_per_cycle" \
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
[[ $result_count -eq 1 && $roi_count -eq 1 && $fatal_count -eq 0 ]] || {
    printf 'invalid completion: result=%s roi=%s fatal=%s\n' \
        "$result_count" "$roi_count" "$fatal_count" >&2
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
    l3_read_hits l3_read_misses memory_bytes_read cpu_cycles \
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
        section == 1 && $1 == "system.mem_ctrls.bytesRead::maa" { mb = $2 }
        section == 1 && $1 == "system.switch_cpus0.numCycles" { cc = $2 }
        section == 1 && $1 ~ /IND_NumCacheLineInserted$/ { rc += $2 }
        section == 1 && $1 ~ /IND_NumRowsInserted$/ { rr += $2 }
        section == 1 && $1 ~ /IND_NumUniqueCacheLineInserted$/ { ruc += $2 }
        section == 1 && $1 ~ /IND_NumUniqueRowsInserted$/ { rur += $2 }
        section == 1 && $1 ~ /IND_LoadsCacheHitResponding$/ { lc += $2 }
        section == 1 && $1 ~ /IND_LoadsCacheHitAccessing$/ { la += $2 }
        section == 1 && $1 ~ /IND_LoadsMemAccessing$/ { lmema += $2 }
        section == 1 && $1 ~ /IND_VirtResponseSlotHighWater$/ { rsh += $2 }
        section == 1 && $1 ~ /IND_VirtResponseWordHighWater$/ { rwh += $2 }
        section == 1 && $1 ~ /IND_VirtResponseWordPoolStalls$/ { rps += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            print ticks + 0, insts + 0, il + 0, iw + 0, hw + 0,
                  ifw + 0, ifc + 0, ife + 0, ifx + 0,
                  wi + 0, wc + 0, pr + 0, pe + 0, pf + 0, pa + 0,
                  ps + 0, ir + 0, sr + 0, sw + 0, ac + 0,
                  prs + 0, pwr + 0, pwd + 0, pws + 0,
                  lh + 0, lm + 0, mb + 0, cc + 0, rc + 0,
                  rr + 0, ruc + 0, rur + 0,
                  lc + la + lmema - il, rsh + 0, rwh + 0, rps + 0
            exit
        }
    ' "$out/run/stats.txt"
)
read -r rt_full build_rounds < <(
    awk '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ /IND_NumRTFull$/ { rf += $2 }
        section == 1 && $1 ~ /IND_VirtBuildRounds$/ { br += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            print rf + 0, br + 0
            exit
        }
    ' "$out/run/stats.txt"
)
read -r dram_reads dram_acts dram_pres < <(
    awk '
        $1 == "CH0_num_RD_commands_T:" { rd = $2 }
        $1 == "CH0_num_ACT_commands_T:" { act = $2 }
        $1 == "CH0_num_PRE_commands_T:" { pre = $2 }
        END { print rd + 0, act + 0, pre + 0 }
    ' "$out/restore.log"
)
[[ $ticks -gt 0 && $insts -gt 0 && $stream_spd_reads -gt 0 && \
   $stream_writes -gt 0 && $alu_compute -gt 0 && $dram_reads -gt 0 ]] || {
    echo "missing first-ROI performance or consumer activity" >&2
    exit 1
}
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
        expected_index_words=$((16384 * index_partitions))
        [[ $index_words -eq $expected_index_words && $index_hwm -gt 0 && $index_hwm -le 64 ]] || {
            echo "invalid bounded index evidence: $index_words/$index_hwm" >&2
            exit 1
        }
        [[ $indirect_spd_reads -eq 0 ]] || {
            echo "direct-index gather used $indirect_spd_reads SPD read cycles" >&2
            exit 1
        }
        if [[ $index_partitions -gt 1 ]]; then
            expected_filter_words=$((expected_index_words + rt_full))
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
           $index_hwm -le 64 && $indirect_spd_reads -eq 0 ]] || {
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
    expected_descriptor_discards=16384
    expected_partition_discards=$((index_words - expected_descriptor_discards))
    [[ $feeder_descriptor_discards -eq $expected_descriptor_discards && \
       $feeder_predicate_discards -eq 0 && \
       $feeder_partition_discards -eq $expected_partition_discards ]] || {
        echo "invalid private index-feeder discard evidence: inserted=$feeder_descriptor_discards/$expected_descriptor_discards predicate=$feeder_predicate_discards/0 partition=$feeder_partition_discards/$expected_partition_discards" >&2
        exit 1
    }
fi

physical_records=0
physical_record_sha256=none
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

headers=(case output_hash simTicks simInsts index_line_reads index_words
    index_hwm feeder_descriptor_discards feeder_predicate_discards
    feeder_partition_discards physical_records physical_record_sha256
    index_filter_words index_filter_cycles index_filter_wait_events
    index_filter_wait_cycles write_issues
    write_completions indirect_spd_reads pages_ready
    pages_ready_before_source_drain first_page_ready_cycles
    all_pages_ready_cycles page_ready_span_cycles stream_spd_reads
    stream_writes alu_compute_cycles page_ready_signals page_wait_reads
    page_wait_deferrals page_wait_responses l3_read_hits_maa
    l3_read_misses_maa memory_bytes_read_maa cpu_cycles row_table_slices
    row_table_rows_per_slice row_table_entries_per_subslice_row
    virtual_grow_order virtual_index_partitions virtual_index_range_passes
    virtual_index_force_cache virtual_partition_keep_combiner
    offset_table_entries offset_table_epoch_entries
    transparent_spd_mode
    virtual_index_filter_words_per_cycle require_index_filter_wait
    response_slots response_word_pool
    row_table_cache_lines
    row_table_rows_inserted row_table_unique_cache_lines
    row_table_unique_rows source_reads response_slot_hwm response_word_hwm
    response_pool_stalls row_table_full_events virtual_build_rounds dram_reads
    dram_activates dram_precharges)
values=("$case_name" "$output_hash" "$ticks" "$insts" "$index_line_reads"
    "$index_words" "$index_hwm" "$feeder_descriptor_discards"
    "$feeder_predicate_discards" "$feeder_partition_discards"
    "$physical_records" "$physical_record_sha256"
    "$index_filter_words" "$index_filter_cycles"
    "$index_filter_wait_events" "$index_filter_wait_cycles"
    "$write_issues" "$write_completions"
    "$indirect_spd_reads" "$pages_ready" "$pages_ready_early"
    "$first_page_cycles" "$all_page_cycles" "$page_span_cycles"
    "$stream_spd_reads" "$stream_writes" "$alu_compute"
    "$page_ready_signals" "$page_wait_reads" "$page_wait_deferrals"
    "$page_wait_responses" "$l3_read_hits" "$l3_read_misses"
    "$memory_bytes_read" "$cpu_cycles" "$row_slices" "$row_rows"
    "$row_entries" "$grow_order" "$index_partitions" "$index_range_passes"
    "$index_force_cache" "$partition_keep_combiner" \
    "$resolved_offset_entries" "$resolved_offset_epoch_entries"
    "$transparent_spd_mode"
    "$index_filter_words_per_cycle" "$require_index_filter_wait"
    "$response_slots" "$response_word_pool"
    "$rt_cache_lines" "$rt_rows" "$rt_unique_cache_lines" "$rt_unique_rows"
    "$source_reads" "$response_slot_hwm" "$response_word_hwm"
    "$response_pool_stalls" "$rt_full" "$build_rounds" "$dram_reads"
    "$dram_acts" "$dram_pres")
if [[ $index_range_passes -eq 1 ]]; then
    expected_index_words=$((16384 * index_partitions))
    expected_partition_discards=$((16384 * (index_partitions - 1)))
    range_begin_count=$(grep -Ec \
        'event=bounded_range_begin schema=1 .* logical=16384 active_offsets=[1-9][0-9]* .* backing=llc_index_rescan combiner=retained$' \
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
    [[ $resolved_offset_entries -gt 0 &&
       $resolved_offset_entries -le 4096 &&
       $resolved_offset_epoch_entries -gt 0 &&
       $resolved_offset_epoch_entries -le 4096 &&
       $((row_slices * row_rows * row_entries)) -le 4096 &&
       $index_force_cache -eq 1 && $partition_keep_combiner -eq 1 &&
       $grow_order -eq 1 &&
       $index_words -eq $expected_index_words &&
       $feeder_descriptor_discards -eq 16384 &&
       $feeder_predicate_discards -eq 0 &&
       $feeder_partition_discards -eq $expected_partition_discards &&
       $range_begin_count -eq 1 &&
       $range_pass_count -eq $index_partitions &&
       $range_complete_count -eq 1 &&
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
touch "$out/virtual_tile_consumer_case.pass"
cat "$out/result.tsv"
