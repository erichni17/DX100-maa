#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
run_root=/data1/nier/dx100-runs/2026-08-03-gzz-tile-attribution
mkdir -p "$run_root"

available_gib() {
    awk '/^MemAvailable:/ {printf "%d\n", $2 / 1024 / 1024}' /proc/meminfo
}

launch_wave() {
    local wave=$1 treatment=$2
    local available
    available=$(available_gib)
    if (( available < 120 )); then
        echo "refusing $wave wave: only ${available} GiB available" >&2
        return 20
    fi

    local units=()
    for physical in 16384 32768 65536; do
        local unit="dx100-gzz-${wave}-p${physical}-20260803"
        units+=("$unit.service")
        systemd-run --user --no-block \
            --unit="$unit" \
            --description="DX100 GZZ attribution $wave physical=$physical" \
            --working-directory="$root" \
            --property=MemoryAccounting=yes \
            --property=MemoryHigh=28G \
            --property=MemoryMax=32G \
            --property=MemorySwapMax=0 \
            --property=OOMPolicy=stop \
            --property=KillMode=control-group \
            "$root/experiments/scripts/run_gzz_tile_attribution.sh" \
            "$physical" "$treatment"
    done

    while :; do
        local running=0
        for unit in "${units[@]}"; do
            if [[ $(systemctl --user show "$unit" -p ActiveState --value) == active ]]; then
                running=1
            fi
        done
        (( running == 1 )) || break
        sleep 60
    done

    local failed=0
    for unit in "${units[@]}"; do
        local result status
        result=$(systemctl --user show "$unit" -p Result --value)
        status=$(systemctl --user show "$unit" -p ExecMainStatus --value)
        printf '%s\tresult=%s\tstatus=%s\n' "$unit" "$result" "$status"
        [[ "$result" == success && "$status" == 0 ]] || failed=1
    done
    (( failed == 0 ))
}

launch_wave native native
launch_wave logical16 16384

date -Ins > "$run_root/campaign.complete"
