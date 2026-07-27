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
"$root/experiments/scripts/build_virtual_tile_attribution.sh" \
    "$out/binaries"

cases=(
    native_unfused_16k
    native_fused_16k
    virtual_index_16k_physical_16k
    virtual_index_16k_physical_4k
    native_fused_4k
)
for case_name in "${cases[@]}"; do
    binary="$out/binaries/test_virtual_tile_attribution_T16384"
    if [[ $case_name == native_fused_4k ]]; then
        binary="$out/binaries/test_virtual_tile_attribution_T4096"
    fi
    "$root/experiments/scripts/run_virtual_tile_attribution_prefetch_case.sh" \
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
if len({row["output_hash"] for row in rows.values()}) != 1:
    raise SystemExit("attribution matrix output hashes differ")

ticks = {name: int(row["simTicks"]) for name, row in rows.items()}
def latency_pct(new, old):
    return (new / old - 1.0) * 100.0
def throughput_pct(new, old):
    return (old / new - 1.0) * 100.0

a = ticks["native_unfused_16k"]
b = ticks["native_fused_16k"]
e = ticks["virtual_index_16k_physical_16k"]
c = ticks["virtual_index_16k_physical_4k"]
d = ticks["native_fused_4k"]
lines = [
    f"native_unfused_ticks={a}",
    f"native_fused_ticks={b}",
    f"virtual_16k_physical_16k_ticks={e}",
    f"virtual_16k_physical_4k_ticks={c}",
    f"native_4k_chunks_ticks={d}",
    f"native_fusion_latency_change_pct={latency_pct(b, a):.6f}",
    f"virtual_vs_native16_latency_change_pct={latency_pct(c, b):.6f}",
    f"virtual_vs_native16_throughput_change_pct={throughput_pct(c, b):.6f}",
    f"native4k_vs_native16_latency_change_pct={latency_pct(d, b):.6f}",
    f"virtual_vs_native4k_latency_change_pct={latency_pct(c, d):.6f}",
    f"virtual_vs_native4k_throughput_change_pct={throughput_pct(c, d):.6f}",
]
(out / "comparisons.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
(out / "virtual_tile_attribution_matrix.pass").touch()
PY

cat "$out/matrix.tsv"
cat "$out/comparisons.txt"
