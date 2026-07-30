#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
harness="$repo_root/experiments/lanl_maa_fp64_physical"
tools_root=${LANL_MAA_FP64_TOOLS_ROOT:-/data1/nier/tools/lanl-maa-fp64-physical-20260729}
build_root=${LANL_MAA_FP64_BUILD_ROOT:-/data1/nier/build/lanl-maa-fp64-physical-20260729}
iverilog=${IVERILOG:-$tools_root/iverilog/usr/bin/iverilog}
vvp=${VVP:-$tools_root/iverilog/usr/bin/vvp}
ivl_base=${IVL_BASE:-$tools_root/iverilog/usr/lib/x86_64-linux-gnu/ivl}

[[ -x "$iverilog" ]]
[[ -x "$vvp" ]]
[[ -x "$ivl_base/ivl" ]]
mkdir -p "$build_root/rtl-smoke"

"$iverilog" -B "$ivl_base" -g2012 -Wall \
    -s lanl_maa_line_table_tb \
    -o "$build_root/rtl-smoke/lanl_maa_line_table_tb" \
    "$harness/rtl/LanlMaaLineTable.v" \
    "$harness/tests/lanl_maa_line_table_tb.v"

exec "$vvp" -M "$ivl_base" \
    "$build_root/rtl-smoke/lanl_maa_line_table_tb"
