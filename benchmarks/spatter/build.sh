#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
"$ROOT/compile.sh" GEM5
"$ROOT/setup_xrage.sh"
