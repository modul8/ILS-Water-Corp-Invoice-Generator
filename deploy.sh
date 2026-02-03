#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="/mnt/evo-pool/apps/ils_app_repo/server_app/"
DEST_DIR="/mnt/evo-pool/apps/ils_app/"
APP_OWNER="${APP_OWNER:-}"

log() { echo "[deploy] $*"; }
warn() { echo "[deploy][warn] $*" >&2; }

log "Syncing app files from $SRC_DIR to $DEST_DIR"
rsync -av --delete \
  --no-times --omit-dir-times --no-perms --no-owner --no-group \
  --chmod=Du=rwx,Dg=rx,Do=rx,Fu=rw,Fg=r,Fo=r \
  --exclude 'config.php' \
  --exclude 'assets/' \
  --exclude 'uploads/' \
  "$SRC_DIR" \
  "$DEST_DIR"
log "App sync complete"

# Ensure PHP user can read vendor after sync.
if [ -d "$DEST_DIR/vendor" ]; then
  owner_to_set=""
  if [ -n "$APP_OWNER" ]; then
    owner_to_set="$APP_OWNER"
  elif id -u application >/dev/null 2>&1; then
    owner_to_set="application:application"
  fi
  if [ -n "$owner_to_set" ]; then
    log "Setting vendor ownership to $owner_to_set"
    chown -R "$owner_to_set" "$DEST_DIR/vendor" || warn "chown failed on $DEST_DIR/vendor"
  else
    warn "No APP_OWNER set and user 'application' not found; skipping chown"
  fi
  # On ACL-managed datasets, chmod may be blocked; don't fail deploy.
  log "Ensuring vendor is readable"
  chmod -R u+rwX,go+rX "$DEST_DIR/vendor" || warn "chmod failed on $DEST_DIR/vendor"
else
  warn "Vendor directory not found at $DEST_DIR/vendor"
fi

# Normalize perms for web-readability, including config.php (excluded from rsync).
if [ -f "$DEST_DIR/index.php" ]; then
  chmod 644 "$DEST_DIR/index.php" || warn "chmod failed on $DEST_DIR/index.php"
fi
if [ -f "$DEST_DIR/api/index.php" ]; then
  chmod 644 "$DEST_DIR/api/index.php" || warn "chmod failed on $DEST_DIR/api/index.php"
fi
if [ -f "$DEST_DIR/config.php" ]; then
  chmod 644 "$DEST_DIR/config.php" || warn "chmod failed on $DEST_DIR/config.php"
fi
if [ -d "$DEST_DIR/assets" ]; then
  chmod 755 "$DEST_DIR/assets" || warn "chmod failed on $DEST_DIR/assets"
  chmod -R o+rX "$DEST_DIR/assets" || warn "chmod failed on $DEST_DIR/assets (recursive)"
fi
if [ -d "$DEST_DIR/uploads" ]; then
  chmod 755 "$DEST_DIR/uploads" || warn "chmod failed on $DEST_DIR/uploads"
  chmod -R u+rwX,go+rX "$DEST_DIR/uploads" || warn "chmod failed on $DEST_DIR/uploads (recursive)"
fi

# Sync assets separately so upgrades can refresh them while still keeping config.php.
if [ -d "$SRC_DIR/assets" ]; then
  log "Syncing assets"
  rsync -av --delete \
    --no-times --omit-dir-times --no-perms --no-owner --no-group \
    --chmod=Du=rwx,Dg=rx,Do=rx,Fu=rw,Fg=r,Fo=r \
    "$SRC_DIR/assets/" \
    "$DEST_DIR/assets/"
  log "Assets sync complete"
else
  warn "Skipping assets sync (missing: $SRC_DIR/assets)"
fi
