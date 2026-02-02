#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="/mnt/evo-pool/apps/ils_app_repo/server_app/"
DEST_DIR="/mnt/evo-pool/apps/ils_app/"

rsync -av --delete \
  --no-times --omit-dir-times --no-perms --no-owner --no-group \
  --exclude 'config.php' \
  --exclude 'assets/' \
  "$SRC_DIR" \
  "$DEST_DIR"
