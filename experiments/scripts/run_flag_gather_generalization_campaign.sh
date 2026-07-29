#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "usage: $0 GEM5_BIN XRAGE_BIN FLAG_MANIFEST OUTDIR" >&2
    exit 2
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
gem5=$(realpath "$1")
binary=$(realpath "$2")
manifest=$(realpath "$3")
out=$(realpath -m "$4")
simulator_commit=${XRAGE_SIMULATOR_SOURCE_COMMIT:-}
max_parallel=${FLAG_MAX_PARALLEL:-2}
reuse_campaign=${FLAG_REUSE_CAMPAIGN:-}
simulator_provenance=${XRAGE_SIMULATOR_PROVENANCE:-$(dirname "$gem5")/manifest.txt}

[[ $simulator_commit =~ ^[0-9a-f]{40}$ ]] || {
    echo "XRAGE_SIMULATOR_SOURCE_COMMIT must be a full Git commit" >&2
    exit 2
}
[[ $max_parallel =~ ^[1-4]$ ]] || {
    echo "FLAG_MAX_PARALLEL must be in [1,4]" >&2
    exit 2
}
[[ -x $gem5 && -x $binary && -f $manifest ]] || {
    echo "missing gem5, FLAG runtime binary, or imported manifest" >&2
    exit 2
}
[[ -f $simulator_provenance &&
   -f $(dirname "$simulator_provenance")/artifact_sha256.txt ]] || {
    echo "missing frozen simulator provenance: $simulator_provenance" >&2
    exit 2
}
[[ ! -e $out ]] || {
    echo "refusing to overwrite FLAG gather campaign: $out" >&2
    exit 2
}
if [[ -n $reuse_campaign ]]; then
    reuse_campaign=$(realpath "$reuse_campaign")
    [[ -f $reuse_campaign/campaign_manifest.txt &&
       -f $reuse_campaign/artifact_sha256.txt &&
       -d $reuse_campaign/cases ]] || {
        echo "invalid FLAG reuse campaign: $reuse_campaign" >&2
        exit 2
    }
    prior_commit=$(awk -F= '$1 == "simulator_source_commit" {print $2}' \
        "$reuse_campaign/campaign_manifest.txt")
    [[ $prior_commit == "$simulator_commit" ]] || {
        echo "FLAG reuse simulator commit mismatch" >&2
        exit 2
    }
    for artifact in "$gem5" "$binary" "$manifest"; do
        artifact_hash=$(sha256sum "$artifact" | cut -d' ' -f1)
        grep -q "^${artifact_hash}  ${artifact}$" \
            "$reuse_campaign/artifact_sha256.txt" || {
            echo "FLAG reuse artifact mismatch: $artifact" >&2
            exit 2
        }
    done
fi
[[ -z $(git -C "$root" status --short) ]] || {
    echo "FLAG gather campaign requires a clean source worktree" >&2
    exit 2
}
while read -r pid; do
    [[ -n $pid && $pid != $$ ]] || continue
    if [[ $(readlink -f "/proc/$pid/exe" 2>/dev/null || true) == "$gem5" ]]; then
        echo "refusing to duplicate a live FLAG gather campaign" >&2
        exit 2
    fi
done < <(pgrep -f "$out" || true)

mapfile -t gathers < <(python3 -c '
import json, pathlib, sys
manifest = pathlib.Path(sys.argv[1])
data = json.loads(manifest.read_text())
rows = [row for row in data["configurations"] if row["kernel"] == "gather"]
if len(rows) != 14:
    raise SystemExit(f"expected 14 FLAG gathers, found {len(rows)}")
for row in rows:
    print("{}\t{}\t{}".format(
        row["id"], manifest.parent / row["input"], row["input_sha256"]
    ))
' "$manifest")
[[ ${#gathers[@]} -eq 14 ]] || {
    echo "failed to load all 14 FLAG gathers from $manifest" >&2
    exit 2
}

mkdir -p "$out/frozen-tools" "$out/cases"
for tool in run_xrage_direct_index_smoke.sh summarize_xrage_comparison.py \
    summarize_xrage_dram.py compare_maa_issue_digests.py \
    summarize_flag_gather_generalization.py; do
    cp "$root/experiments/scripts/$tool" "$out/frozen-tools/$tool"
done
chmod 755 "$out/frozen-tools/"*
{
    printf 'simulator_source_commit=%s\n' "$simulator_commit"
    printf 'runner_source_commit=%s\n' "$(git -C "$root" rev-parse HEAD)"
    printf 'gather_configurations=%s\n' "${#gathers[@]}"
    printf 'max_parallel=%s\n' "$max_parallel"
    printf 'reuse_campaign=%s\n' "$reuse_campaign"
    printf 'simulator_provenance=%s\n' "$simulator_provenance"
    printf 'arms=fused16,compact16,direct4\n'
    printf 'direct_index_buffer_lines=128\n'
    printf 'timeout=none\n'
    printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$out/campaign_manifest.txt"
sha256sum "$gem5" "$binary" "$manifest" "$simulator_provenance" \
    "$(dirname "$simulator_provenance")/artifact_sha256.txt" \
    "$out/frozen-tools/"* \
    > "$out/artifact_sha256.txt"
printf 'configuration\tarm\tsource\tresult_sha256\tmanifest_sha256\tdebug_sha256\n' \
    > "$out/reused_arms.tsv"

validate_case() {
    local case_out=$1
    python3 "$out/frozen-tools/compare_maa_issue_digests.py" \
        --allow-per-instruction-unit-reassignment \
        --baseline fused16 --output-dir "$case_out/issue-comparison" \
        "fused16=$case_out/fused16/run/xrage-debug.log" \
        "compact16=$case_out/compact16/run/xrage-debug.log" \
        "direct4=$case_out/direct4/run/xrage-debug.log"
    python3 "$out/frozen-tools/summarize_xrage_comparison.py" \
        --require-shared-binary --baseline fused16 \
        --simulator-provenance "$simulator_provenance" \
        --output-dir "$case_out/comparison" \
        --pair compact_bypass=fused16,compact16 \
        --pair direct_net=fused16,direct4 \
        --pair direct_vs_compact=compact16,direct4 \
        "fused16=$case_out/fused16" \
        "compact16=$case_out/compact16" \
        "direct4=$case_out/direct4"
    touch "$case_out/flag_gather_case.pass"
}

reuse_case() {
    local config_id=$1 input=$2 expected_hash=$3
    local prior_case="$reuse_campaign/cases/$config_id"
    local case_out="$out/cases/$config_id"
    [[ -d $prior_case ]] || return 1
    for label in fused16 compact16 direct4; do
        [[ -f $prior_case/$label/xrage_attribution_smoke.pass ]] || return 1
    done

    mkdir -p "$case_out"
    for label in fused16 compact16 direct4; do
        local arm="$prior_case/$label"
        local source_commit arm_input input_hash
        source_commit=$(awk -F= '$1 == "source_commit" {print $2}' \
            "$arm/manifest.txt")
        arm_input=$(awk -F= '$1 == "input" {print $2}' "$arm/manifest.txt")
        input_hash=$(sha256sum "$arm/run/xrage-debug.log" | cut -d' ' -f1)
        [[ $source_commit == "$simulator_commit" ]] || {
            echo "reused FLAG arm source mismatch: $config_id/$label" >&2
            return 2
        }
        [[ -f $arm_input &&
           $(sha256sum "$input" | cut -d' ' -f1) == "$expected_hash" &&
           $(sha256sum "$arm_input" | cut -d' ' -f1) == "$expected_hash" ]] || {
            echo "reused FLAG input checksum mismatch: $config_id/$label" >&2
            return 2
        }
        ln -s "$arm" "$case_out/$label"
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$config_id" "$label" "$arm" \
            "$(sha256sum "$arm/result.tsv" | cut -d' ' -f1)" \
            "$(sha256sum "$arm/manifest.txt" | cut -d' ' -f1)" \
            "$input_hash" >> "$out/reused_arms.tsv"
    done
    validate_case "$case_out"
    echo "REUSED FLAG gather case: $config_id"
}

run_case() {
    local config_id=$1 input=$2 expected_hash=$3
    local case_out="$out/cases/$config_id"
    local runner="$out/frozen-tools/run_xrage_direct_index_smoke.sh"
    mkdir -p "$case_out"
    [[ $(sha256sum "$input" | cut -d' ' -f1) == "$expected_hash" ]] || {
        echo "FLAG input checksum mismatch: $config_id" >&2
        return 1
    }

    run_arm() {
        local label=$1 arm=$2 guest=$3 physical=$4 native_order=$5 lines=$6
        DX100_ROOT_OVERRIDE="$root" \
            XRAGE_SIMULATOR_SOURCE_COMMIT="$simulator_commit" \
            XRAGE_ARM="$arm" XRAGE_GUEST_ARM="$guest" \
            XRAGE_DEBUG_FLAGS=MAAIssueDigest \
            MAA_PHYSICAL_TILE_ELEMENTS="$physical" \
            MAA_LOGICAL_TILE_ELEMENTS_OVERRIDE=16384 \
            MAA_GUEST_ABI_TILE_ELEMENTS=16384 \
            MAA_VIRTUAL_NATIVE_ISSUE_ORDER="$native_order" \
            MAA_VIRTUAL_INDEX_BUFFER_LINES="$lines" \
            "$runner" "$gem5" "$binary" "$input" "$case_out/$label"
    }

    run_arm fused16 fused fused16 16384 0 1
    run_arm compact16 compact compact16 16384 1 1
    run_arm direct4 direct_index_4k direct4 4096 1 128
    validate_case "$case_out"
}

batch_pids=()
batch_labels=()
wait_batch() {
    local failed=0
    for idx in "${!batch_pids[@]}"; do
        if ! wait "${batch_pids[$idx]}"; then
            echo "FLAG gather case failed: ${batch_labels[$idx]}" >&2
            failed=1
        fi
    done
    batch_pids=()
    batch_labels=()
    [[ $failed -eq 0 ]]
}

for row in "${gathers[@]}"; do
    IFS=$'\t' read -r config_id input expected_hash <<< "$row"
    if [[ -n $reuse_campaign ]]; then
        if reuse_case "$config_id" "$input" "$expected_hash"; then
            continue
        else
            reuse_rc=$?
            [[ $reuse_rc -eq 1 ]] || exit "$reuse_rc"
        fi
    fi
    run_case "$config_id" "$input" "$expected_hash" &
    batch_pids+=("$!")
    batch_labels+=("$config_id")
    if [[ ${#batch_pids[@]} -eq $max_parallel ]]; then
        wait_batch
    fi
done
if [[ ${#batch_pids[@]} -ne 0 ]]; then
    wait_batch
fi

python3 "$out/frozen-tools/summarize_flag_gather_generalization.py" \
    "$manifest" "$out" "$out/summary"
touch "$out/flag_gather_generalization_campaign.pass"
echo "PASS FLAG gather generalization campaign: $out"
