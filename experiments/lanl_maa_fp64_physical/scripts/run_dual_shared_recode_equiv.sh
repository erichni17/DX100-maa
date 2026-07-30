#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
harness="$repo_root/experiments/lanl_maa_fp64_physical"
tools_root=${LANL_MAA_FP64_TOOLS_ROOT:-/data1/nier/tools/lanl-maa-fp64-physical-20260729}
hardfloat="$tools_root/src/HardFloat-1/source"
build_root=${LANL_MAA_FP64_BUILD_ROOT:-/data1/nier/build/lanl-maa-fp64-physical-20260729}
iverilog=${IVERILOG:-$tools_root/iverilog/usr/bin/iverilog}
vvp=${VVP:-$tools_root/iverilog/usr/bin/vvp}
ivl_base=${IVL_BASE:-$tools_root/iverilog/usr/lib/x86_64-linux-gnu/ivl}

[[ -x "$iverilog" ]]
[[ -x "$vvp" ]]
[[ -x "$ivl_base/ivl" ]]
mkdir -p "$build_root/rtl-smoke"

"$iverilog" -B "$ivl_base" -g2012 \
    -I "$hardfloat/RISCV" \
    -I "$hardfloat" \
    -s lanl_fp64_dual_shared_recode_equiv_tb \
    -o "$build_root/rtl-smoke/lanl_fp64_dual_shared_recode_equiv_tb" \
    "$hardfloat/HardFloat_primitives.v" \
    "$hardfloat/HardFloat_rawFN.v" \
    "$hardfloat/RISCV/HardFloat_specialize.v" \
    "$hardfloat/addRecFN.v" \
    "$hardfloat/divSqrtRecFN_small.v" \
    "$hardfloat/fNToRecFN.v" \
    "$hardfloat/isSigNaNRecFN.v" \
    "$hardfloat/mulAddRecFN.v" \
    "$hardfloat/mulRecFN.v" \
    "$hardfloat/recFNToFN.v" \
    "$harness/rtl/LanlFp64HardFloat.v" \
    "$harness/rtl/LanlFp64DualSharedRecode.v" \
    "$harness/tests/lanl_fp64_dual_shared_recode_equiv_tb.v"

exec "$vvp" -M "$ivl_base" \
    "$build_root/rtl-smoke/lanl_fp64_dual_shared_recode_equiv_tb"
