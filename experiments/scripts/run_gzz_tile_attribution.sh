#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 PHYSICAL_TILE native|LOGICAL_TILE" >&2
    exit 2
fi

physical=$1
treatment=$2
case "$physical" in
    16384|32768|65536) ;;
    *) echo "unsupported physical tile: $physical" >&2; exit 2 ;;
esac

extra_flags=
if [[ "$treatment" == native ]]; then
    logical=$physical
    cohort="native_p${physical}"
else
    logical=$treatment
    [[ "$logical" =~ ^[0-9]+$ ]] || {
        echo "logical tile must be a positive integer" >&2
        exit 2
    }
    (( logical > 0 && logical <= physical )) || {
        echo "logical tile must be in (0, physical]" >&2
        exit 2
    }
    extra_flags="-DGZZ_LOGICAL_CHUNK_SIZE=${logical}"
    cohort="logical_p${physical}_l${logical}"
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
runtime_root=/data1/nier/DX100
run_root=${GZZ_ATTRIBUTION_RUN_ROOT:-/data1/nier/dx100-runs/2026-08-03-gzz-tile-attribution-v2}
campaign_root="$run_root/$cohort"
mkdir -p "$campaign_root"

{
    printf 'source_root=%s\n' "$root"
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'runtime_root=%s\n' "$runtime_root"
    printf 'physical_tile=%s\n' "$physical"
    printf 'logical_chunk=%s\n' "$logical"
    printf 'extra_flags=%s\n' "$extra_flags"
    printf 'started_at=%s\n' "$(date -Ins)"
} > "$campaign_root/treatment.txt"

export DX100_SOURCE_ROOT="$root"
export DX100_RUNTIME_ROOT="$runtime_root"
export CAMPAIGN_ROOT="$campaign_root"
# A gem5 SE checkpoint contains the loaded executable image.  A checkpoint
# produced by a differently-built GZZ binary can restore successfully and then
# fault in unrelated libc instructions.  Keep checkpoints cohort-local so a
# treatment can never restore a checkpoint made from another executable.
export CHECKPOINT_ROOT="$campaign_root/checkpoints"
export GZZ_EXTRA_CXX_FLAGS="$extra_flags"
export GZZ_DEBUG_FLAGS=MAAController
export GZZ_DEBUG_FILE=maa_controller.trace
export RESTORE_TIMEOUT=0
export CKPT_TIMEOUT=0
export PROG_INTERVAL=0

exec "$root/benchmarks/UME/run_ume_tile_smoke.sh" \
    gem5.opt.ovl_base gradzatz "$physical" 1000000 2GB 0 0 0
