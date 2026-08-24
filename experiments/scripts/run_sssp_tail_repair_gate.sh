#!/usr/bin/env bash
# Prepare, launch exactly once, and validate the repaired full-S22 candidate.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
full_runner="$root/experiments/scripts/run_sssp_old_result_hybrid_full.sh"
source_file="$root/benchmarks/gapbs/src/sssp.cc"
route_header="$root/benchmarks/gapbs/src/sssp_tail_route.hh"
replay_header="$root/benchmarks/gapbs/src/sssp_tail_replay.hh"
graph=/data1/nier/worktrees/DX100-full-tile-sweep-20260720/benchmarks/gapbs/serialized_graph_22.wsg
graph_sha256=23eb25e34343334976554071a8184f7b03358fe1892ba44cd2f5a38369f4eebc

usage() {
    echo "usage: $0 --prepare GATE_ROOT" >&2
    echo "       $0 --launch GATE_ROOT UNIT" >&2
    echo "       $0 --run-frozen GATE_ROOT" >&2
    echo "       $0 --status GATE_ROOT UNIT" >&2
    echo "       $0 --validate GATE_ROOT UNIT" >&2
    exit 2
}

hash_value() {
    sha256sum "$1" | awk '{print $1}'
}

manifest_value() {
    local manifest=$1 key=$2
    awk -F= -v key="$key" \
        '$1 == key {print substr($0, length(key) + 2)}' "$manifest"
}

require_hash() {
    local path=$1 expected=$2
    [[ -f $path && $(hash_value "$path") == "$expected" ]]
}

prepare_gate() {
    local gate=$1 frozen guest manifest cxx source_commit guest_sha
    gate=$(realpath -m "$gate")
    [[ ! -e $gate ]] || {
        echo "refusing existing gate root: $gate" >&2
        return 2
    }
    [[ -z $(git -C "$root" status --short) ]] || {
        echo "refusing frozen build from dirty source tree" >&2
        git -C "$root" status --short >&2
        return 1
    }
    require_hash "$graph" "$graph_sha256"

    frozen="$gate/frozen"
    guest="$frozen/sssp_maa_2G_old_result_hybrid_fp"
    manifest="$frozen/candidate.manifest"
    mkdir -p "$frozen" "$gate/full"
    rmdir "$gate/full"
    cxx=${CXX:-g++}
    {
        printf '%q ' "$cxx" -I"$root/benchmarks/gapbs/src" \
            -I"$root/benchmarks/API" -I"$root/include" \
            -I"$root/util/m5/src" -std=c++11 -O3 -Wall -Wextra -Werror \
            -Wno-ignored-qualifiers -Wno-unused-parameter -fopenmp -DGEM5 \
            -DMAA -DNUM_CORES=4 -DNUM_TILES_PER_CORE=8 -DTILE_SIZE=16384 \
            -DMAA_CONSUMER_TILE_SIZE=4096 -DMAA_MEM_SIZE=0x80000000 \
            -DSSSP_FP_ENABLE=1 -DSSSP_OLD_RESULT_HYBRID=1 \
            "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$guest"
        printf '\n'
    } >"$frozen/build.command"
    "$cxx" -I"$root/benchmarks/gapbs/src" -I"$root/benchmarks/API" \
        -I"$root/include" -I"$root/util/m5/src" -std=c++11 -O3 -Wall \
        -Wextra -Werror -Wno-ignored-qualifiers -Wno-unused-parameter \
        -fopenmp -DGEM5 -DMAA -DNUM_CORES=4 -DNUM_TILES_PER_CORE=8 \
        -DTILE_SIZE=16384 -DMAA_CONSUMER_TILE_SIZE=4096 \
        -DMAA_MEM_SIZE=0x80000000 -DSSSP_FP_ENABLE=1 \
        -DSSSP_OLD_RESULT_HYBRID=1 \
        "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$guest"
    chmod 0555 "$guest"
    guest_sha=$(hash_value "$guest")
    source_commit=$(git -C "$root" rev-parse HEAD)
    {
        printf 'schema=dx100.sssp.tail_repair.frozen.v1\n'
        printf 'state=frozen\nprepared_at=%s\n' "$(date -Ins)"
        printf 'source_commit=%s\nsource_sha256=%s\n' \
            "$source_commit" "$(hash_value "$source_file")"
        printf 'route_header_sha256=%s\n' "$(hash_value "$route_header")"
        printf 'replay_header_sha256=%s\n' "$(hash_value "$replay_header")"
        printf 'full_runner_sha256=%s\n' "$(hash_value "$full_runner")"
        printf 'gate_runner_sha256=%s\n' "$(hash_value "$0")"
        printf 'candidate_guest_path=%s\ncandidate_guest_sha256=%s\n' \
            "$guest" "$guest_sha"
        printf 'graph_path=%s\ngraph_sha256=%s\n' "$graph" "$graph_sha256"
        printf 'logical_elements=16384\nphysical_tile_elements=4096\n'
        printf 'native_arms=0\nwall_timeout=none\nlaunch_count=0\n'
    } >"$manifest"
    sha256sum "$guest" "$source_file" "$route_header" "$replay_header" \
        "$full_runner" "$0" "$frozen/build.command" \
        >"$frozen/files.sha256"
    hash_value "$frozen/files.sha256" >"$frozen/identity.sha256"
    chmod 0444 "$manifest" "$frozen/build.command" "$frozen/files.sha256" \
        "$frozen/identity.sha256"
    {
        printf 'schema=dx100.sssp.tail_repair.prepared.v1\n'
        printf 'state=prepared\nlaunch_count=0\n'
        printf 'frozen_identity_sha256=%s\n' \
            "$(<"$frozen/identity.sha256")"
    } >"$gate/prepared.ledger"
    chmod 0444 "$gate/prepared.ledger"
    printf 'SSSP_TAIL_REPAIR_PREPARED gate=%s guest_sha256=%s\n' \
        "$gate" "$guest_sha"
}

launch_gate() {
    local gate=$1 unit=$2 frozen manifest guest guest_sha load_state lease
    gate=$(realpath -m "$gate")
    lease="$gate/launch.lease"
    if ! mkdir "$lease"; then
        echo "refusing gate with an existing exclusive launch lease: $gate" >&2
        return 2
    fi
    {
        printf 'schema=dx100.sssp.tail_repair.launch_lease.v1\n'
        printf 'owner_pid=%s\nunit=%s.service\n' "$$" "$unit"
        printf 'acquired_at=%s\n' "$(date -Ins)"
    } >"$lease/owner.tmp"
    mv "$lease/owner.tmp" "$lease/owner"
    chmod 0444 "$lease/owner"
    frozen="$gate/frozen"
    manifest="$frozen/candidate.manifest"
    guest="$frozen/sssp_maa_2G_old_result_hybrid_fp"
    [[ -s $gate/prepared.ledger && -s $manifest ]]
    [[ $(manifest_value "$gate/prepared.ledger" state) == prepared ]]
    [[ $(manifest_value "$gate/prepared.ledger" launch_count) == 0 ]]
    [[ ! -e $gate/launch.intent && ! -e $gate/launch.accepted ]]
    [[ ! -e $gate/full ]]
    guest_sha=$(manifest_value "$manifest" candidate_guest_sha256)
    require_hash "$guest" "$guest_sha"
    load_state=$(systemctl --user show "$unit.service" \
        --property=LoadState --value 2>/dev/null || true)
    [[ -z $load_state || $load_state == not-found ]]

    {
        printf 'schema=dx100.sssp.tail_repair.launch.v1\n'
        printf 'state=launching\nlaunch_count=1\nunit=%s.service\n' "$unit"
        printf 'requested_at=%s\n' "$(date -Ins)"
        printf 'frozen_guest_sha256=%s\n' "$guest_sha"
        printf 'native_arms=0\nwall_timeout=none\n'
    } >"$gate/launch.intent.tmp"
    mv "$gate/launch.intent.tmp" "$gate/launch.intent"
    chmod 0444 "$gate/launch.intent"

    systemd-run --user --unit="$unit" \
        --description="DX100 repaired SSSP full-S22 candidate-only gate" \
        --property=Type=exec --property=Restart=no \
        --setenv="SSSP_PREBUILT_GUEST=$guest" \
        --setenv="SSSP_PREBUILT_GUEST_SHA256=$guest_sha" \
        "$0" --run-frozen "$gate"
    {
        printf 'schema=dx100.sssp.tail_repair.launch.accepted.v1\n'
        printf 'state=accepted\nlaunch_count=1\nunit=%s.service\n' "$unit"
        printf 'accepted_at=%s\n' "$(date -Ins)"
    } >"$gate/launch.accepted.tmp"
    mv "$gate/launch.accepted.tmp" "$gate/launch.accepted"
    chmod 0444 "$gate/launch.accepted"
    {
        printf 'state=accepted\naccepted_at=%s\n' "$(date -Ins)"
    } >"$lease/accepted.tmp"
    mv "$lease/accepted.tmp" "$lease/accepted"
    chmod 0444 "$lease/accepted"
    systemctl --user show "$unit.service" --no-pager \
        --property=Id --property=LoadState --property=ActiveState \
        --property=SubState --property=MainPID \
        --property=ExecMainStartTimestampMonotonic \
        >"$gate/unit.identity"
    chmod 0444 "$gate/unit.identity"
    printf 'SSSP_TAIL_REPAIR_LAUNCHED unit=%s.service gate=%s\n' "$unit" "$gate"
}

run_frozen() {
    local gate=$1 manifest guest guest_sha full_rc validation_rc terminal
    gate=$(realpath -m "$gate")
    manifest="$gate/frozen/candidate.manifest"
    guest="$gate/frozen/sssp_maa_2G_old_result_hybrid_fp"
    [[ -s $gate/launch.intent && -s $manifest ]]
    [[ $(manifest_value "$gate/launch.intent" launch_count) == 1 ]]
    guest_sha=$(manifest_value "$manifest" candidate_guest_sha256)
    require_hash "$guest" "$guest_sha"

    set +e
    SSSP_PREBUILT_GUEST="$guest" \
        SSSP_PREBUILT_GUEST_SHA256="$guest_sha" \
        "$full_runner" "$gate/full"
    full_rc=$?
    validation_rc=1
    if (( full_rc == 0 )); then
        "$full_runner" --validate "$gate/full"
        validation_rc=$?
    fi
    set -e
    terminal=false
    if (( full_rc == 0 && validation_rc == 0 )); then
        terminal=true
    fi
    {
        printf 'schema=dx100.sssp.tail_repair.systemd_result.v1\n'
        printf 'terminal=%s\nfull_runner_exit=%s\n' "$terminal" "$full_rc"
        printf 'validation_exit=%s\nfinished_at=%s\n' \
            "$validation_rc" "$(date -Ins)"
        printf 'launch_count=1\nnative_arms=0\nwall_timeout=none\n'
    } >"$gate/systemd.result.tmp"
    mv "$gate/systemd.result.tmp" "$gate/systemd.result"
    [[ $terminal == true ]]
}

status_gate() {
    local gate=$1 unit=$2
    gate=$(realpath -m "$gate")
    systemctl --user show "$unit.service" --no-pager \
        --property=Id --property=LoadState --property=ActiveState \
        --property=SubState --property=MainPID --property=Result \
        --property=ExecMainCode --property=ExecMainStatus
    for ledger in prepared.ledger launch.lease/owner launch.lease/accepted \
        launch.intent launch.accepted systemd.result; do
        if [[ -s $gate/$ledger ]]; then
            printf '%s\n' "--- $ledger"
            sed -n '1,80p' "$gate/$ledger"
        fi
    done
}

validate_gate() {
    local gate=$1 unit=$2 manifest guest_sha active result exit_status
    gate=$(realpath -m "$gate")
    manifest="$gate/frozen/candidate.manifest"
    for ledger in prepared.ledger launch.lease/owner launch.lease/accepted \
        launch.intent launch.accepted systemd.result; do
        [[ -s $gate/$ledger ]]
    done
    [[ $(manifest_value "$gate/launch.intent" launch_count) == 1 ]]
    [[ $(manifest_value "$gate/launch.accepted" launch_count) == 1 ]]
    [[ $(manifest_value "$gate/systemd.result" launch_count) == 1 ]]
    [[ $(manifest_value "$gate/systemd.result" terminal) == true ]]
    [[ $(manifest_value "$gate/systemd.result" full_runner_exit) == 0 ]]
    [[ $(manifest_value "$gate/systemd.result" validation_exit) == 0 ]]
    guest_sha=$(manifest_value "$manifest" candidate_guest_sha256)
    require_hash "$gate/frozen/sssp_maa_2G_old_result_hybrid_fp" "$guest_sha"
    require_hash "$gate/full/bin/sssp_maa_2G_old_result_hybrid_fp" "$guest_sha"
    [[ $(manifest_value "$gate/full/candidate.manifest" \
        candidate_guest_origin) == prebuilt_frozen ]]
    "$full_runner" --validate "$gate/full"
    active=$(systemctl --user show "$unit.service" \
        --property=ActiveState --value)
    result=$(systemctl --user show "$unit.service" --property=Result --value)
    exit_status=$(systemctl --user show "$unit.service" \
        --property=ExecMainStatus --value)
    [[ $active == inactive && $result == success && $exit_status == 0 ]]
    printf 'SSSP_TAIL_REPAIR_FULL_PASS gate=%s unit=%s.service\n' "$gate" "$unit"
}

[[ $# -ge 2 ]] || usage
case $1 in
--prepare)
    [[ $# -eq 2 ]] || usage
    prepare_gate "$2"
    ;;
--launch)
    [[ $# -eq 3 ]] || usage
    launch_gate "$2" "$3"
    ;;
--run-frozen)
    [[ $# -eq 2 ]] || usage
    run_frozen "$2"
    ;;
--status)
    [[ $# -eq 3 ]] || usage
    status_gate "$2" "$3"
    ;;
--validate)
    [[ $# -eq 3 ]] || usage
    validate_gate "$2" "$3"
    ;;
*)
    usage
    ;;
esac
