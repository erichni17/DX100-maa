#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "usage: $0 GEM5_BIN OUTDIR RAMULATOR_LIB" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
out=$(realpath -m "$2")
ramulator_lib=$(realpath "$3")
m5op="$root/util/m5/build/x86/abi/x86/m5op.S"
[[ -f $m5op ]] || m5op="$root/util/m5/src/abi/x86/m5op.S"
[[ ! -e $out ]] || { echo "refusing to overwrite $out" >&2; exit 2; }
mkdir -p "$out/bin"
trap 'printf "%s\n" "$?" > "$out/matrix.exit"' EXIT

build_guest() {
    local label=$1 tile=$2
    g++ -I"$root/benchmarks/API" -I"$root/include" \
        -I"$root/util/m5/src" -std=c++17 -O3 -Wall -Wextra -g3 \
        -DGEM5 -DTILE_SIZE=16384 -DPHYSICAL_PAGE="$tile" -DNUM_CORES=4 \
        -DMAA_MEM_SIZE=0x80000000 \
        "$m5op" \
        "$root/benchmarks/API/test_backed_rmw_reorder.cpp" \
        -o "$out/bin/$label"
}
build_guest native16 16384
build_guest native4 4096
cp --reflink=auto "$out/bin/native4" "$out/bin/backed16meta"
cp --reflink=auto "$out/bin/native4" "$out/bin/backed4diag"

sha256sum "$gem5" "$ramulator_lib" "$out/bin/"* \
    "$root/src/mem/MAA/IndirectAccess.cc" \
    "$root/src/mem/MAA/IndirectAccess.hh" \
    "$root/src/mem/MAA/CpuSidePort.cc" \
    "$root/benchmarks/API/MAA_gem5.hpp" \
    "$root/benchmarks/API/test_backed_rmw_reorder.cpp" \
    > "$out/artifact_sha256.txt"
git -C "$root" rev-parse HEAD > "$out/source_commit.txt"
printf 'scope=api_mechanism\npublication=timed_guest_cache_stores\n' \
    > "$out/evidence_contract.txt"

config="$root/configs/deprecated/example/se.py"
ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
export LD_LIBRARY_PATH="$(dirname "$ramulator_lib"):${LD_LIBRARY_PATH:-}"

checkpoint() {
    local label=$1 mode=$2
    timeout 300 "$gem5" --listener-mode=off \
        --outdir="$out/$label/checkpoint" "$config" \
        --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB \
        --max-checkpoints=1 --cmd "$out/bin/$label" --options "$mode" \
        > "$out/$label.checkpoint.log" 2>&1
    grep -Eq '^Exiting @ tick [0-9]+ because checkpoint$' \
        "$out/$label.checkpoint.log"
}

run_arm() {
    local label=$1 mode=$2 physical=$3 offsets=$4 rows=$5
    mkdir -p "$out/$label"
    checkpoint "$label" "$mode"
    local treatment=()
    if [[ $label == backed* ]]; then
        treatment=(--maa_virtual_index_force_cache)
    fi
    if [[ $label == backed4diag ]]; then
        treatment+=(
            --maa_virtual_index_partitions=4
            --maa_virtual_index_range_passes
            --maa_virtual_index_range_policy=3
            --maa_virtual_index_descriptor_spool
            --maa_virtual_descriptor_spool_read_credits=4
            --maa_virtual_descriptor_spool_write_credits=16
        )
    fi
    set +e
    timeout 21600 "$gem5" --listener-mode=off \
        --debug-flags=MAAVirtualTrace \
        --debug-file="$out/$label/maa.trace" \
        --outdir="$out/$label/run" "$config" --cpu-type X86O3CPU \
        -r 1 --checkpoint-dir="$out/$label/checkpoint" \
        -n 4 --mem-size 2GB --sys-clock 3.2GHz \
        --cpu-clock 3.2GHz --caches --l1d_size=32kB --l1d_assoc=8 \
        --l1d_mshrs=16 --l1d_write_buffers=8 --l1i_size=32kB \
        --l1i_assoc=8 --l1i_mshrs=16 --l1i_write_buffers=8 \
        --l2cache --l2_size=256kB --l2_assoc=4 --l2_mshrs=32 \
        --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16 \
        --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4 \
        --cacheline_size=64 --mem-type Ramulator2 \
        --ramulator-config "$ramulator" --mem-channels=1 --maa \
        --maa_num_tile_elements=16384 \
        --maa_physical_tile_elements="$physical" \
        --maa_num_initial_row_table_slices=16 \
        --maa_num_row_table_rows_per_slice="$rows" \
        --maa_num_row_table_entries_per_subslice_row=8 \
        --maa_num_offset_table_entries="$offsets" \
        --maa_num_offset_table_epoch_entries="$offsets" \
        "${treatment[@]}" --cmd "$out/bin/$label" --options "$mode" \
        > "$out/$label/restore.log" 2>&1
    local rc=$?
    set -e
    printf '%s\n' "$rc" > "$out/$label/restore.exit"
    [[ $rc -eq 0 ]]
    grep -Eq '^BACKED_RMW_RESULT .* errors=0$' \
        "$out/$label/restore.log"
    grep -Fqx 'ROI Ended' "$out/$label/restore.log"
    ! grep -Eiq 'panic|fatal|assert|abort|segmentation fault|error:' \
        "$out/$label/restore.log"
}

run_arm native16 native 16384 16384 64
run_arm native4 native 4096 4096 32
run_arm backed16meta backed 4096 16384 64
run_arm backed4diag backed 4096 4096 32

field_from_result() {
    local field=$1 file=$2
    sed -n "s/.*${field}=\([^ ]*\).*/\1/p" "$file" | tail -1
}
sim_ticks() {
    awk '$1 == "simTicks" { print $2; exit }' "$1"
}

printf 'arm\toutput_hash\tsimTicks\tphysical_elements\n' > "$out/matrix.tsv"
for arm in native16 native4 backed16meta backed4diag; do
    marker=$(grep -E '^BACKED_RMW_RESULT ' "$out/$arm/restore.log")
    hash=$(field_from_result hash "$out/$arm/restore.log")
    ticks=$(sim_ticks "$out/$arm/run/stats.txt")
    physical=$(field_from_result physical "$out/$arm/restore.log")
    printf '%s\t%s\t%s\t%s\n' "$arm" "$hash" "$ticks" "$physical" \
        >> "$out/matrix.tsv"
done

reference=$(awk -F '\t' '$1 == "native16" { print $2 }' "$out/matrix.tsv")
[[ $(awk -F '\t' '$1 == "native4" { print $2 }' "$out/matrix.tsv") == \
   "$reference" ]]
[[ $(awk -F '\t' '$1 == "backed16meta" { print $2 }' "$out/matrix.tsv") == \
   "$reference" ]]
[[ $(awk -F '\t' '$1 == "backed4diag" { print $2 }' "$out/matrix.tsv") == \
   "$reference" ]]

for arm in backed16meta backed4diag; do
    summary_count=$(grep -c 'event=backed_rmw_complete ' \
        "$out/$arm/maa.trace")
    [[ $summary_count -eq 1 ]]
    grep 'event=backed_rmw_complete ' "$out/$arm/maa.trace" \
        > "$out/$arm/mechanism.tsv"
    grep -Eq 'generation_exact=1 .*publication=timed_guest_cache_stores ' \
        "$out/$arm/mechanism.tsv"
    grep -Eq 'a_write_issues=([1-9][0-9]*) a_write_acks=\1' \
        "$out/$arm/mechanism.tsv"
done
grep -q 'metadata_scope=full16k ' "$out/backed16meta/mechanism.tsv"
grep -q 'metadata_scope=diagnostic4k ' "$out/backed4diag/mechanism.tsv"

touch "$out/matrix.complete"
cat "$out/matrix.tsv"
