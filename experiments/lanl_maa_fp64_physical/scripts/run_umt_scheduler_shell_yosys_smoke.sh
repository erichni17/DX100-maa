#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
harness="$repo_root/experiments/lanl_maa_fp64_physical"
build_root=${LANL_MAA_FP64_BUILD_ROOT:-/data1/nier/build/lanl-maa-fp64-physical-20260729}
yosys=${YOSYS:-$build_root/bazel-output-root/f2f5e01e79b38130a4801e35d50b0b89/execroot/_main/bazel-out/k8-opt-exec-ST-d57f47055a04/bin/external/yosys+/yosys}
timeout_seconds=${LANL_MAA_UMT_YOSYS_TIMEOUT_SECONDS:-120}
files=(
    "$harness/rtl/LanlUmtTokenEntry.v"
    "$harness/rtl/LanlUmtRotatingPriority.v"
    "$harness/rtl/LanlUmtBank16x640.v"
    "$harness/rtl/LanlUmtSchedulerShell.v"
)

[[ -x "$yosys" ]]
[[ "$timeout_seconds" =~ ^[1-9][0-9]*$ ]]

for top in \
    LanlUmtSchedulerShellT24W1 \
    LanlUmtSchedulerShellT24W2 \
    LanlUmtSchedulerShellT32W1 \
    LanlUmtSchedulerShellT32W2; do
    echo "BEGIN_YOSYS_SMOKE=$top"
    timeout "${timeout_seconds}s" "$yosys" -q -p \
        "read_verilog ${files[*]}; hierarchy -check -top $top; proc; memory_collect; check"
    echo "PASS_YOSYS_SMOKE=$top"
done
