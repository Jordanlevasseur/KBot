#!/bin/sh
# Fail closed: no fallback if namespaces or bubblewrap are unavailable.
set -eu
umask 077
command -v bwrap >/dev/null 2>&1 || { echo 'bubblewrap is required' >&2; exit 1; }
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
STATE_DIR=${KALSHI_SIM_STATE:-"$ROOT/state"}
mkdir -p "$STATE_DIR"
# A fresh mount namespace exposes only runtime files and this application.
# No host /home, browser profiles, SSH material, environment credentials, or network.
exec bwrap --die-with-parent --new-session --unshare-all --cap-drop ALL \
  --clearenv --setenv PATH /usr/bin:/bin --setenv PYTHONPATH /app \
  --setenv PYTHONDONTWRITEBYTECODE 1 --setenv HOME /tmp \
  --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib \
  --symlink usr/lib64 /lib64 --proc /proc --dev /dev --tmpfs /tmp \
  --dir /etc --ro-bind /etc/ld.so.cache /etc/ld.so.cache \
  --dir /app --ro-bind "$ROOT/kalshi_bot" /app/kalshi_bot \
  --bind "$STATE_DIR" /state --chdir /app \
  /usr/bin/python3 -m kalshi_bot --db /state/simulation.sqlite3 "$@"
