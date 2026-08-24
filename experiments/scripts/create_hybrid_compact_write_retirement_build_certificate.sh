#!/usr/bin/env bash
# Force a clean committed-HEAD gem5 relink and bind it to exact source/build
# provenance for the compact write-retirement A/B gate.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=${1:?usage: $0 OUT}
gem5=$root/build/X86/gem5.opt
readonly base_commit=0554f53a484ef797735131487b270155e82ac516
readonly frozen_ramulator=/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/input/libramulator.so
readonly dependency_source=/data1/nier/worktrees/DX100-virtualization-line-handoff-20260812

[[ ! -e $out ]] || { echo "certificate output exists: $out" >&2; exit 2; }
[[ -z $(git -C "$root" status --porcelain --untracked-files=all) ]] || {
    echo "source worktree is not entirely clean" >&2
    exit 2
}
git -C "$root" merge-base --is-ancestor "$base_commit" HEAD
[[ -x $frozen_ramulator ]]

source_commit=$(git -C "$root" rev-parse HEAD)
source_tree=$(git -C "$root" rev-parse 'HEAD^{tree}')
source_archive_sha=$(git -C "$root" archive --format=tar HEAD |
    sha256sum | awk '{print $1}')
source_commit_time=$(git -C "$root" show -s --format=%cI HEAD)

mkdir -p "$out"
printf '%s\n' 'scons --ignore-style build/X86/gem5.opt -j8' \
    >"$out/build-command.txt"

backup_dir=$(mktemp -d)
restore_backup=0
cleanup() {
    if [[ $restore_backup -eq 1 && -e $backup_dir/gem5.opt ]]; then
        mv "$backup_dir/gem5.opt" "$gem5"
    fi
    rm -rf "$backup_dir"
}
trap cleanup EXIT
if [[ -e $gem5 ]]; then
    mv "$gem5" "$backup_dir/gem5.opt"
    restore_backup=1
fi

build_start_epoch=$(date +%s)
set +e
(cd "$root" && scons --ignore-style build/X86/gem5.opt -j8) \
    >"$out/build.log" 2>&1
build_rc=$?
set -e
printf '%s\n' "$build_rc" >"$out/build.rc"
[[ $build_rc -eq 0 && -x $gem5 ]]
binary_mtime_epoch=$(stat -c %Y "$gem5")
[[ $binary_mtime_epoch -ge $build_start_epoch ]]
restore_backup=0

git -C "$root" diff --quiet
git -C "$root" diff --cached --quiet
[[ -z $(git -C "$root" status --porcelain --untracked-files=all) ]]
[[ -z $(git -C "$root" diff --name-only --diff-filter=D \
    "$base_commit" HEAD) ]]

git -C "$root" diff --name-only --diff-filter=ACMR -z \
    "$base_commit" HEAD | sort -z |
    while IFS= read -r -d '' path; do
        sha256sum "$root/$path" | sed "s#  $root/#  #"
    done >"$out/changed-sources.sha256"
[[ -s $out/changed-sources.sha256 ]]

find "$root/build/X86/mem/MAA" -maxdepth 1 -type f -name '*.o' \
    -printf '%P\0' | sort -z |
    while IFS= read -r -d '' object; do
        sha256sum "$root/build/X86/mem/MAA/$object" |
            sed 's#  .*/build/X86/mem/MAA/#  #'
    done >"$out/maa-objects.sha256"
[[ -s $out/maa-objects.sha256 ]]

directory_fingerprint() {
    local directory=$1
    find "$directory" -type f -printf '%P\0' | sort -z |
        while IFS= read -r -d '' path; do
            sha256sum "$directory/$path" | sed "s#  $directory/#  #"
        done | sha256sum | awk '{print $1}'
}

binary_sha=$(sha256sum "$gem5" | awk '{print $1}')
build_log_sha=$(sha256sum "$out/build.log" | awk '{print $1}')
build_command_sha=$(sha256sum "$out/build-command.txt" | awk '{print $1}')
changed_sources_sha=$(sha256sum "$out/changed-sources.sha256" |
    awk '{print $1}')
maa_objects_sha=$(sha256sum "$out/maa-objects.sha256" | awk '{print $1}')
ramulator_sha=$(sha256sum "$frozen_ramulator" | awk '{print $1}')
spdlog_sha=$(directory_fingerprint \
    "$root/ext/ramulator2/ramulator2/ext/spdlog")
yaml_sha=$(directory_fingerprint \
    "$root/ext/ramulator2/ramulator2/ext/yaml-cpp")
m5op_sha=$(sha256sum "$root/util/m5/build/x86/abi/x86/m5op.S" |
    awk '{print $1}')

{
    printf 'schema=dx100.hybrid_compact_write_retirement_build.v1\n'
    printf 'source_base_commit=%s\n' "$base_commit"
    printf 'source_commit=%s\nsource_tree=%s\n' \
        "$source_commit" "$source_tree"
    printf 'source_commit_time=%s\n' "$source_commit_time"
    printf 'source_archive_sha256=%s\n' "$source_archive_sha"
    printf 'changed_sources_sha256=%s\n' "$changed_sources_sha"
    printf 'build_command_sha256=%s\n' "$build_command_sha"
    printf 'build_log_sha256=%s\n' "$build_log_sha"
    printf 'build_start_epoch=%s\nbuild_rc=%s\n' \
        "$build_start_epoch" "$build_rc"
    printf 'maa_objects_sha256=%s\n' "$maa_objects_sha"
    printf 'gem5_path=%s\ngem5_sha256=%s\n' "$gem5" "$binary_sha"
    printf 'gem5_mtime_epoch=%s\n' "$binary_mtime_epoch"
    printf 'gem5_mtime=%s\n' "$(date -d "@$binary_mtime_epoch" -Ins)"
    printf 'offline_dependency_source=%s\n' "$dependency_source"
    printf 'ramulator_sha256=%s\n' "$ramulator_sha"
    printf 'ramulator_spdlog_directory_sha256=%s\n' "$spdlog_sha"
    printf 'ramulator_yaml_cpp_directory_sha256=%s\n' "$yaml_sha"
    printf 'util_m5op_s_sha256=%s\n' "$m5op_sha"
    printf 'clean_worktree=true\nforced_relink=true\nterminal=true\n'
} >"$out/certificate.txt"

(cd "$out" && find . -type f ! -name certificate.sha256 -printf '%P\0' |
    sort -z | xargs -0 sha256sum >certificate.sha256)
(cd "$out" && sha256sum -c certificate.sha256)
printf 'HYBRID_COMPACT_WRITE_RETIREMENT_BUILD_CERTIFICATE_PASS out=%s\n' \
    "$out"
