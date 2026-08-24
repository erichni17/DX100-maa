#!/usr/bin/env bash
# Candidate-only exact gate for the full-CG logical-page -> SoA/JIT chain.
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "usage: $0 GEM5_BIN OUTDIR small|full" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
out=$(realpath -m "$2")
size=$3
config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
source_file="$root/benchmarks/NAS/cg/cg.cpp"
cxx=${CXX:-g++}

case $size in
  small)
    cg_na=1024
    default_reference=/data1/nier/dx100-runs/2026-08-14-cg-logical16-rmw-smoke-906c4e1c-r3/runs/legacy/restore.log
    default_reference_sha=36f2d6f6db48672084c7e298e9edb72dc0e5ac93e9f519b7b9f571952ad7590a
    ;;
  full)
    cg_na=150000
    default_reference=/data1/nier/dx100-runs/2026-08-11-cg-bounded-789cc703-full-v8/bounded4_cached/run.log
    default_reference_sha=0fe931685c37695bc51c74288c67f1494a0c91a723f8e831efa0ac2a7515441c
    ;;
  *)
    echo "size must be small or full" >&2
    exit 2
    ;;
esac
reference=${CG_REFERENCE_LOG:-$default_reference}
reference_sha=${CG_REFERENCE_SHA256:-$default_reference_sha}

[[ -x $gem5 ]] || { echo "missing gem5 binary: $gem5" >&2; exit 2; }
[[ -f $reference ]] || { echo "missing frozen CG reference: $reference" >&2; exit 2; }
[[ ! -e $out ]] || { echo "refusing existing output: $out" >&2; exit 2; }
[[ $(sha256sum "$reference" | awk '{print $1}') == "$reference_sha" ]] || {
    echo "frozen CG reference hash mismatch" >&2
    exit 1
}
git -C "$root" status --short --branch > /tmp/cg-logical-page-status-before.$$
[[ $(wc -l < /tmp/cg-logical-page-status-before.$$) -eq 1 ]] || {
    echo "refusing evidence run from a dirty worktree" >&2
    cat /tmp/cg-logical-page-status-before.$$ >&2
    exit 1
}

mkdir -p "$out/input" "$out/bin" "$out/checkpoint" "$out/run"
mv /tmp/cg-logical-page-status-before.$$ "$out/input/source_status.before"
selector="$out/input/logical_page_soa_jit.selector"
printf '%s\n' 'token_stream_ld logical_page_soa_jit' > "$selector"
chmod 0444 "$selector"

guest="$out/bin/cg_logical_page_rmw"
"$cxx" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++11 -O3 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers -Wno-unused-parameter -fopenmp \
    -DGEM5 -DMAA -DMAA_VIRTUAL_GATHER -DMAA_GENERAL_VIRTUAL_CONSUMER \
    -DMAA_CONSUMER_TILE_SIZE=4096 -DCG_LOGICAL16_RMW \
    -DCG_LOGICAL_PAGE_RMW -DCG_FP_ENABLE -DCG_NA="$cg_na" \
    -DNUM_CORES=4 -DNUM_TILES_PER_CORE=10 -DTILE_SIZE=16384 \
    -DMAA_MEM_SIZE=0x80000000 "$root/util/m5/src/abi/x86/m5op.S" \
    "$source_file" -o "$guest"

checkpoint_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" "$config"
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1
    --cmd "$guest" --options "MAA_DEFERRED $selector"
)
restore_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/run"
    --debug-flags=MAAVirtualTrace --debug-file=logical_page_trace.log
    "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --checkpoint-dir "$out/checkpoint"
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
    --maa_num_tiles_per_core=10 --maa_num_tile_elements=16384
    --maa_physical_tile_elements=4096 --maa_logical_tile_page_scheduler
    --maa_num_offset_table_entries=16384
    --maa_num_offset_table_epoch_entries=16384
    --maa_num_initial_row_table_slices=16
    --maa_soa_jit_predicate_active_credits=16
    --maa_soa_jit_active_value_owners=32
    --cmd "$guest" --options "MAA_DEFERRED $selector"
)

source_commit=$(git -C "$root" rev-parse HEAD)
reference_line=$(grep -E "^CG_FINGERPRINT .* elements=$cg_na .* result=PASS$" \
    "$reference")
[[ $(grep -Ec "^CG_FINGERPRINT .* elements=$cg_na .* result=PASS$" \
          "$reference") -eq 1 ]] || {
    echo "frozen reference lacks one exact CG fingerprint" >&2
    exit 1
}
{
    printf 'schema=dx100.cg.logical_page_rmw.v1\n'
    printf 'size=%s\ncg_na=%s\n' "$size" "$cg_na"
    printf 'source_commit=%s\n' "$source_commit"
    printf 'reference_path=%s\nreference_sha256=%s\n' \
        "$reference" "$reference_sha"
    printf 'reference_fingerprint=%s\n' "$reference_line"
    printf 'arm=hybrid_only\ncomparison_arms=0\n'
    printf 'native_reruns=0\nwall_timeout=none\n'
    printf 'logical_elements=16384\nphysical_tile_elements=4096\n'
    printf 'guest_lanes=32\nlogical_scheduler_reserved_lanes=8\n'
    printf 'hidden_logical_payload_bytes=0\nhost_payload_access=0\n'
    printf 'checkpoint_command='; printf '%q ' "${checkpoint_cmd[@]}"; printf '\n'
    printf 'restore_command='; printf '%q ' "${restore_cmd[@]}"; printf '\n'
} > "$out/manifest.txt"
sha256sum "$gem5" "$guest" "$selector" "$source_file" "$config" \
    "$ramulator" "$0" "$reference" > "$out/input/artifact_sha256.before"

# No timeout wrapper is permitted for either phase. The full arm is allowed to
# run to its architectural terminal or an explicit simulator failure.
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
    "${checkpoint_cmd[@]}" > "$out/checkpoint.log" 2>&1
checkpoint_rc=$?
set -e
printf '%s\n' "$checkpoint_rc" > "$out/checkpoint.exit"
[[ $checkpoint_rc -eq 0 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because checkpoint$' \
          "$out/checkpoint.log" || true) -eq 1 ]]
! grep -Eq 'CG_LOGICAL16_RMW_SELECTION|CG_FINGERPRINT|ROI End!!!' \
    "$out/checkpoint.log"
(
    cd "$out/checkpoint"
    find . -type f -print0 | sort -z | xargs -0 sha256sum
) > "$out/input/checkpoint.files.sha256"
checkpoint_sha=$(sha256sum "$out/input/checkpoint.files.sha256" | awk '{print $1}')

set +e
OMP_PROC_BIND=false OMP_NUM_THREADS=4 \
    "${restore_cmd[@]}" > "$out/run/restore.log" 2>&1
restore_rc=$?
set -e
printf '%s\n' "$restore_rc" > "$out/run/restore.exit"
[[ $restore_rc -eq 0 ]]

restore="$out/run/restore.log"
stats="$out/run/stats.txt"
trace="$out/run/logical_page_trace.log"
[[ -s $stats && -s $trace ]]
[[ $(grep -Fxc "$reference_line" "$restore" || true) -eq 1 ]] || {
    echo "candidate CG fingerprint does not match the frozen physical4 reference" >&2
    exit 1
}
[[ $(grep -Ec '^CG_LOGICAL16_RMW_SELECTION treatment=logical_page_soa_jit .*host_payload_access=0 performance_promotable=0$' "$restore" || true) -eq 1 ]]
[[ $(grep -Ec '^CG_LOGICAL16_RMW_TERMINAL treatment=logical_page_soa_jit .*host_payload_access=0 performance_promotable=0 result=PASS$' "$restore" || true) -eq 1 ]]
[[ $(grep -Fxc 'ROI End!!!' "$restore" || true) -eq 1 ]]
[[ $(grep -Ec '^Exiting @ tick [0-9]+ because m5_exit instruction encountered$' "$restore" || true) -eq 1 ]]
[[ $(grep -Eic 'panic|fatal|assert|abort|segmentation fault|error:' "$restore" || true) -eq 0 ]]

for resolved in num_maas=1 num_tiles_per_core=10 \
    num_tile_elements=16384 physical_tile_elements=4096 \
    logical_tile_page_scheduler=true num_offset_table_entries=16384 \
    num_offset_table_epoch_entries=16384 num_initial_row_table_slices=16 \
    soa_jit_predicate_active_credits=16 soa_jit_active_value_owners=32; do
    grep -Fqx "$resolved" "$out/run/config.ini"
done

terminal=$(grep '^CG_LOGICAL16_RMW_TERMINAL ' "$restore")
field() {
    local key=$1
    sed -n "s/.* $key=\([0-9][0-9]*\).*/\1/p" <<<"$terminal"
}
windows=$(field full_windows)
index_words=$(field staged_index_words)
value_words=$(field staged_value_words)
product_words=$(field product_words)
index_pages=$(field index_publish_pages)
value_pages=$(field value_publish_pages)
logical_alus=$(field logical_alu_vectors)
logical_windows=$(field logical_page_windows)
[[ $windows =~ ^[1-9][0-9]*$ ]]
[[ $logical_windows -eq $windows && $logical_alus -eq $windows ]]
[[ $index_words -eq $((windows * 16384)) ]]
[[ $value_words -eq $index_words && $product_words -eq $index_words ]]
[[ $index_pages -eq $((windows * 4)) && $value_pages -eq $index_pages ]]

stat_sum() {
    local suffix=$1
    awk -v suffix="$suffix" '
        /^---------- Begin Simulation Statistics/ { section++ }
        section == 1 && $1 ~ ("_" suffix "$") { sum += $2 }
        /^---------- End Simulation Statistics/ && section == 1 {
            printf "%.0f\n", sum
            exit
        }
    ' "$stats"
}

soa_instructions=$(stat_sum IND_SoaJitInstructions)
soa_terminals=$(stat_sum IND_SoaJitTerminalCompletions)
soa_selected=$(stat_sum IND_SoaJitSelected)
soa_rejected=$(stat_sum IND_SoaJitPredicateRejected)
soa_aliases=$(stat_sum IND_SoaJitAliasesApplied)
soa_fallbacks=$(stat_sum IND_BoundedGlobalMergeFallbacks)
publish_issues=$(stat_sum STR_PublishIssues)
publish_accepts=$(stat_sum STR_PublishAccepts)
publish_responses=$(stat_sum STR_PublishWriteResponses)
publish_terminals=$(stat_sum STR_PublishTerminals)
expected_publish_pages=$((windows * 12))
expected_publish_lines=$((expected_publish_pages * 256))

[[ $soa_instructions -eq $windows && $soa_terminals -eq $windows ]]
[[ $soa_selected -eq $index_words && $soa_rejected -eq 0 ]]
[[ $soa_aliases -eq $index_words && $soa_fallbacks -eq 0 ]]
[[ $publish_issues -eq $expected_publish_lines ]]
[[ $publish_accepts -eq $publish_issues ]]
[[ $publish_responses -eq $publish_issues ]]
[[ $publish_terminals -eq $expected_publish_pages ]]

logical_admits=$(grep -Fc 'event=logical_page_admit ' "$trace" || true)
logical_begins=$(grep -Fc 'event=logical_page_begin ' "$trace" || true)
logical_dispatches=$(grep -Fc 'event=logical_page_native_dispatch ' "$trace" || true)
logical_completes=$(grep -Fc 'event=logical_page_native_complete ' "$trace" || true)
logical_retires=$(grep -Fc 'event=logical_page_retire ' "$trace" || true)
soa_trace_terminals=$(grep -Ec 'event=soa_jit_complete .* logical=16384 .* terminal=1$' "$trace" || true)
fallback_events=$(grep -Ec 'event=[^ ]*fallback' "$trace" || true)
[[ $logical_admits -eq $windows && $logical_retires -eq $windows ]]
[[ $logical_begins -eq $((windows * 4)) ]]
[[ $logical_dispatches -eq $((windows * 16)) ]]
[[ $logical_completes -eq $logical_dispatches ]]
[[ $soa_trace_terminals -eq $windows && $fallback_events -eq 0 ]]

sim_ticks=$(awk '$1 == "simTicks" { print $2; exit }' "$stats")
[[ $sim_ticks =~ ^[1-9][0-9]*$ ]]
sha256sum "$selector" "$reference" > "$out/input/immutable.after"
[[ $(awk 'NR == 1 {print $1}' "$out/input/immutable.after") == \
   $(awk '$2 ~ /logical_page_soa_jit.selector$/ {print $1}' \
       "$out/input/artifact_sha256.before") ]]
[[ $(awk 'NR == 2 {print $1}' "$out/input/immutable.after") == "$reference_sha" ]]

git -C "$root" status --short --branch > "$out/input/source_status.after"
cmp -s "$out/input/source_status.before" "$out/input/source_status.after"
{
    printf 'terminal=true\ncorrect=true\n'
    printf 'size=%s\nsource_commit=%s\n' "$size" "$source_commit"
    printf 'gem5_sha256=%s\nguest_sha256=%s\n' \
        "$(sha256sum "$gem5" | awk '{print $1}')" \
        "$(sha256sum "$guest" | awk '{print $1}')"
    printf 'checkpoint_sha256=%s\nreference_sha256=%s\n' \
        "$checkpoint_sha" "$reference_sha"
    printf 'simTicks=%s\nlogical_windows=%s\n' "$sim_ticks" "$windows"
    printf 'logical_page_actions=%s/%s\n' "$logical_dispatches" "$logical_completes"
    printf 'logical_page_instructions=%s/%s\n' "$logical_admits" "$logical_retires"
    printf 'publisher_write_responses=%s/%s\n' "$publish_responses" "$publish_issues"
    printf 'soa_jit_terminals=%s/%s\n' "$soa_terminals" "$soa_instructions"
    printf 'IND_SoaJitFallbacks=0\nIND_SoaJitOpenContexts=0\n'
    printf 'fallback_basis=IND_BoundedGlobalMergeFallbacks\n'
    printf 'open_state_basis=instructions_equal_terminals_and_all_response_ledgers_closed\n'
    printf 'fingerprint=%s\n' "$reference_line"
} > "$out/result.txt"
sha256sum "$out/manifest.txt" "$out/result.txt" "$restore" "$stats" \
    "$out/run/config.ini" "$trace" "$out/input/source_status.after" \
    > "$out/result_sha256.txt"
touch "$out/gate.complete"
printf 'PASS CG logical-page RMW %s gate out=%s\n' "$size" "$out"
