#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="/mnt/evo-pool/apps/ils_app_repo/server_app/"
DEST_DIR="/mnt/evo-pool/apps/ils_app/"

rsync -av --delete \
  --no-times --omit-dir-times --no-perms --no-owner --no-group \
  --exclude 'config.php' \
  --exclude 'assets/' \
  --exclude 'uploads/' \
  "$SRC_DIR" \
  "$DEST_DIR"

# Sync assets separately so upgrades can refresh them while still keeping config.php.
if [ -d "$SRC_DIR/assets" ]; then
  rsync -av --delete \
    --no-times --omit-dir-times --no-perms --no-owner --no-group \
    "$SRC_DIR/assets/" \
    "$DEST_DIR/assets/"
else
  echo "Skipping assets sync (missing: $SRC_DIR/assets)"
fi
