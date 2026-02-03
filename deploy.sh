#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="/mnt/evo-pool/apps/ils_app_repo/server_app/"
DEST_DIR="/mnt/evo-pool/apps/ils_app/"
APP_OWNER="${APP_OWNER:-}"

rsync -av --delete \
  --no-times --omit-dir-times --no-perms --no-owner --no-group \
  --exclude 'config.php' \
  --exclude 'assets/' \
  --exclude 'uploads/' \
  "$SRC_DIR" \
  "$DEST_DIR"

# Ensure PHP user can read vendor after sync.
if [ -d "$DEST_DIR/vendor" ]; then
  if [ -n "$APP_OWNER" ]; then
    chown -R "$APP_OWNER" "$DEST_DIR/vendor" || true
  elif id -u application >/dev/null 2>&1; then
    chown -R application:application "$DEST_DIR/vendor" || true
  fi
  # On ACL-managed datasets, chmod may be blocked; don't fail deploy.
  chmod -R u+rwX,go+rX "$DEST_DIR/vendor" || true
fi

# Sync assets separately so upgrades can refresh them while still keeping config.php.
if [ -d "$SRC_DIR/assets" ]; then
  rsync -av --delete \
    --no-times --omit-dir-times --no-perms --no-owner --no-group \
    "$SRC_DIR/assets/" \
    "$DEST_DIR/assets/"
else
  echo "Skipping assets sync (missing: $SRC_DIR/assets)"
fi
