#!/usr/bin/env bash
# Refresh the agent's view of the world and rebuild the dashboard.
#
#   pcs-refresh.sh propose   full screen + sizing + proposals   (slow, ~5 min)
#   pcs-refresh.sh mark      re-price open positions, take due exits (fast)
#
# What `propose` does at the end depends on this account's configuration:
#
#   auto_approve = off  ->  writes tickets and stops. Opening is a human action.
#   auto_approve = on   ->  opens every clear proposal itself, PAPER ONLY.
#
# Check which one this box is running before enabling the timer:
#     ./.venv/bin/python run.py config
#
# Either way there is no live path: `open_approved` refuses a non-paper ledger
# in code, and no broker credentials belong on this instance.
set -euo pipefail

APP="${APP:-/opt/pcs}"
cd "$APP"

TASK="${1:-mark}"
LOG="$APP/logs/${TASK}.log"
mkdir -p "$APP/logs"

{
  echo "=== $(date -Is) $TASK ==="
  case "$TASK" in
    propose) ./.venv/bin/python run.py propose ;;
    mark)    ./.venv/bin/python run.py mark ;;
    screen)  ./.venv/bin/python run.py screen ;;
    *) echo "unknown task: $TASK" >&2; exit 64 ;;
  esac
} >>"$LOG" 2>&1

# Publish the dashboard where nginx can read it.
install -m 0644 "$APP/dashboard.html" /var/www/pcs/index.html
