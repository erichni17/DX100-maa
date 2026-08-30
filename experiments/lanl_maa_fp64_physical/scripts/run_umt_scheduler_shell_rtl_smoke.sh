#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
harness="$repo_root/experiments/lanl_maa_fp64_physical"
tools_root=${LANL_MAA_FP64_TOOLS_ROOT:-/data1/nier/tools/lanl-maa-fp64-physical-20260729}
build_root=${LANL_MAA_UMT_SHELL_BUILD_ROOT:-/data1/nier/build/lanl-maa-umt-scheduler-shell-20260830}
iverilog=${IVERILOG:-$tools_root/iverilog/usr/bin/iverilog}
vvp=${VVP:-$tools_root/iverilog/usr/bin/vvp}
ivl_base=${IVL_BASE:-$tools_root/iverilog/usr/lib/x86_64-linux-gnu/ivl}
rtl="$harness/rtl/LanlUmtSchedulerShell.v"
testbench="$harness/tests/lanl_umt_scheduler_shell_tb.v"

[[ -x "$iverilog" ]]
[[ -x "$vvp" ]]
[[ -x "$ivl_base/ivl" ]]
mkdir -p "$build_root"

for top in \
    LanlUmtSchedulerShellT24W1 \
    LanlUmtSchedulerShellT24W2 \
    LanlUmtSchedulerShellT32W1 \
    LanlUmtSchedulerShellT32W2; do
    "$iverilog" -B "$ivl_base" -g2005 -Wall \
        -Wno-sensitivity-entire-array -s "$top" -tnull "$rtl"
done

"$iverilog" -B "$ivl_base" -g2005 -Wall \
    -Wno-sensitivity-entire-array \
    -s lanl_umt_scheduler_shell_tb \
    -o "$build_root/lanl_umt_scheduler_shell_tb" \
    "$rtl" "$testbench"

exec "$vvp" -M "$ivl_base" \
    "$build_root/lanl_umt_scheduler_shell_tb"
