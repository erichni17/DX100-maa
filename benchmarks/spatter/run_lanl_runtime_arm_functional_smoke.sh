#!/usr/bin/env bash
# Check every imported LANL gather across all runtime-selectable MAA arms.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BINARY=${1:-$ROOT/build_lanl_func/spatter_maa_xrage_runtime_verify_16K}
DATA_ROOT=${2:-$ROOT/tests/test-data/lanl}
MANIFEST=$DATA_ROOT/manifest.json
ARMS=(native16 fused16 fused4 compact16 direct4)

[[ -x "$BINARY" ]] || { echo "missing runtime MAA binary: $BINARY" >&2; exit 2; }
[[ -f "$MANIFEST" ]] || { echo "missing trace manifest: $MANIFEST" >&2; exit 2; }

passed=0
while IFS=$'\t' read -r config_id input expected_input_hash; do
    path=$DATA_ROOT/$input
    [[ -f "$path" ]] || { echo "missing trace input: $path" >&2; exit 3; }
    actual_input_hash=$(sha256sum "$path" | awk '{print $1}')
    [[ "$actual_input_hash" == "$expected_input_hash" ]] || {
        echo "trace hash mismatch: $config_id" >&2
        exit 3
    }

    reference_result=
    for arm in "${ARMS[@]}"; do
        output=$(mktemp)
        trap 'rm -f "$output"' EXIT
        OMP_NUM_THREADS=4 SPATTER_DATA_SEED=${SPATTER_DATA_SEED:-1} \
            "$BINARY" -b serial -f "$path" --maa-arm "$arm" \
            >"$output" 2>&1
        [[ $(grep -c '^MAA_GATHER_VERIFY_PASS ' "$output" || true) -eq 1 ]] || {
            echo "missing gather verification marker: $config_id/$arm" >&2
            cat "$output" >&2
            exit 4
        }
        if grep -Eiq 'verify_fail|assert|abort|fatal|error:' "$output"; then
            echo "runtime arm smoke reported an error: $config_id/$arm" >&2
            cat "$output" >&2
            exit 4
        fi
        result=$(grep '^MAA_GATHER_VERIFY_PASS ' "$output")
        if [[ -z "$reference_result" ]]; then
            reference_result=$result
        elif [[ "$result" != "$reference_result" ]]; then
            echo "runtime arm result mismatch: $config_id/$arm" >&2
            echo "reference: $reference_result" >&2
            echo "actual:    $result" >&2
            exit 5
        fi
        rm -f "$output"
        trap - EXIT
        passed=$((passed + 1))
        echo "LANL_RUNTIME_ARM_FUNCTIONAL_PASS id=$config_id arm=$arm"
    done
done < <(jq -r '.configurations[] | select(.kernel == "gather") | [.id, .input, .input_sha256] | @tsv' "$MANIFEST")

expected=$(( $(jq '[.configurations[] | select(.kernel == "gather")] | length' "$MANIFEST") * ${#ARMS[@]} ))
[[ "$passed" -eq "$expected" ]] || {
    echo "expected $expected gather/arm cases, ran $passed" >&2
    exit 6
}
echo "LANL_RUNTIME_ARM_FUNCTIONAL_SMOKE_PASS cases=$passed"
