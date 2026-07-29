#!/usr/bin/env bash
# Validate imported LANL trace results with DX100's functional MAA model.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BINARY=${1:-$ROOT/build_lanl_func/spatter_maa_flag_verify_16K}
DATA_ROOT=${2:-$ROOT/tests/test-data/lanl}
MANIFEST=$DATA_ROOT/manifest.json

[[ -x "$BINARY" ]] || { echo "missing functional MAA binary: $BINARY" >&2; exit 2; }
[[ -f "$MANIFEST" ]] || { echo "missing trace manifest: $MANIFEST" >&2; exit 2; }

passed=0
while IFS=$'\t' read -r config_id kernel input expected_hash; do
    path=$DATA_ROOT/$input
    [[ -f "$path" ]] || { echo "missing trace input: $path" >&2; exit 3; }
    actual_hash=$(sha256sum "$path" | awk '{print $1}')
    [[ "$actual_hash" == "$expected_hash" ]] || {
        echo "trace hash mismatch: $config_id" >&2
        exit 3
    }
    output=$(mktemp)
    trap 'rm -f "$output"' EXIT
    OMP_NUM_THREADS=4 SPATTER_DATA_SEED=${SPATTER_DATA_SEED:-1} \
        "$BINARY" -b serial -f "$path" >"$output" 2>&1
    marker=MAA_${kernel^^}_VERIFY_PASS
    [[ $(grep -c "^$marker " "$output" || true) -eq 1 ]] || {
        echo "missing verification marker $marker: $config_id" >&2
        cat "$output" >&2
        exit 4
    }
    if grep -Eiq 'verify_fail|assert|abort|fatal|error:' "$output"; then
        echo "functional MAA smoke reported an error: $config_id" >&2
        cat "$output" >&2
        exit 4
    fi
    rm -f "$output"
    trap - EXIT
    passed=$((passed + 1))
    echo "LANL_TRACE_FUNCTIONAL_CONFIG_PASS id=$config_id"
done < <(jq -r '.configurations[] | [.id, .kernel, .input, .input_sha256] | @tsv' "$MANIFEST")

expected=$(jq '.configurations | length' "$MANIFEST")
[[ "$passed" -eq "$expected" ]] || {
    echo "expected $expected configurations, ran $passed" >&2
    exit 5
}
echo "LANL_TRACE_FUNCTIONAL_SMOKE_PASS configs=$passed"
