#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 GEM5_BIN OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
out=$(realpath -m "$2")

if [[ -e $out ]]; then
    echo "refusing to overwrite existing output path: $out" >&2
    exit 2
fi
mkdir -p "$out"
"$root/experiments/scripts/build_virtual_tile_consumer.sh" "$out/binaries"
binary="$out/binaries/test_virtual_tile_consumer_T16384"

cases=(
    native_16k
    paged_staged_16k
    paged_16k
    paged_4k
    paged_reload_warm_4k
    paged_reload_cold_4k
    native_4k
)
for case_name in "${cases[@]}"; do
    "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" \
        "$gem5" "$binary" "$case_name" "$out/$case_name"
done

head -n 1 "$out/${cases[0]}/result.tsv" > "$out/matrix.tsv"
for case_name in "${cases[@]}"; do
    tail -n 1 "$out/$case_name/result.tsv" >> "$out/matrix.tsv"
done

python3 - "$out" <<'PY'
import csv
import sys
from pathlib import Path

out = Path(sys.argv[1])
with (out / "matrix.tsv").open(newline="", encoding="utf-8") as handle:
    rows = {row["case"]: row for row in csv.DictReader(handle, delimiter="\t")}

hashes = {row["output_hash"] for row in rows.values()}
if len(hashes) != 1:
    raise SystemExit("cache-audit output hashes differ")

ticks = {name: int(row["simTicks"]) for name, row in rows.items()}

def pct(new, old):
    return (new / old - 1.0) * 100.0

comparisons = {
    "staged_warm_vs_native16_pct": pct(ticks["paged_staged_16k"], ticks["native_16k"]),
    "direct_warm16_vs_native16_pct": pct(ticks["paged_16k"], ticks["native_16k"]),
    "direct_warm4_vs_native16_pct": pct(ticks["paged_4k"], ticks["native_16k"]),
    "direct_feeder_delta_at_16k_pct": pct(ticks["paged_16k"], ticks["paged_staged_16k"]),
    "cold_vs_warm_reload_latency_pct": pct(
        ticks["paged_reload_cold_4k"], ticks["paged_reload_warm_4k"]
    ),
}

warm = rows["paged_reload_warm_4k"]
cold = rows["paged_reload_cold_4k"]
if int(warm["l3_read_hits_maa"]) < 2000 or int(warm["l3_read_misses_maa"]) > 16:
    raise SystemExit("warm reload did not remain LLC-resident")
if int(cold["l3_read_misses_maa"]) < 2000:
    raise SystemExit("cold reload did not miss in the LLC")
if int(cold["memory_bytes_read_maa"]) <= int(warm["memory_bytes_read_maa"]):
    raise SystemExit("cold reload did not increase MAA DRAM bytes")
(out / "comparisons.txt").write_text(
    "".join(f"{key}={value:.6f}\n" for key, value in comparisons.items()),
    encoding="utf-8",
)
(out / "virtual_tile_cache_audit.pass").touch()
PY

cat "$out/matrix.tsv"
cat "$out/comparisons.txt"
