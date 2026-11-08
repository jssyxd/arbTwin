#!/usr/bin/env bash
# Install a credential file as /opt/arbtwin/.env on the trading host.
#
# Usage (on any machine that can reach the host):
#   ./scripts/install_env.sh ./accountbot2.env root@155.254.60.38
#
# The file is copied over SSH, then installed with 640 arbtwin:arbtwin and the
# remote temp copy is shredded.  No secret ever appears on the command line.
set -euo pipefail

SRC="${1:?usage: install_env.sh <local-env-file> [user@host]}"
TARGET="${2:-root@155.254.60.38}"
REMOTE_TMP="/tmp/arbtwin.env.$$"

[[ -f "$SRC" ]] || { echo "no such file: $SRC" >&2; exit 1; }
grep -q '^OKX_API_KEY=' "$SRC" || echo "warning: OKX_API_KEY not present in $SRC" >&2

scp -q "$SRC" "${TARGET}:${REMOTE_TMP}"
ssh "$TARGET" "set -e
  install -m 640 -o arbtwin -g arbtwin '${REMOTE_TMP}' /opt/arbtwin/.env
  shred -u '${REMOTE_TMP}' 2>/dev/null || rm -f '${REMOTE_TMP}'
  echo 'installed: '\$(stat -c '%a %U:%G %n' /opt/arbtwin/.env)
  grep -q '^OKX_API_PASSPHRASE=..*' /opt/arbtwin/.env \\
    && echo 'okx passphrase: SET' || echo 'okx passphrase: MISSING (OKX private API will refuse)'
  systemctl restart arbtwin
  sleep 5
  systemctl is-active arbtwin"