#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 TILE" >&2
    exit 2
fi

tile=$1
case "$tile" in
    1024|2048|4096|8192|16384|32768|65536) ;;
    *) echo "unsupported GZZ tile: $tile" >&2; exit 2 ;;
esac

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
runtime_root=${DX100_RUNTIME_ROOT:-/data1/nier/DX100}
run_root=${GZZ_AUTHORITATIVE_RUN_ROOT:-/data1/nier/dx100-runs/2026-07-20-full-tile-sweep/gzz_authoritative_20260803}
point_root="$run_root/t${tile}"
mkdir -p "$point_root"

{
    printf 'schema_version=1\n'
    printf 'source_root=%s\n' "$root"
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'runtime_root=%s\n' "$runtime_root"
    printf 'physical_tile=%s\n' "$tile"
    printf 'logical_chunk=%s\n' "$tile"
    printf 'treatment=native\n'
    printf 'started_at=%s\n' "$(date -Ins)"
} > "$point_root/treatment.txt"

export DX100_SOURCE_ROOT="$root"
export DX100_RUNTIME_ROOT="$runtime_root"
export CAMPAIGN_ROOT="$point_root"
export CHECKPOINT_ROOT="$point_root/checkpoints"
export RESTORE_TIMEOUT=0
export CKPT_TIMEOUT=0
export PROG_INTERVAL=0
unset GZZ_EXTRA_CXX_FLAGS GZZ_DEBUG_FLAGS GZZ_DEBUG_FILE

set +e
"$root/benchmarks/UME/run_ume_tile_smoke.sh" \
    gem5.opt.ovl_base gradzatz "$tile" 1000000 2GB 0 0 0
rc=$?
set -e

binary="$root/benchmarks/UME/gradzatz_maa_$((tile / 1024))K"
if [[ -f "$binary" ]]; then
    binary_sha=$(sha256sum -- "$binary")
    printf 'benchmark_sha256=%s\n' "${binary_sha%% *}" >> "$point_root/treatment.txt"
fi
printf 'finished_at=%s\nwrapper_rc=%s\n' "$(date -Ins)" "$rc" >> "$point_root/treatment.txt"
exit "$rc"
