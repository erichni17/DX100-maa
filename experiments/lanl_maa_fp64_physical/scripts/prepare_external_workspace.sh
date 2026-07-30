#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
harness="$repo_root/experiments/lanl_maa_fp64_physical"
tools_root=${LANL_MAA_FP64_TOOLS_ROOT:-/data1/nier/tools/lanl-maa-fp64-physical-20260729}
bazel_root=${LANL_MAA_BAZEL_ORFS_ROOT:-$tools_root/src/bazel-orfs-6b55b049a5e753a234151578a3b3424388660db7}
hardfloat_root=${LANL_MAA_HARDFLOAT_ROOT:-$tools_root/src/HardFloat-1}

hardfloat_archive="$tools_root/downloads/HardFloat-1.zip"
bazel_archive="$tools_root/downloads/bazel-orfs-6b55b049a5e753a234151578a3b3424388660db7.tar.gz"

printf '%s  %s\n' \
    6b3757c9fbfa2230c6a2b84605e39372cb589dd7500e979c4f0b8ecc8a03b14b \
    "$hardfloat_archive" | sha256sum --check --status
printf '%s  %s\n' \
    5ac89aea9c35fbdbbe118b6cb415510dd97c7e59adebcf46593239e734b6b809 \
    "$bazel_archive" | sha256sum --check --status

[[ $(<"$bazel_root/.bazelversion") == 8.6.0 ]]
[[ -f "$hardfloat_root/source/addRecFN.v" ]]
[[ -f "$hardfloat_root/source/RISCV/HardFloat_specialize.vi" ]]

target="$bazel_root/lanl_fp64"
if [[ -e "$target" && ! -d "$target" ]]; then
    printf 'refusing non-directory target: %s\n' "$target" >&2
    exit 1
fi

mkdir -p "$target/activity" "$target/hardfloat" "$target/rtl" \
    "$target/scripts"
install -m 0644 "$harness/BUILD.bazel" "$target/BUILD.bazel"
install -m 0644 "$harness/constraints.sdc" "$target/constraints.sdc"
install -m 0644 "$harness/portfolio_power.bzl" \
    "$target/portfolio_power.bzl"
install -m 0644 "$harness/activity/portfolio_activity_contract.json" \
    "$target/activity/portfolio_activity_contract.json"
install -m 0644 "$harness/rtl/LanlFp64HardFloat.v" \
    "$target/rtl/LanlFp64HardFloat.v"
install -m 0644 "$harness/rtl/LanlFp64DualSharedRecode.v" \
    "$target/rtl/LanlFp64DualSharedRecode.v"
install -m 0644 "$harness/rtl/LanlFp64Completion2W.v" \
    "$target/rtl/LanlFp64Completion2W.v"
install -m 0644 "$harness/rtl/LanlFp64Completion2WSplit.v" \
    "$target/rtl/LanlFp64Completion2WSplit.v"
install -m 0644 "$harness/rtl/LanlMaaLineTable.v" \
    "$target/rtl/LanlMaaLineTable.v"
install -m 0755 "$harness/scripts/generate_portfolio_saif.py" \
    "$target/scripts/generate_portfolio_saif.py"
install -m 0755 "$harness/scripts/generate_dual_portfolio_saif.py" \
    "$target/scripts/generate_dual_portfolio_saif.py"
install -m 0644 "$harness/scripts/portfolio_power_base.tcl" \
    "$target/scripts/portfolio_power_base.tcl"
install -m 0644 "$harness/hardfloat.BUILD.bazel" \
    "$target/hardfloat/BUILD.bazel"

for source in \
    HardFloat_primitives.v \
    HardFloat_rawFN.v \
    addRecFN.v \
    divSqrtRecFN_small.v \
    fNToRecFN.v \
    isSigNaNRecFN.v \
    mulAddRecFN.v \
    mulRecFN.v \
    recFNToFN.v \
    HardFloat_consts.vi \
    HardFloat_localFuncs.vi
do
    install -m 0644 "$hardfloat_root/source/$source" \
        "$target/hardfloat/$source"
    cmp --silent "$hardfloat_root/source/$source" "$target/hardfloat/$source"
done

for source in HardFloat_specialize.v HardFloat_specialize.vi
do
    install -m 0644 "$hardfloat_root/source/RISCV/$source" \
        "$target/hardfloat/$source"
    cmp --silent "$hardfloat_root/source/RISCV/$source" \
        "$target/hardfloat/$source"
done

printf 'prepared=%s\n' "$target"
