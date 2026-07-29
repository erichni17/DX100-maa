#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 OUTDIR NLOHMANN_JSON_SOURCE" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
json_source=$(realpath "$2")
source_dir="$root/benchmarks/spatter"
build_dir="$out/build"
targets=(
    spatter_maa_verify_16K
    spatter_maa_fused_verify_16K
    spatter_maa_fused_4K
    spatter_maa_fused_verify_4K
    spatter_maa_virtual_verify_16K
    spatter_maa_virtual_index_verify_16K
)

[[ ! -e $out ]] || {
    echo "refusing to overwrite existing output: $out" >&2
    exit 2
}
[[ -f $json_source/include/nlohmann/json.hpp ]] || {
    echo "invalid offline nlohmann-json source: $json_source" >&2
    exit 2
}
[[ -f $root/util/m5/build/x86/abi/x86/m5op.S ]] || {
    echo "missing built m5ops assembly source" >&2
    exit 2
}
[[ -z $(git -C "$root" status --short) ]] || {
    echo "XRAGE attribution build requires a clean source worktree" >&2
    exit 2
}

mkdir -p "$build_dir"
configure_cmd=(
    cmake -S "$source_dir" -B "$build_dir"
    -DBUILD_GEM5=ON
    -DBUILD_FUNC=OFF
    -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_CXX_FLAGS=-I$json_source/include
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
    -DGEM5_HOME="$root"
    -DMAA_HOME="$root/benchmarks/API"
    -DMAA_MEM_SIZE=2147483648
    -DFETCHCONTENT_SOURCE_DIR_NLOHMANN_JSON="$json_source"
)
build_cmd=(cmake --build "$build_dir" --parallel 4 --target "${targets[@]}")

printf '%q ' "${configure_cmd[@]}" > "$out/configure.command"
printf '\n' >> "$out/configure.command"
printf '%q ' "${build_cmd[@]}" > "$out/build.command"
printf '\n' >> "$out/build.command"
"${configure_cmd[@]}" > "$out/configure.log" 2>&1
"${build_cmd[@]}" > "$out/build.log" 2>&1

for target in "${targets[@]}"; do
    [[ -x $build_dir/$target ]] || {
        echo "missing XRAGE attribution binary: $target" >&2
        exit 1
    }
done
[[ -s $build_dir/CMakeCache.txt && -s $build_dir/compile_commands.json ]] || {
    echo "missing CMake provenance artifacts" >&2
    exit 1
}
[[ -z $(git -C "$root" status --short) ]] || {
    echo "XRAGE attribution build modified the source worktree" >&2
    exit 1
}

{
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'cmake=%s\n' "$(cmake --version | head -1)"
    printf 'cxx=%s\n' "$("${CXX:-c++}" --version | head -1)"
    printf 'nlohmann_json_source=%s\n' "$json_source"
    printf 'nlohmann_json_header_sha256=%s\n' \
        "$(sha256sum "$json_source/include/nlohmann/json.hpp" | cut -d' ' -f1)"
} > "$out/build_manifest.txt"

(cd "$root" &&
    git ls-files -z -- benchmarks/spatter benchmarks/API \
        util/m5/build/x86/abi/x86/m5op.S |
        xargs -0 sha256sum) > "$out/tracked_source_sha256.txt"
sha256sum "$0" "$out/configure.command" "$out/build.command" \
    "$out/configure.log" "$out/build.log" "$build_dir/CMakeCache.txt" \
    "$build_dir/compile_commands.json" \
    "${targets[@]/#/$build_dir/}" > "$out/artifact_sha256.txt"
touch "$out/xrage_attribution_build.pass"
echo "PASS XRAGE attribution build: $out"
