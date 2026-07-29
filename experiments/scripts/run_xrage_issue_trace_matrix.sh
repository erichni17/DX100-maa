#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "usage: $0 GEM5_BIN XRAGE_BIN INPUT_JSON OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
runner="$root/experiments/scripts/run_xrage_direct_index_smoke.sh"
analyzer="$root/experiments/scripts/analyze_xrage_issue_trace.py"
comparator="$root/experiments/scripts/summarize_xrage_comparison.py"
gem5=$(realpath "$1")
binary=$(realpath "$2")
input=$(realpath "$3")
out=$(realpath -m "$4")
max_parallel=${XRAGE_TRACE_MAX_PARALLEL:-3}
source_commit=$(git -C "$root" rev-parse HEAD)

[[ -x $gem5 && -x $binary && -f $input ]] || {
    echo "missing gem5, XRAGE binary, or input" >&2
    exit 2
}
[[ $max_parallel =~ ^[1-3]$ ]] || {
    echo "XRAGE_TRACE_MAX_PARALLEL must be in [1,3]" >&2
    exit 2
}
[[ ! -e $out ]] || {
    echo "refusing to overwrite existing output: $out" >&2
    exit 2
}
[[ -z $(git -C "$root" status --short) ]] || {
    echo "XRAGE issue trace requires a clean source worktree" >&2
    exit 2
}

mkdir -p "$out"
{
    printf 'source_commit=%s\n' "$source_commit"
    printf 'gem5=%s\n' "$gem5"
    printf 'binary=%s\n' "$binary"
    printf 'input=%s\n' "$input"
    printf 'max_parallel=%s\n' "$max_parallel"
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$out/manifest.txt"
sha256sum "$gem5" "$binary" "$input" "$runner" "$analyzer" \
    "$comparator" > "$out/artifact_sha256.txt"

run_arm() {
    local label=$1
    local arm=$2
    local guest_arm=$3
    local physical=$4
    local index_lines=$5
    XRAGE_ARM="$arm" XRAGE_GUEST_ARM="$guest_arm" \
        MAA_PHYSICAL_TILE_ELEMENTS="$physical" \
        MAA_VIRTUAL_INDEX_BUFFER_LINES="$index_lines" \
        XRAGE_DEBUG_FLAGS=MAAIssueTrace \
        XRAGE_SIMULATOR_SOURCE_COMMIT="$source_commit" \
        "$runner" "$gem5" "$binary" "$input" "$out/$label"
}

labels=(fused16 compact16 direct4)
pids=()
failures=0
for label in "${labels[@]}"; do
    case "$label" in
        fused16) args=(fused fused16 16384 1) ;;
        compact16) args=(compact compact16 16384 1) ;;
        direct4) args=(direct_index_4k direct4 4096 8) ;;
    esac
    run_arm "$label" "${args[@]}" &
    pids+=("$!")
    if [[ ${#pids[@]} -eq $max_parallel ]]; then
        for pid in "${pids[@]}"; do
            if ! wait "$pid"; then
                failures=$((failures + 1))
            fi
        done
        pids=()
    fi
done
for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
        failures=$((failures + 1))
    fi
done
[[ $failures -eq 0 ]] || {
    echo "$failures XRAGE issue-trace arms failed" >&2
    exit 1
}

python3 "$comparator" --require-shared-binary --baseline fused16 \
    --output-dir "$out/comparison" \
    fused16="$out/fused16" compact16="$out/compact16" \
    direct4="$out/direct4"
python3 "$analyzer" --baseline fused16 \
    --output "$out/issue_order.tsv" \
    fused16="$out/fused16/run/xrage-debug.log" \
    compact16="$out/compact16/run/xrage-debug.log" \
    direct4="$out/direct4/run/xrage-debug.log"

touch "$out/xrage_issue_trace_matrix.pass"
echo "PASS XRAGE issue-trace matrix: $out"
