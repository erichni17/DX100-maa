#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "${script_dir}/../.." && pwd)
probe_source="${repo_root}/experiments/probes/umt_ordered_wave_stream_resource_probe.cc"
csv_output="${1:-${repo_root}/experiments/analysis/umt_ordered_wave_stream_resource_sweep_2026-08-09.csv}"
json_output="${2:-${repo_root}/experiments/analysis/umt_ordered_wave_stream_resource_sweep_2026-08-09.json}"
probe_tmp_dir=$(mktemp -d /tmp/umt-stream-resource-probe.XXXXXX)
probe_binary="${probe_tmp_dir}/umt_ordered_wave_stream_resource_probe"

cleanup()
{
    rm -f -- "${probe_binary}"
    rmdir -- "${probe_tmp_dir}"
}
trap cleanup EXIT

g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -Werror \
    -I"${repo_root}/src" "${probe_source}" -o "${probe_binary}"
"${probe_binary}" > "${csv_output}"
"${probe_binary}" --json > "${json_output}"

printf 'wrote %s\nwrote %s\n' "${csv_output}" "${json_output}"
