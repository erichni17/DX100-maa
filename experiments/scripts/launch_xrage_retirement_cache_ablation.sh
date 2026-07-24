#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 8 ]]; then
    cat >&2 <<EOF
usage: $0 EXPECTED_SELF_SHA256 EXPECTED_RUNNER_SHA256 EXPECTED_VERIFIER_SHA256 EXPECTED_REFERENCE_VERIFIER_SHA256 EXPECTED_SIM_COMMIT BFS_CAMPAIGN REFERENCE_APPROVAL OUTPUT
EOF
    exit 2
fi

expected_self_sha=$1
expected_runner_sha=$2
expected_verifier_sha=$3
expected_reference_verifier_sha=$4
expected_sim_commit=$5
bfs_campaign=$(realpath "$6")
reference_approval=$(realpath "$7")
output=$(realpath -m "$8")
self=$(realpath "$0")
sim_root=$(cd "$(dirname "$self")/../.." && pwd)
runner=$sim_root/experiments/scripts/run_xrage_retirement_cache_ablation.py
verifier=$sim_root/experiments/scripts/verify_xrage_retirement_cache_ablation.py
reference_root=/data1/nier/worktrees/dx100-research-virtual-suite-20260717
reference_verifier=$reference_root/experiments/scripts/verify_virtual_campaign.py
primary=/data1/nier/worktrees/DX100-virtual-suite-20260717
source_20k=$primary/experiments/campaigns/2026-07-24_xrage_20k_correctness_coherent_cd140bb
source_full=$primary/experiments/campaigns/2026-07-24_xrage_full_correctness_coherent_cd140bb
reference_campaign=$primary/experiments/campaigns/2026-07-24_xrage_full_attribution_coherent_cd140bb_replicated

hash_file() {
    sha256sum "$1" | cut -d' ' -f1
}

for expected in "$expected_self_sha" "$expected_runner_sha" \
                "$expected_verifier_sha" "$expected_reference_verifier_sha"; do
    [[ $expected =~ ^[0-9a-f]{64}$ ]] || {
        echo "expected script hashes must be full SHA-256 values" >&2
        exit 2
    }
done
[[ $expected_sim_commit =~ ^[0-9a-f]{40}$ ]] || {
    echo "expected simulator commit must be a full Git object ID" >&2
    exit 2
}

assert_authorized_state() {
    [[ $(hash_file "$self") == "$expected_self_sha" &&
       $(hash_file "$runner") == "$expected_runner_sha" &&
       $(hash_file "$verifier") == "$expected_verifier_sha" &&
       $(hash_file "$reference_verifier") == \
           "$expected_reference_verifier_sha" ]] || {
        echo "an authorized workflow script changed" >&2
        return 1
    }
    [[ $(git -C "$sim_root" rev-parse HEAD) == "$expected_sim_commit" &&
       -z $(git -C "$sim_root" status --porcelain=v1) ]] || {
        echo "retirement-cache cost worktree changed after authorization" >&2
        return 1
    }
}

assert_authorized_state
[[ -f $bfs_campaign/campaign.pass &&
   ! -e $bfs_campaign/campaign.fail ]] || {
    echo "the upstream BFS replay is not in a clean pass state" >&2
    exit 3
}

if [[ -f $output/campaign.pass ]]; then
    "$verifier" "$output"
    assert_authorized_state
    echo "retirement-cache ablation already passed and verified: $output"
    exit 0
fi
[[ ! -e $output ]] || {
    echo "ablation output exists without a verified pass: $output" >&2
    exit 4
}

assert_authorized_state
"$runner" \
    --sim-root "$sim_root" \
    --expected-sim-commit "$expected_sim_commit" \
    --gem5 "$primary/build_tile_ready_fix_v2/X86/gem5.opt" \
    --ramulator-yaml \
        "$primary/ext/ramulator2/ramulator2/example_gem5_config.yaml" \
    --ramulator-lib \
        "$primary/ext/ramulator2/ramulator2/libramulator.so" \
    --virtual-verify-bin \
        "$primary/benchmarks/spatter/build_control_virtual/spatter_maa_virtual_verify_16K" \
    --virtual-perf-bin \
        "$primary/benchmarks/spatter/build_control_virtual/spatter_maa_virtual_16K" \
    --input-20k \
        /data1/nier/DX100/experiments/inputs/xrage_gather0_20k.json \
    --input-full \
        /data1/nier/DX100/experiments/inputs/xrage_gather0_full.json \
    --source-20k "$source_20k" \
    --source-full-correctness "$source_full" \
    --reference-campaign "$reference_campaign" \
    --reference-approval "$reference_approval" \
    --reference-verifier "$reference_verifier" \
    --output "$output"
assert_authorized_state
"$verifier" "$output"
assert_authorized_state
echo "completed and independently verified retirement-cache ablation: $output"
