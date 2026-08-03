#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "$0")/.." && pwd)/scripts/isoarea_pingpong_layout.sh"
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
printf 'VIRTUAL_TILE_CONSUMER_LAYOUT mode=transparent page_elements=4096 logical_elements=16384 mem_size=2147483648\n' > "$d/local"
printf 'VIRTUAL_TILE_CONSUMER_LAYOUT mode=deferred page_elements=0 logical_elements=16384 mem_size=2147483648\n' > "$d/shared"
isoarea_validate_layout "$d/local" transparent 4096
isoarea_validate_layout "$d/shared" deferred 0
isoarea_validate_layout "$d/shared" transparent 4096 && exit 1 || true
