#!/usr/bin/env bash
# Fetch only the reviewed official source, verify commit, then apply the reviewed patch.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REV="$(cat "$ROOT/deploy/vexa/UPSTREAM_REVISION")"
DEST="${1:-$ROOT/vendor/vexa}"
if [[ -e "$DEST" ]]; then
  echo "Refusing to overwrite $DEST. Verify it with deploy/vexa/verify_vendor.py, or choose a new destination." >&2
  exit 2
fi
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT
git -C "$TMP" init -q
git -C "$TMP" remote add origin https://github.com/Vexa-ai/vexa.git
git -C "$TMP" fetch --depth 1 origin "$REV"
test "$(git -C "$TMP" rev-parse FETCH_HEAD)" = "$REV"
mkdir -p "$DEST"
git -C "$TMP" archive FETCH_HEAD | tar -x -C "$DEST"
git -C "$DEST" apply --check "$ROOT/deploy/vexa/local-only.patch"
git -C "$DEST" apply "$ROOT/deploy/vexa/local-only.patch"
mkdir -p "$DEST/.local-build"
cp "$ROOT/deploy/vexa/Dockerfile.bot" "$DEST/.local-build/Dockerfile.bot"
printf '%s\n' "$REV" > "$DEST/.local-build/UPSTREAM_REVISION"
sha256sum "$ROOT/deploy/vexa/local-only.patch" | cut -d' ' -f1 > "$DEST/.local-build/PATCH_SHA256"
python3 "$ROOT/deploy/vexa/verify_vendor.py" "$DEST"
echo "Pinned and hardened Vexa source prepared at $DEST. No services were started."
