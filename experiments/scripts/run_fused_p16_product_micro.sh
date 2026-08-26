#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
out=$(realpath -m "$1")
gem5="$root/build/X86/gem5.opt"
ramulator=/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/input/libramulator.so
config="$root/configs/deprecated/example/se.py"
ramulator_config="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"
source_file="$root/benchmarks/API/test_fused_p16_product.cpp"
cxx=${CXX:-g++}

[[ -x $gem5 ]] || { echo "missing current gem5: $gem5" >&2; exit 2; }
[[ -f $ramulator ]] || { echo "missing Ramulator: $ramulator" >&2; exit 2; }
[[ ! -e $out || -z $(find "$out" -mindepth 1 -print -quit) ]] || {
    echo "refusing nonempty output: $out" >&2
    exit 2
}
mkdir -p "$out/bin" "$out/checkpoint" "$out/run" "$out/input"

guest="$out/bin/fused_p16_product_micro"
"$cxx" -I"$root/benchmarks/API" -I"$root/include" \
    -I"$root/util/m5/src" -std=c++17 -O3 -Wall -Wextra -Werror \
    -Wno-ignored-qualifiers -Wno-unused-parameter -DGEM5 -DMAA \
    -DNUM_CORES=4 -DNUM_TILES_PER_CORE=8 -DTILE_SIZE=16384 \
    -DMAA_MEM_SIZE=0x80000000 \
    "$root/util/m5/src/abi/x86/m5op.S" "$source_file" -o "$guest"

export LD_LIBRARY_PATH="$(dirname "$ramulator"):${LD_LIBRARY_PATH:-}"
resolved_ramulator=$(ldd "$gem5" | awk '$1 == "libramulator.so" {print $3}')
[[ -n $resolved_ramulator && $(realpath "$resolved_ramulator") == \
   $(realpath "$ramulator") ]] || {
    echo "current gem5 does not resolve the frozen Ramulator" >&2
    exit 2
}

checkpoint_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/checkpoint" "$config"
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 2GB --max-checkpoints=1
    --cmd "$guest"
)
restore_cmd=(
    "$gem5" --listener-mode=off --outdir="$out/run"
    --debug-flags=MAAVirtualTrace --debug-file=fused_p16_trace.log
    "$config" --cpu-type X86O3CPU -r 1 -n 4 --mem-size 2GB
    --checkpoint-dir "$out/checkpoint" --sys-clock 3.2GHz
    --cpu-clock 3.2GHz --caches --l1d_size=32kB --l1d_assoc=8
    --l1d_mshrs=16 --l1d_write_buffers=8 --l1i_size=32kB
    --l1i_assoc=8 --l1i_mshrs=16 --l1i_write_buffers=8 --l2cache
    --l2_size=256kB --l2_assoc=4 --l2_mshrs=32
    --l2_write_buffers=16 --l3cache --l3_size=8MB --l3_assoc=16
    --l3_mshrs=256 --l3_write_buffers=128 --l3_ports=4
    --cacheline_size=64 --mem-type Ramulator2
    --ramulator-config "$ramulator_config" --mem-channels=2 --maa
    --maa_num_maas=1 --maa_num_indirect_units_per_maa=1
    --maa_num_tiles_per_core=8 --maa_num_tile_elements=16384
    --maa_physical_tile_elements=4096 --maa_num_offset_table_entries=16384
    --maa_num_offset_table_epoch_entries=16384
    --maa_num_initial_row_table_slices=32 --maa_virtual_combine_slots=16
    --maa_virtual_combine_ways=4 --maa_virtual_combine_banks=4
    --maa_virtual_words_per_cycle=1 --maa_virtual_response_slots=8
    --maa_virtual_response_words=0 --maa_virtual_response_word_pool=0
    --maa_virtual_max_outstanding_writes=32 --maa_page_fed_soa_jit
    --maa_soa_jit_value_cache_enable --maa_soa_jit_active_value_owners=32
    --maa_soa_jit_value_prefetch_credits=0 --cmd "$guest"
)

git -C "$root" status --short --branch > "$out/input/source_status.before"
sha256sum "$gem5" "$ramulator" "$guest" "$source_file" \
    "$ramulator_config" > "$out/input/artifact_sha256.before"
{
    printf 'schema=dx100.fused_p16_product_micro.v1\n'
    printf 'source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'gem5_sha256=%s\n' "$(sha256sum "$gem5" | awk '{print $1}')"
    printf 'guest_sha256=%s\n' "$(sha256sum "$guest" | awk '{print $1}')"
    printf 'checkpoint_command='; printf '%q ' "${checkpoint_cmd[@]}"; printf '\n'
    printf 'restore_command='; printf '%q ' "${restore_cmd[@]}"; printf '\n'
} > "$out/manifest.txt"

OMP_NUM_THREADS=1 "${checkpoint_cmd[@]}" > "$out/checkpoint.log" 2>&1
grep -Eq '^Exiting @ tick [0-9]+ because checkpoint$' "$out/checkpoint.log"
OMP_NUM_THREADS=1 "${restore_cmd[@]}" > "$out/run/restore.log" 2>&1

restore="$out/run/restore.log"
stats="$out/run/stats.txt"
trace="$out/run/fused_p16_trace.log"
[[ -s $restore && -s $stats && -s $trace ]]
[[ $(grep -Ec '^FUSED_P16_PRODUCT_LAYOUT .*virtual_p_allocation_bytes=0 .*product_publisher_lines=0 .*global_fallbacks=0$' "$restore" || true) -eq 1 ]]
[[ $(grep -Fxc 'FUSED_P16_PRODUCT_PROGRESS producer_complete=1' "$restore" || true) -eq 1 ]]
[[ $(grep -Fxc 'FUSED_P16_PRODUCT_PROGRESS q16_complete=1' "$restore" || true) -eq 1 ]]
[[ $(grep -Fxc 'FUSED_P16_PRODUCT_SENTINELS count=0' "$restore" || true) -eq 1 ]]
result=$(grep '^FUSED_P16_PRODUCT_RESULT ' "$restore")
[[ $(grep -Ec '^FUSED_P16_PRODUCT_RESULT .*errors=0$' "$restore" || true) -eq 1 ]]
field() { sed -n "s/.* $1=\([^ ]*\).*/\1/p" <<<"$result"; }
[[ $(field reference_hash) == $(field product_hash) ]]
[[ $(field product_hash) == $(field q_hash) ]]
python3 - "$restore" <<'PY'
import re
import sys
from pathlib import Path

records = [
    line for line in Path(sys.argv[1]).read_text().splitlines()
    if line.startswith("FUSED_P16_PRODUCT_DUMP ")
]
if len(records) != 256:
    raise SystemExit(f"expected 256 dump records, saw {len(records)}")
for record, expected in zip(records, range(0, 16384, 64)):
    fields = record.split()
    if fields[1] != f"offset={expected}" or len(fields) != 66:
        raise SystemExit(f"malformed dump record at offset {expected}")
    if any(re.fullmatch(r"[0-9a-f]{8}", word) is None for word in fields[2:]):
        raise SystemExit(f"malformed product word at offset {expected}")
PY

stat_sum() {
    awk -v suffix="$1" '
        /^---------- Begin Simulation Statistics/ {section++}
        section == 1 && $1 ~ ("_" suffix "$") {sum += $2; found++}
        /^---------- End Simulation Statistics/ && section == 1 {
            if (!found) exit 2; printf "%.0f\n", sum; exit
        }' "$stats"
}
stat_zero() {
    awk -v suffix="$1" '
        /^---------- Begin Simulation Statistics/ {section++}
        section == 1 && $1 ~ ("_" suffix "$") {sum += $2; found++}
        /^---------- End Simulation Statistics/ && section == 1 {
            printf "%.0f\n", sum; exit
        }' "$stats"
}
[[ $(stat_sum IND_FusedP16Operations) -eq 1 ]]
[[ $(stat_sum IND_FusedP16Epochs) -eq 1 ]]
for ledger in IND_FusedP16SourceOrdinals \
    IND_FusedP16CoefficientDeliveries IND_FusedP16MulAccepts \
    IND_FusedP16MulCompletions IND_FusedP16ProductInsertions \
    IND_FusedP16ProductWriteCompletions; do
    [[ $(stat_sum "$ledger") -eq 16384 ]]
done
coefficient_issues=$(stat_sum IND_FusedP16CoefficientReadIssues)
[[ $coefficient_issues -ge 1024 && $coefficient_issues -le 16384 ]]
[[ $(stat_sum IND_FusedP16CoefficientReadResponses) -eq $coefficient_issues ]]
[[ $(stat_sum IND_FusedP16CoefficientFills) -eq $coefficient_issues ]]
for forbidden in IND_FusedP16EpochDrains IND_FusedP16Fallbacks \
    IND_FusedP16PublisherLines IND_FusedP16VirtualPBytes \
    IND_BoundedGlobalMergeFallbacks IND_NumOTEpochDrain STR_PublishIssues; do
    [[ $(stat_zero "$forbidden") -eq 0 ]]
done
[[ $(stat_sum IND_SoaJitPageFedOperations) -eq 1 ]]
[[ $(stat_sum IND_SoaJitPageFedAdmitCommands) -eq 4 ]]
[[ $(stat_sum IND_SoaJitPageFedCloseCommands) -eq 1 ]]
[[ $(stat_sum IND_SoaJitPageFedCommandResponses) -eq 5 ]]
[[ $(stat_sum IND_SoaJitPageFedAdmittedWords) -eq 16384 ]]
[[ $(stat_sum IND_SoaJitValueDeliveries) -eq 16384 ]]

python3 - "$trace" <<'PY'
import re
import sys
from pathlib import Path

lines = Path(sys.argv[1]).read_text().splitlines()

def addresses(event):
    result = []
    for line in lines:
        if f"event={event} " not in line:
            continue
        match = re.search(r"(?:addr|paddr)=0x([0-9a-f]+)", line)
        if match:
            result.append(int(match.group(1), 16))
    return result

p_issue = addresses("source_issue")
p_response = addresses("source_response")
c_issue = [
    int(m.group(1), 16)
    for line in lines
    if "event=fused_p16_coefficient_request " in line and "action=fill" in line
    for m in [re.search(r"paddr=0x([0-9a-f]+)", line)]
    if m
]
c_response = addresses("fused_p16_coefficient_response")
if len(p_issue) < 2 or sorted(p_issue) != sorted(p_response) or p_issue == p_response:
    raise SystemExit("p responses did not prove out-of-issue-order return")
if len(c_issue) < 2 or sorted(c_issue) != sorted(c_response) or c_issue == c_response:
    raise SystemExit("coefficient responses did not prove out-of-issue-order return")
PY

sim_ticks=$(awk '$1 == "simTicks" {print $2; exit}' "$stats")
[[ $sim_ticks =~ ^[1-9][0-9]*$ ]]
sha256sum "$out/checkpoint"/cpt.*/* > "$out/input/checkpoint_sha256.txt"
sha256sum "$gem5" "$ramulator" "$guest" "$source_file" \
    "$ramulator_config" > "$out/input/artifact_sha256.after"
cmp -s "$out/input/artifact_sha256.before" "$out/input/artifact_sha256.after"
printf 'status=PASS\nsimTicks=%s\nproduct_hash=%s\nq_hash=%s\ncoefficient_read_lines=%s\n' \
    "$sim_ticks" "$(field product_hash)" "$(field q_hash)" \
    "$coefficient_issues" > "$out/result.txt"
cat "$out/result.txt"
