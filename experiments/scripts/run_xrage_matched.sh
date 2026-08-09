#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 && $# -ne 4 ]]; then
    echo "usage: $0 GEM5_BIN NLOHMANN_JSON_SOURCE OUTDIR [INPUT_JSON]" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
json_source=$(realpath "$2")
out=$(realpath -m "$3")
input=$(realpath "${4:-$root/experiments/inputs/xrage_zero_payload_20k.json}")
build="$out/build"
smoke="$root/experiments/scripts/run_xrage_direct_index_smoke.sh"
native_target=spatter_maa_xrage_runtime_verify_16K
zero_target=spatter_maa_xrage_runtime_verify_4K

[[ -x $gem5 && -f $json_source/include/nlohmann/json.hpp && -f $input ]] || {
    echo "missing gem5, offline nlohmann-json source, or input" >&2
    exit 2
}
[[ -x $smoke ]] || {
    echo "missing XRAGE smoke runner" >&2
    exit 2
}
[[ ! -e $out ]] || {
    echo "refusing to overwrite existing output: $out" >&2
    exit 2
}
[[ -z $(git -C "$root" status --short) ]] || {
    echo "matched XRAGE run requires a clean source worktree" >&2
    exit 2
}

mkdir -p "$build"
source_commit=$(git -C "$root" rev-parse HEAD)
configure_cmd=(
    cmake -S "$root/benchmarks/spatter" -B "$build"
    -DBUILD_GEM5=ON -DBUILD_FUNC=OFF -DCMAKE_BUILD_TYPE=Release
    -DGEM5_HOME="$root" -DMAA_HOME="$root/benchmarks/API"
    -DMAA_MEM_SIZE=2147483648
    -DFETCHCONTENT_SOURCE_DIR_NLOHMANN_JSON="$json_source"
)
build_cmd=(
    cmake --build "$build" --parallel 16 --target
    "$native_target" "$zero_target"
)
printf '%q ' "${configure_cmd[@]}" > "$out/configure.command"
printf '\n' >> "$out/configure.command"
printf '%q ' "${build_cmd[@]}" > "$out/build.command"
printf '\n' >> "$out/build.command"
"${configure_cmd[@]}" > "$out/configure.log" 2>&1
"${build_cmd[@]}" > "$out/build.log" 2>&1

native_bin="$build/$native_target"
zero_bin="$build/$zero_target"
[[ -x $native_bin && -x $zero_bin ]] || {
    echo "matched XRAGE guest build is incomplete" >&2
    exit 1
}
[[ -z $(git -C "$root" status --short) ]] || {
    echo "matched XRAGE guest build modified the source worktree" >&2
    exit 1
}

{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'input=%s\n' "$input"
    printf 'pattern=UNIFORM:20000:1:NR\n'
    printf 'result_scale=3\n'
    printf 'native_arm=native16x3\n'
    printf 'native_logical_tile_elements=16384\n'
    printf 'zero_payload_arm=zeropayload4x3\n'
    printf 'zero_payload_logical_tile_elements=4096\n'
    printf 'parallel_arms=1\n'
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$out/manifest.txt"
sha256sum "$gem5" "$native_bin" "$zero_bin" "$input" "$smoke" \
    > "$out/artifact_sha256.txt"

env \
    XRAGE_ARM=native XRAGE_GUEST_ARM=native16x3 XRAGE_RESULT_SCALE=3 \
    XRAGE_SIMULATOR_SOURCE_COMMIT="$source_commit" XRAGE_GUEST_DATA_SEED=1 \
    MAA_PHYSICAL_TILE_ELEMENTS=16384 MAA_GUEST_ABI_TILE_ELEMENTS=16384 \
    "$smoke" "$gem5" "$native_bin" "$input" "$out/native16x3" \
    > "$out/native16x3.controller.log" 2>&1 &
native_pid=$!

env \
    XRAGE_ARM=zero_payload_4k XRAGE_GUEST_ARM=zeropayload4x3 \
    XRAGE_RESULT_SCALE=3 XRAGE_SIMULATOR_SOURCE_COMMIT="$source_commit" \
    XRAGE_GUEST_DATA_SEED=1 \
    MAA_PHYSICAL_TILE_ELEMENTS=4096 MAA_GUEST_ABI_TILE_ELEMENTS=4096 \
    MAA_NUM_OFFSET_TABLE_ENTRIES=4096 \
    MAA_NUM_OFFSET_TABLE_EPOCH_ENTRIES=4096 \
    MAA_NUM_INITIAL_ROW_TABLE_SLICES=16 MAA_ROW_TABLE_ROWS_PER_SLICE=16 \
    MAA_VIRTUAL_INDEX_BUFFER_LINES=1 MAA_VIRTUAL_INDEX_PARTITIONS=1 \
    MAA_VIRTUAL_COMBINE_SLOTS=16 MAA_VIRTUAL_COMBINE_WORDS=128 \
    MAA_VIRTUAL_COMBINE_WAYS=1 MAA_VIRTUAL_COMBINE_BANKS=1 \
    MAA_VIRTUAL_RESPONSE_SLOTS=8 MAA_VIRTUAL_RESPONSE_WORDS=0 \
    MAA_VIRTUAL_RESPONSE_WORD_POOL=64 MAA_VIRTUAL_WORDS_PER_CYCLE=1 \
    MAA_VIRTUAL_MAX_OUTSTANDING_WRITES=8 \
    MAA_FUSED_RESULT_TRANSFER_WORDS_PER_CYCLE=1 \
    MAA_FUSED_RESULT_TRANSFER_BANKS=1 \
    "$smoke" "$gem5" "$zero_bin" "$input" "$out/zeropayload4x3" \
    > "$out/zeropayload4x3.controller.log" 2>&1 &
zero_pid=$!

set +e
wait "$native_pid"
native_rc=$?
wait "$zero_pid"
zero_rc=$?
set -e
printf '%s\n' "$native_rc" > "$out/native16x3.exit"
printf '%s\n' "$zero_rc" > "$out/zeropayload4x3.exit"
[[ $native_rc -eq 0 && $zero_rc -eq 0 ]] || {
    echo "matched XRAGE arm failed: native=$native_rc zero_payload=$zero_rc" >&2
    exit 1
}

read_result() {
    awk -F '\t' 'NR == 2 { print $1, $2, $3 }' "$1"
}
read -r native_hash native_length native_ticks < <(
    read_result "$out/native16x3/result.tsv"
)
read -r zero_hash zero_length zero_ticks < <(
    read_result "$out/zeropayload4x3/result.tsv"
)
[[ $native_length -eq 20000 && $zero_length -eq 20000 &&
   $native_hash == "$zero_hash" && $native_ticks -gt 0 &&
   $zero_ticks -gt 0 ]] || {
    echo "matched XRAGE correctness/provenance check failed" >&2
    exit 1
}
speedup=$(awk -v native="$native_ticks" -v zero="$zero_ticks" \
    'BEGIN { printf "%.6f", native / zero }')
{
    printf 'source_commit\tinput_sha256\toutput_hash\tverified_length'
    printf '\tnative16x3_simTicks\tzeropayload4x3_simTicks'
    printf '\tnative_over_zero_speedup\n'
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$source_commit" "$(sha256sum "$input" | awk '{print $1}')" \
        "$native_hash" "$native_length" "$native_ticks" "$zero_ticks" \
        "$speedup"
} > "$out/summary.tsv"
touch "$out/xrage_zero_payload_matched.pass"
echo "XRAGE_ZERO_PAYLOAD_MATCHED_PASS native_simTicks=$native_ticks zero_simTicks=$zero_ticks speedup=$speedup"
