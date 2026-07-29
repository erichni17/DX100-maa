#!/usr/bin/env bash
# Refuse every gem5 task unless the parent launch was explicitly user-approved.
set -euo pipefail

if [[ "${DX100_SIMULATION_LAUNCH_APPROVED:-}" != "YES" ]]; then
  echo "simulation launch blocked: explicit user approval was not supplied" >&2
  exit 125
fi
if [[ "${DX100_SIMULATION_PLAN_VERSION:-}" != "tile-final-recovery-v3" ]]; then
  echo "simulation launch blocked: workflow is absent or superseded" >&2
  exit 125
fi

# The v3 GAPBS workflow was loaded before the SSSP SPD-boundary defect was
# fixed, so its final pending task still names the known-bad binary snapshot.
# Keep the already-running BC tasks intact while refusing only that stale SSSP
# launch.  Successor SSSP runs use a different immutable binary SHA.
if [[ "$(basename -- "${1:-}")" == "run_gapbs_tile_smoke.sh" &&
      "${3:-}" == "sssp" && -x "${DX100_GEM5_BIN:-}" ]]; then
  gem5_sha256=$(sha256sum -- "$DX100_GEM5_BIN")
  gem5_sha256=${gem5_sha256%% *}
  if [[ "$gem5_sha256" == \
        "bcc30842a2f26aad2a0cddc769381180f885c683c0be711e2feffb0ac56c18ab" ]]; then
    echo "simulation launch blocked: SSSP binary is superseded by the SPD-boundary fix" >&2
    exit 125
  fi
fi

exec "$@"
