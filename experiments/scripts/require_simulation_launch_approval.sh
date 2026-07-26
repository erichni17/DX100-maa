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

exec "$@"
