#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
run_root=${GZZ_AUTHORITATIVE_RUN_ROOT:-/data1/nier/dx100-runs/2026-07-20-full-tile-sweep/gzz_fixed_feed_20260803}
tiles=(1024 2048 4096 8192 16384 32768 65536)
mkdir -p "$run_root"

exec 9>"$run_root/campaign.lock"
if ! flock -n 9; then
    echo "another authoritative GZZ campaign owns $run_root" >&2
    exit 19
fi

if [[ -f "$run_root/campaign.complete" ]] &&
   python3 "$root/experiments/scripts/analyze_gzz_authoritative.py" \
       --run-root "$run_root" --logical-cap 16384; then
    echo "authoritative GZZ campaign is already complete"
    exit 0
fi

available_kib=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
minimum_kib=$((160 * 1024 * 1024))
if (( available_kib < minimum_kib )); then
    echo "refusing launch: $((available_kib / 1024 / 1024)) GiB available; 160 GiB required" >&2
    exit 20
fi

{
    printf 'schema_version\t1\n'
    printf 'started_at\t%s\n' "$(date -Ins)"
    printf 'source_commit\t%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'mem_available_kib\t%s\n' "$available_kib"
    printf 'aggregate_memory_high\t96G\n'
    printf 'aggregate_memory_max\t112G\n'
    printf 'aggregate_swap_max\t0\n'
    printf 'tiles\t%s\n' "${tiles[*]}"
} > "$run_root/admission.tsv"
: > "$run_root/launch_pids.tsv"

vmstat -t 1 > "$run_root/vmstat.log" &
telemetry_pid=$!
pids=()

stop_children() {
    local pid
    for pid in "${pids[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
    kill "$telemetry_pid" 2>/dev/null || true
    wait "$telemetry_pid" 2>/dev/null || true
}
trap stop_children TERM INT EXIT

for tile in "${tiles[@]}"; do
    mkdir -p "$run_root/t${tile}"
    GZZ_AUTHORITATIVE_RUN_ROOT="$run_root" \
        "$root/experiments/scripts/run_gzz_authoritative_point.sh" "$tile" \
        > "$run_root/t${tile}/service.log" 2>&1 &
    pids+=("$!")
    printf '%s\t%s\n' "$tile" "$!" >> "$run_root/launch_pids.tsv"
done

failed=0
: > "$run_root/point_status.tsv"
for index in "${!tiles[@]}"; do
    tile=${tiles[$index]}
    pid=${pids[$index]}
    set +e
    wait "$pid"
    rc=$?
    set -e
    printf '%s\t%s\t%s\n' "$tile" "$pid" "$rc" >> "$run_root/point_status.tsv"
    (( rc == 0 )) || failed=1
done

kill "$telemetry_pid" 2>/dev/null || true
wait "$telemetry_pid" 2>/dev/null || true
trap - TERM INT EXIT

set +e
python3 "$root/experiments/scripts/analyze_gzz_authoritative.py" \
    --run-root "$run_root" --logical-cap 16384
analysis_rc=$?
set -e
if (( failed == 0 && analysis_rc == 0 )); then
    date -Ins > "$run_root/campaign.complete"
    exit 0
fi
printf 'finished_at=%s\npoint_failure=%s\nanalysis_rc=%s\n' \
    "$(date -Ins)" "$failed" "$analysis_rc" > "$run_root/campaign.failed"
exit 1
