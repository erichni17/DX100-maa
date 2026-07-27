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

for case_name in native_16k paged_16k paged_4k native_4k; do
    "$root/experiments/scripts/run_virtual_tile_consumer_case.sh" \
        "$gem5" "$binary" "$case_name" "$out/$case_name"
done

head -n 1 "$out/native_16k/result.tsv" > "$out/matrix.tsv"
for case_name in native_16k paged_16k paged_4k native_4k; do
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
    raise SystemExit("consumer matrix output hashes differ")

ticks = {name: int(row["simTicks"]) for name, row in rows.items()}
def pct(new, old):
    return (new / old - 1.0) * 100.0

lines = [
    f"native16_ticks={ticks['native_16k']}",
    f"paged16_ticks={ticks['paged_16k']}",
    f"paged4_ticks={ticks['paged_4k']}",
    f"native4_ticks={ticks['native_4k']}",
    f"paged16_vs_native16_latency_change_pct={pct(ticks['paged_16k'], ticks['native_16k']):.6f}",
    f"paged4_vs_native16_latency_change_pct={pct(ticks['paged_4k'], ticks['native_16k']):.6f}",
    f"paged4_vs_paged16_latency_change_pct={pct(ticks['paged_4k'], ticks['paged_16k']):.6f}",
    f"native4_vs_native16_latency_change_pct={pct(ticks['native_4k'], ticks['native_16k']):.6f}",
    f"paged4_vs_native4_latency_change_pct={pct(ticks['paged_4k'], ticks['native_4k']):.6f}",
    f"paged4_vs_native4_throughput_change_pct={(ticks['native_4k'] / ticks['paged_4k'] - 1.0) * 100.0:.6f}",
]
(out / "comparisons.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
(out / "virtual_tile_consumer_matrix.pass").touch()
PY

cat "$out/matrix.tsv"
cat "$out/comparisons.txt"
