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
[[ -z $(git -C "$root" status --short) ]] || {
    echo "FLAG attribution campaign requires a clean source worktree" >&2
    exit 2
}

mkdir -p "$out/frozen-tools"
runner="$out/frozen-tools/run_xrage_direct_index_smoke.sh"
comparator="$out/frozen-tools/summarize_xrage_comparison.py"
storage_reporter="$out/frozen-tools/report_maa_storage.py"
cp "$root/experiments/scripts/run_xrage_direct_index_smoke.sh" "$runner"
cp "$root/experiments/scripts/summarize_xrage_comparison.py" "$comparator"
cp "$root/experiments/scripts/report_maa_storage.py" "$storage_reporter"
chmod +x "$runner" "$comparator" "$storage_reporter"

{
    printf 'runner_source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'simulator_source_commit=%s\n' "$simulator_commit"
    printf 'execution=sequential\n'
    printf 'timeout=none\n'
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$out/campaign_manifest.txt"
sha256sum "$gem5" "$binary" "$input" "$runner" "$comparator" \
    "$storage_reporter" > "$out/campaign_artifact_sha256.txt"

run_arm() {
    local label=$1
    local arm=$2
    local guest_arm=$3
    local physical=$4
    local native_order=$5
    local index_lines=$6
    DX100_ROOT_OVERRIDE="$root" \
        XRAGE_SIMULATOR_SOURCE_COMMIT="$simulator_commit" \
        XRAGE_ARM="$arm" XRAGE_GUEST_ARM="$guest_arm" \
        MAA_PHYSICAL_TILE_ELEMENTS="$physical" \
        MAA_VIRTUAL_NATIVE_ISSUE_ORDER="$native_order" \
        MAA_VIRTUAL_INDEX_BUFFER_LINES="$index_lines" \
        "$runner" "$gem5" "$binary" "$input" "$out/$label"
}

run_arm native16 native native16 16384 0 1
run_arm fused16 fused fused16 16384 0 1
run_arm fused4 fused_4k fused4 4096 0 1
run_arm compact16 compact compact16 16384 1 1
run_arm direct4 direct_index_4k direct4 4096 1 8

python3 "$comparator" --require-shared-binary --baseline native16 \
    --output-dir "$out/comparison" \
    --pair fusion=native16,fused16 \
    --pair native_tile_shrink=fused16,fused4 \
    --pair compact_bypass=fused16,compact16 \
    --pair direct4_reorder=fused4,direct4 \
    --pair direct4_cost=fused16,direct4 \
    --pair direct4_vs_old_compact=compact16,direct4 \
    "native16=$out/native16" "fused16=$out/fused16" \
    "fused4=$out/fused4" "compact16=$out/compact16" \
    "direct4=$out/direct4"
python3 "$storage_reporter" "$out/direct4/run/config.ini" \
    --mechanism direct-index --dram-subslices 64 \
    --output-dir "$out/storage"
touch "$out/flag_runtime_attribution_campaign.pass"
echo "PASS FLAG runtime attribution campaign: $out"
