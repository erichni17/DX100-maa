#!/usr/bin/env bash
set -euo pipefail

if [[ ${LANL_MAA_ALLOW_PHYSICAL:-0} != 1 ]]; then
    printf 'set LANL_MAA_ALLOW_PHYSICAL=1 after receiving an explicit gate approval\n' >&2
    exit 2
fi

repo_root=$(git rev-parse --show-toplevel)
harness="$repo_root/experiments/lanl_maa_fp64_physical"
tools_root=${LANL_MAA_FP64_TOOLS_ROOT:-/data1/nier/tools/lanl-maa-fp64-physical-20260729}
build_root=${LANL_MAA_FP64_BUILD_ROOT:-/data1/nier/build/lanl-maa-fp64-physical-20260729}
bazel_root=${LANL_MAA_BAZEL_ORFS_ROOT:-$tools_root/src/bazel-orfs-6b55b049a5e753a234151578a3b3424388660db7}

"$harness/scripts/prepare_external_workspace.sh"

if (($# == 0)); then
    set -- \
        //lanl_fp64:fp64_add_final \
        //lanl_fp64:fp64_mul_final \
        //lanl_fp64:fp64_fma_final \
        //lanl_fp64:fp64_div1_final \
        //lanl_fp64:fp64_div4_final \
        //lanl_fp64:fp64_div8_final
fi

mkdir -p \
    "$build_root/bazelisk" \
    "$build_root/bazel-output-root" \
    "$build_root/repository-cache"

cd "$bazel_root"
# Bazel serializes its client environment onto the server process command
# line.  Start it from a small allowlist so unrelated credentials inherited by
# an interactive Codex session never become visible through process listings.
exec env -i \
    HOME="$HOME" \
    USER="${USER:-nier}" \
    PATH=/usr/local/bin:/usr/bin:/bin \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TMPDIR=/tmp \
    BAZELISK_HOME="$build_root/bazelisk" \
    /usr/local/bin/bazel \
    --batch \
    --host_jvm_args=-XX:ActiveProcessorCount=4 \
    --output_user_root="$build_root/bazel-output-root" \
    build \
    --jobs=4 \
    --local_resources=memory=14336 \
    --repository_cache="$build_root/repository-cache" \
    --ui_event_filters=-debug \
    --noshow_progress \
    --show_result=0 \
    --output_groups=logs,jsons,reports,drcs,6_final.spef \
    "$@"
