#!/usr/bin/env bash
# Dashboard logins -- add, rotate, list and revoke.
#
#   sudo ./deploy/viewer.sh add tester         generate a password and print it once
#   sudo ./deploy/viewer.sh add tester --password s3cret   ... or set one yourself
#   sudo ./deploy/viewer.sh list               who can log in
#   sudo ./deploy/viewer.sh remove tester      revoke
#
# A thin wrapper over `run.py viewer`, which holds the actual logic and is
# covered by the test suite. This exists so the command is findable next to the
# other deploy scripts and so the file permissions are fixed up afterwards --
# the login service reads /etc/pcs/viewers as the pcs user.
#
# Every login sees the SAME page. There is no admin tier and no per-user view:
# nginx serves one static file and the only dynamic endpoint is the login
# itself, so a viewer can read the account and do nothing else -- not approve a
# trade, not change a setting, not reach the agent.
#
# What they WILL see: the account label, cash, positions, every closed trade
# and the full event log. That is a paper account, but decide it is fine to
# share before you share it -- there is no redaction mode.
set -euo pipefail

APP=/opt/pcs
VIEWERS=/etc/pcs/viewers
SVC_USER=pcs

case "${1:-}" in
  add|list|remove) ;;
  *) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 64 ;;
esac

[ "$(id -u)" -eq 0 ] || { echo "error: run with sudo -- this writes $VIEWERS" >&2; exit 1; }

install -d -m 0750 /etc/pcs
"$APP/.venv/bin/python" "$APP/run.py" viewer "$@"
rc=$?

# Ownership is restored after every write: run.py creates the file as root, and
# the login service reads it as pcs.
if [ -f "$VIEWERS" ]; then
  chown root:"$SVC_USER" "$VIEWERS" 2>/dev/null || true
  chmod 640 "$VIEWERS"
fi
exit "$rc"
