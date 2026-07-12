#!/usr/bin/env bash
# Recover the xRAGE-derived Spatter trace used by the DX100 ISCA artifact.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DEST=$ROOT/tests/test-data/xrage/all.json
ARCHIVE=${TMPDIR:-/tmp}/dx100-xrage.tar.gz
EXPECTED_SHA256=bc556e2e373b7f006331f78aca14834146cb8ef74cf63665c8dbed5c7ec9574d
PRIMARY_URL=https://web.eecs.umich.edu/~arkhadem/projects/xrage.tar.gz
WAYBACK_URL=https://web.archive.org/web/20250530061630id_/http://web.eecs.umich.edu/~arkhadem/projects/xrage.tar.gz

if [[ -f "$DEST" ]] && jq empty "$DEST" >/dev/null 2>&1; then
  echo "[xrage] reusing $DEST"
  exit 0
fi

rm -f "$ARCHIVE"
if ! curl -L --fail --retry 2 --max-time 300 -o "$ARCHIVE" "$PRIMARY_URL"; then
  echo "[xrage] primary URL unavailable; using the Internet Archive capture"
  curl -L --fail --retry 3 --max-time 600 -o "$ARCHIVE" "$WAYBACK_URL"
fi

echo "$EXPECTED_SHA256  $ARCHIVE" | sha256sum -c -
gzip -t "$ARCHIVE"
mkdir -p "$(dirname "$DEST")"
tar -xzf "$ARCHIVE" -C "$(dirname "$DEST")" all.json
jq empty "$DEST"
echo "[xrage] installed $DEST"
