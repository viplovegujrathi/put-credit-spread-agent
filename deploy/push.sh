#!/usr/bin/env bash
# Deploy from this laptop to the instance. Run from the repo root:
#
#   ./deploy/push.sh pcs-agent                      # code + timers, no web
#   PCS_DOMAIN=pcs.duckdns.org ./deploy/push.sh pcs-agent   # ... and nginx + TLS
#
# `$1` is an ssh host alias from ~/.ssh/config (or user@ip).
set -euo pipefail

HOST="${1:?usage: ./deploy/push.sh <ssh-host> }"
DOMAIN="${PCS_DOMAIN:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Ship the tests too, and run them on the box. A deploy that cannot prove the
# rules still hold on the machine that will be trading is just a file copy.
echo "== syncing $ROOT -> $HOST:/tmp/pcs-src"
rsync -az --delete \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude '.ruff_cache' --exclude 'logs' \
  --exclude 'data/ledger.json' --exclude 'data/proposals.json' \
  --exclude 'data/settings.json' --exclude 'data/snapshots.json' \
  --exclude 'data/last_screen*.json' \
  "$ROOT"/ "$HOST":/tmp/pcs-src/

echo "== bootstrapping"
# shellcheck disable=SC2029  # $DOMAIN is meant to expand locally
ssh -t "$HOST" "sudo SRC=/tmp/pcs-src PCS_DOMAIN='$DOMAIN' bash /tmp/pcs-src/deploy/bootstrap.sh"

echo "== verifying on the box"
ssh "$HOST" 'cd /opt/pcs && sudo -u pcs ./.venv/bin/python -m pytest -q 2>&1 | tail -3'
ssh "$HOST" 'cd /opt/pcs && sudo -u pcs ./.venv/bin/python run.py config | head -12'
ssh "$HOST" 'systemctl list-timers "pcs-*" --no-pager'
