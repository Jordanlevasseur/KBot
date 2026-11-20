#!/bin/sh
# Offline preflight is the default. No account connection without --connect.
set -eu
umask 077
if [ "$#" -lt 2 ]; then
  echo 'Usage: run-account.sh PRIVATE_CREDENTIAL_DIRECTORY demo|production [--connect] [--once]' >&2
  exit 2
fi
CREDENTIALS=$(realpath -e -- "$1")
ENVIRONMENT=$2
shift 2
case "$ENVIRONMENT" in demo|production) ;; *) echo 'Invalid environment' >&2; exit 2 ;; esac
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
STATE_DIR=${KALSHI_ACCOUNT_STATE:-"$ROOT/state/account-$ENVIRONMENT"}
GATEWAY_DIR=/run/kalshi-$ENVIRONMENT
mkdir -p "$STATE_DIR"
command -v bwrap >/dev/null 2>&1 || { echo 'bubblewrap is required' >&2; exit 1; }
# Default preflight needs no gateway installation and makes no connection.
if [ "$#" -eq 0 ]; then set -- --preflight; fi
if [ -d "$GATEWAY_DIR" ]; then
  set -- --ro-bind "$GATEWAY_DIR" /gateway -- "$@"
else
  set -- --dir /gateway -- "$@"
fi
# Split bwrap arguments from account arguments without evaluating shell text.
MOUNT_KIND=$1
MOUNT_SOURCE=$2
shift 2
if [ "$MOUNT_KIND" = '--ro-bind' ]; then shift; fi
shift # separator
if [ "$MOUNT_KIND" = '--ro-bind' ]; then
  exec bwrap --die-with-parent --new-session --unshare-all --cap-drop ALL \
    --clearenv --setenv PATH /usr/bin:/bin --setenv PYTHONPATH /app \
    --setenv PYTHONDONTWRITEBYTECODE 1 --setenv HOME /tmp \
    --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib \
    --symlink usr/lib64 /lib64 --proc /proc --dev /dev --tmpfs /tmp \
    --dir /etc --ro-bind /etc/ld.so.cache /etc/ld.so.cache \
    --dir /app --ro-bind "$ROOT/kalshi_bot" /app/kalshi_bot \
    --ro-bind "$CREDENTIALS" /credentials --bind "$STATE_DIR" /state \
    --ro-bind "$MOUNT_SOURCE" /gateway --chdir /app \
    /usr/bin/python3 -m kalshi_bot.account --environment "$ENVIRONMENT" "$@"
else
  exec bwrap --die-with-parent --new-session --unshare-all --cap-drop ALL \
    --clearenv --setenv PATH /usr/bin:/bin --setenv PYTHONPATH /app \
    --setenv PYTHONDONTWRITEBYTECODE 1 --setenv HOME /tmp \
    --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib \
    --symlink usr/lib64 /lib64 --proc /proc --dev /dev --tmpfs /tmp \
    --dir /etc --ro-bind /etc/ld.so.cache /etc/ld.so.cache \
    --dir /app --ro-bind "$ROOT/kalshi_bot" /app/kalshi_bot \
    --ro-bind "$CREDENTIALS" /credentials --bind "$STATE_DIR" /state \
    --dir /gateway --chdir /app \
    /usr/bin/python3 -m kalshi_bot.account --environment "$ENVIRONMENT" "$@"
fi
