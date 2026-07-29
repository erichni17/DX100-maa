#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "usage: $0 GEM5_BIN XRAGE_BIN INPUT_JSON OUTDIR" >&2
    exit 2
fi

if [[ -n ${DX100_ROOT_OVERRIDE:-} ]]; then
    root=$(realpath "$DX100_ROOT_OVERRIDE")
else
    root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
fi
gem5=$(realpath "$1")
binary=$(realpath "$2")
input=$(realpath "$3")
out=$(realpath -m "$4")
simulator_commit=${XRAGE_SIMULATOR_SOURCE_COMMIT:-}
reuse_root=${FLAG_ATTRIBUTION_REUSE_RUNS_ROOT:-}

[[ $simulator_commit =~ ^[0-9a-f]{40}$ ]] || {
    echo "XRAGE_SIMULATOR_SOURCE_COMMIT must be a full Git commit" >&2
    exit 2
}
[[ -x $gem5 && -x $binary && -f $input ]] || {
    echo "missing gem5, XRAGE binary, or input" >&2
    exit 2
}
[[ ! -e $out ]] || {
    echo "refusing to overwrite existing campaign output: $out" >&2
    exit 2
}
if [[ -n $reuse_root ]]; then
    reuse_root=$(realpath "$reuse_root")
    for label in native16 fused16; do
        [[ -f $reuse_root/$label/xrage_attribution_smoke.pass ]] || {
            echo "reused $label arm is not validated: $reuse_root/$label" >&2
            exit 2
        }
    done
fi
[[ -z $(git -C "$root" status --short) ]] || {
    echo "FLAG attribution campaign requires a clean source worktree" >&2
    exit 2
}

mkdir -p "$out/frozen-tools"
runner="$out/frozen-tools/run_xrage_direct_index_smoke.sh"
comparator="$out/frozen-tools/summarize_xrage_comparison.py"
dram_parser="$out/frozen-tools/summarize_xrage_dram.py"
storage_reporter="$out/frozen-tools/report_maa_storage.py"
cp "$root/experiments/scripts/run_xrage_direct_index_smoke.sh" "$runner"
cp "$root/experiments/scripts/summarize_xrage_comparison.py" "$comparator"
cp "$root/experiments/scripts/summarize_xrage_dram.py" "$dram_parser"
cp "$root/experiments/scripts/report_maa_storage.py" "$storage_reporter"
chmod +x "$runner" "$comparator" "$dram_parser" "$storage_reporter"

{
    printf 'runner_source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'simulator_source_commit=%s\n' "$simulator_commit"
    printf 'execution=sequential\n'
    printf 'timeout=none\n'
    printf 'reused_runs_root=%s\n' "$reuse_root"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$out/campaign_manifest.txt"
sha256sum "$gem5" "$binary" "$input" "$runner" "$comparator" \
    "$dram_parser" "$storage_reporter" \
    > "$out/campaign_artifact_sha256.txt"

run_arm() {
    local label=$1
    local arm=$2
    local guest_arm=$3
    local physical=$4
    local native_order=$5
    local index_lines=$6
    local logical=$7
    DX100_ROOT_OVERRIDE="$root" \
        XRAGE_SIMULATOR_SOURCE_COMMIT="$simulator_commit" \
        XRAGE_ARM="$arm" XRAGE_GUEST_ARM="$guest_arm" \
        MAA_PHYSICAL_TILE_ELEMENTS="$physical" \
        MAA_LOGICAL_TILE_ELEMENTS_OVERRIDE="$logical" \
        MAA_GUEST_ABI_TILE_ELEMENTS="$logical" \
        MAA_VIRTUAL_NATIVE_ISSUE_ORDER="$native_order" \
        MAA_VIRTUAL_INDEX_BUFFER_LINES="$index_lines" \
        "$runner" "$gem5" "$binary" "$input" "$out/$label"
}

native16_dir="$out/native16"
fused16_dir="$out/fused16"
if [[ -n $reuse_root ]]; then
    native16_dir="$reuse_root/native16"
    fused16_dir="$reuse_root/fused16"
else
    run_arm native16 native native16 16384 0 1 16384
    run_arm fused16 fused fused16 16384 0 1 16384
fi
# The shared guest binary has a 16K MAA MMIO ABI. This arm shrinks the work
# chunk and physical SPD only; shrinking the logical aperture would move the
# instruction registers and make the guest/simulator memory maps disagree.
run_arm fused4 fused_4k fused4 4096 0 1 16384
run_arm compact16 compact compact16 16384 1 1 16384
run_arm direct4 direct_index_4k direct4 4096 1 8 16384
fused4_dir="$out/fused4"
compact16_dir="$out/compact16"
direct4_dir="$out/direct4"

python3 "$comparator" --require-shared-binary --baseline native16 \
    --output-dir "$out/comparison" \
    --pair fusion=native16,fused16 \
    --pair native_tile_shrink=fused16,fused4 \
    --pair compact_bypass=fused16,compact16 \
    --pair direct4_reorder=fused4,direct4 \
    --pair direct4_cost=fused16,direct4 \
    --pair direct4_vs_old_compact=compact16,direct4 \
    "native16=$native16_dir" "fused16=$fused16_dir" \
    "fused4=$fused4_dir" "compact16=$compact16_dir" \
    "direct4=$direct4_dir"
python3 "$storage_reporter" "$direct4_dir/run/config.ini" \
    --mechanism direct-index --dram-subslices 32 \
    --output-dir "$out/storage"
touch "$out/flag_runtime_attribution_campaign.pass"
echo "PASS FLAG runtime attribution campaign: $out"
