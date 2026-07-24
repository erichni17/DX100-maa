#!/bin/false
set -euo pipefail
umask 077
required_home=/data1/nier/.dx-runtime-state/retirement-cache-home
[[ ${DX100_SANITIZED_LAUNCH:-} == 1 &&
   ${HOME:-} == "$required_home" &&
   ${PATH:-} == /usr/bin:/bin ]] || {
    echo "invoke through the approved env -i launcher contract" >&2
    exit 2
}
for forbidden in CDPATH ENV BASH_ENV PYTHONHOME PYTHONPATH LD_AUDIT \
                 LD_PRELOAD LD_LIBRARY_PATH GIT_DIR GIT_WORK_TREE \
                 GIT_COMMON_DIR GIT_OBJECT_DIRECTORY \
                 GIT_ALTERNATE_OBJECT_DIRECTORIES; do
    [[ ! -v $forbidden ]] || {
        echo "forbidden inherited environment variable: $forbidden" >&2
        exit 2
    }
done
export LANG=C
export LC_ALL=C
export PATH=/usr/bin:/bin
export PYTHONDONTWRITEBYTECODE=1
export PYTHONHASHSEED=0
export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_NO_REPLACE_OBJECTS=1
export GIT_CONFIG_COUNT=3
export GIT_CONFIG_KEY_0=core.fsmonitor
export GIT_CONFIG_VALUE_0=false
export GIT_CONFIG_KEY_1=core.hooksPath
export GIT_CONFIG_VALUE_1=/dev/null
export GIT_CONFIG_KEY_2=core.untrackedCache
export GIT_CONFIG_VALUE_2=false

/usr/bin/mkdir -p -m 0700 "$required_home"
[[ -d $required_home && ! -L $required_home &&
   $(/usr/bin/stat -c %u "$required_home") == "$(/usr/bin/id -u)" &&
   $(/usr/bin/stat -c %a "$required_home") == 700 ]] || {
    echo "private launcher HOME is unsafe" >&2
    exit 2
}

if [[ $# -ne 19 ]]; then
    cat >&2 <<EOF
usage: $0 EXPECTED_SELF_SHA256 EXPECTED_RUNNER_SHA256 EXPECTED_VERIFIER_SHA256 EXPECTED_REFERENCE_VERIFIER_SHA256 EXPECTED_SIM_COMMIT BFS_CAMPAIGN BFS_APPROVAL EXPECTED_BFS_APPROVAL_SHA256 BFS_ORACLE EXPECTED_BFS_ORACLE_SHA256 EXPECTED_INPUT_20K_SHA256 REFERENCE_APPROVAL EXPECTED_REFERENCE_APPROVAL_SHA256 REFERENCE_RESULT_APPROVAL EXPECTED_REFERENCE_RESULT_APPROVAL_SHA256 REFERENCE_CAMPAIGN EXPECTED_SOURCE_20K_FINGERPRINT EXPECTED_SOURCE_FULL_FINGERPRINT OUTPUT
EOF
    exit 2
fi

expected_self_sha=$1
expected_runner_sha=$2
expected_verifier_sha=$3
expected_reference_verifier_sha=$4
expected_sim_commit=$5
bfs_campaign=$(/usr/bin/realpath "$6")
bfs_approval=$(/usr/bin/realpath "$7")
expected_bfs_approval_sha=$8
bfs_oracle=$(/usr/bin/realpath "$9")
expected_bfs_oracle_sha=${10}
expected_input_20k_sha=${11}
reference_approval=$(/usr/bin/realpath "${12}")
expected_reference_approval_sha=${13}
reference_result_approval=$(/usr/bin/realpath "${14}")
expected_reference_result_approval_sha=${15}
reference_campaign=$(/usr/bin/realpath "${16}")
expected_source_20k_fingerprint=${17}
expected_source_full_fingerprint=${18}
output=$(/usr/bin/realpath -m "${19}")
self=$(/usr/bin/realpath "$0")
sim_root=$(cd "$(dirname "$self")/../.." && pwd)
runner=$sim_root/experiments/scripts/run_xrage_retirement_cache_ablation.py
verifier=$sim_root/experiments/scripts/verify_xrage_retirement_cache_ablation.py
reference_root=/data1/nier/worktrees/dx100-research-virtual-suite-20260717
reference_verifier=$reference_root/experiments/scripts/verify_virtual_campaign.py
primary=/data1/nier/worktrees/DX100-virtual-suite-20260717
source_20k=$primary/experiments/campaigns/2026-07-24_xrage_20k_correctness_coherent_cd140bb
source_full=$primary/experiments/campaigns/2026-07-24_xrage_full_correctness_coherent_cd140bb
reference_inputs=$reference_campaign/inputs
input_20k=/data1/nier/DX100/experiments/inputs/xrage_gather0_20k.json

hash_file() {
    /usr/bin/sha256sum "$1" | /usr/bin/cut -d' ' -f1
}

for expected in "$expected_self_sha" "$expected_runner_sha" \
                "$expected_verifier_sha" "$expected_reference_verifier_sha" \
                "$expected_bfs_approval_sha" "$expected_bfs_oracle_sha" \
                "$expected_input_20k_sha" \
                "$expected_reference_approval_sha" \
                "$expected_reference_result_approval_sha" \
                "$expected_source_20k_fingerprint" \
                "$expected_source_full_fingerprint"; do
    [[ $expected =~ ^[0-9a-f]{64}$ ]] || {
        echo "expected artifact hashes must be full SHA-256 values" >&2
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
           "$expected_reference_verifier_sha" &&
       $(hash_file "$reference_approval") == \
           "$expected_reference_approval_sha" &&
       $(hash_file "$reference_result_approval") == \
           "$expected_reference_result_approval_sha" &&
       $(hash_file "$bfs_approval") == "$expected_bfs_approval_sha" &&
       $(hash_file "$bfs_oracle") == "$expected_bfs_oracle_sha" &&
       $(hash_file "$input_20k") == "$expected_input_20k_sha" ]] || {
        echo "an authorized workflow artifact changed" >&2
        return 1
    }
    [[ $(/usr/bin/git -C "$sim_root" rev-parse HEAD) == \
           "$expected_sim_commit" &&
       -z $(/usr/bin/git -C "$sim_root" status --porcelain=v1) &&
       -z $(/usr/bin/git -C "$sim_root" for-each-ref \
           --format='%(refname)' refs/replace) ]] || {
        echo "retirement-cache cost worktree changed after authorization" >&2
        return 1
    }
    git_common_dir=$(/usr/bin/git -C "$sim_root" rev-parse --git-common-dir)
    [[ $git_common_dir == /* ]] || git_common_dir=$sim_root/$git_common_dir
    [[ ! -e $git_common_dir/info/grafts ]] || {
        echo "legacy Git grafts are forbidden" >&2
        return 1
    }
}

verify_output() {
    /usr/bin/python3 -I "$verifier" "$output" \
        --expected-sim-commit "$expected_sim_commit" \
        --expected-launcher-sha "$expected_self_sha" \
        --expected-verifier-sha "$expected_verifier_sha" \
        --expected-runner-sha "$expected_runner_sha" \
        --expected-reference-verifier-sha \
            "$expected_reference_verifier_sha" \
        --expected-reference-approval-sha \
            "$expected_reference_approval_sha" \
        --expected-reference-result-approval-sha \
            "$expected_reference_result_approval_sha" \
        --expected-input-20k-sha "$expected_input_20k_sha" \
        --expected-bfs-approval-sha "$expected_bfs_approval_sha" \
        --expected-bfs-oracle-sha "$expected_bfs_oracle_sha" \
        --expected-source-20k-fingerprint \
            "$expected_source_20k_fingerprint" \
        --expected-source-full-fingerprint \
            "$expected_source_full_fingerprint" \
        --expected-source-20k "$source_20k" \
        --expected-source-full "$source_full" \
        --expected-reference-campaign "$reference_campaign" \
        --expected-bfs-campaign "$bfs_campaign" \
        --launcher-lock-fd-path "$launcher_lock_fd_path" \
        --expected-launcher-lock-identity "$campaign_lock_identity" \
        "$@"
}

atomic_marker() {
    local destination=$1
    local content=$2
    local temporary
    temporary=$(/usr/bin/mktemp "$output/.${destination##*/}.XXXXXX")
    printf '%s' "$content" > "$temporary"
    /usr/bin/chmod 0444 "$temporary"
    /usr/bin/mv -T "$temporary" "$destination"
}

publish_fail() {
    atomic_marker "$output/campaign.fail" "$1"$'\n'
}

campaign_lock_root=/data1/nier/.dx-runtime-state/retirement-cache-launch.lock
/usr/bin/mkdir -p -m 0700 "$campaign_lock_root"
[[ -d $campaign_lock_root && ! -L $campaign_lock_root &&
   $(/usr/bin/stat -c %u "$campaign_lock_root") == \
       "$(/usr/bin/id -u)" ]] || {
    echo "campaign publication lock directory is unsafe" >&2
    exit 2
}
[[ $(/usr/bin/stat -c %a "$campaign_lock_root") == 700 ]] ||
    /usr/bin/chmod 0700 "$campaign_lock_root"
exec {campaign_lock_fd}< "$campaign_lock_root"
/usr/bin/flock -x "$campaign_lock_fd"
launcher_lock_fd_path="/proc/$$/fd/$campaign_lock_fd"
campaign_lock_identity=$(/usr/bin/stat -Lc '%d:%i' "$launcher_lock_fd_path")
read -r lock_type lock_uid lock_mode lock_links < <(
    /usr/bin/stat -Lc '%F %u %a %h' "$launcher_lock_fd_path"
)
[[ $lock_type == directory && $lock_uid == "$(/usr/bin/id -u)" &&
   $lock_mode == 700 && $lock_links -ge 2 &&
   $(/usr/bin/stat -Lc '%d:%i' "$campaign_lock_root") == \
       "$campaign_lock_identity" ]] || {
    echo "opened campaign publication lock is unsafe" >&2
    exit 2
}
assert_campaign_lock() {
    [[ $(/usr/bin/stat -Lc '%d:%i' "$campaign_lock_root") == \
       "$campaign_lock_identity" ]] || {
        echo "campaign publication lock identity changed" >&2
        return 1
    }
}

assert_campaign_lock
assert_authorized_state
[[ -f $bfs_campaign/campaign.pass &&
   ! -L $bfs_campaign/campaign.pass &&
   ! -s $bfs_campaign/campaign.pass &&
   ! -e $bfs_campaign/campaign.fail ]] || {
    echo "the upstream BFS replay is not in a clean pass state" >&2
    exit 3
}
/usr/bin/python3 -I "$reference_verifier" bfs "$bfs_campaign" "$bfs_approval" \
    --oracle "$bfs_oracle"
assert_authorized_state

if [[ -f $output/campaign.pass ]]; then
    verify_output
    assert_campaign_lock
    assert_authorized_state
    echo "retirement-cache ablation already passed and verified: $output"
    exit 0
fi
if [[ -f $output/execution.complete &&
      ! -e $output/campaign.fail ]]; then
    verify_output --publish-pass || {
        publish_fail "independent verification failed"
        exit 4
    }
    echo "published previously completed retirement-cache ablation: $output"
    exit 0
fi
[[ ! -e $output ]] || {
    echo "ablation output exists without a verifiable completion: $output" >&2
    exit 4
}

assert_authorized_state
/usr/bin/python3 -I "$runner" \
    --sim-root "$sim_root" \
    --expected-sim-commit "$expected_sim_commit" \
    --expected-runner-sha "$expected_runner_sha" \
    --expected-reference-verifier-sha "$expected_reference_verifier_sha" \
    --expected-reference-approval-sha "$expected_reference_approval_sha" \
    --expected-reference-result-approval-sha \
        "$expected_reference_result_approval_sha" \
    --expected-input-20k-sha "$expected_input_20k_sha" \
    --expected-bfs-approval-sha "$expected_bfs_approval_sha" \
    --expected-bfs-oracle-sha "$expected_bfs_oracle_sha" \
    --expected-source-20k-fingerprint \
        "$expected_source_20k_fingerprint" \
    --expected-source-full-fingerprint \
        "$expected_source_full_fingerprint" \
    --gem5 "$reference_inputs/bin/gem5.opt" \
    --ramulator-yaml "$reference_inputs/ramulator.yaml" \
    --ramulator-lib "$reference_inputs/lib/libramulator.so" \
    --virtual-verify-bin \
        "$reference_inputs/benchmark/xrage_virtual_verify" \
    --virtual-perf-bin "$reference_inputs/benchmark/xrage_virtual" \
    --input-20k "$input_20k" \
    --input-full "$reference_inputs/benchmark/xrage_input.json" \
    --source-20k "$source_20k" \
    --source-full-correctness "$source_full" \
    --reference-campaign "$reference_campaign" \
    --reference-approval "$reference_approval" \
    --reference-result-approval "$reference_result_approval" \
    --reference-verifier "$reference_verifier" \
    --bfs-campaign "$bfs_campaign" \
    --bfs-approval "$bfs_approval" \
    --bfs-oracle "$bfs_oracle" \
    --output "$output"
assert_campaign_lock
assert_authorized_state
verify_output --publish-pass || {
    publish_fail "independent verification failed"
    exit 4
}
echo "completed, independently verified, and published retirement-cache ablation: $output"
