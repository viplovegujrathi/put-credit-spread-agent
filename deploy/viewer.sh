#!/usr/bin/env bash
# Dashboard logins -- add, rotate, list and revoke.
#
#   sudo ./deploy/viewer.sh add tester         generate a password and print it once
#   sudo ./deploy/viewer.sh add tester --pass s3cret     ... or set one yourself
#   sudo ./deploy/viewer.sh list               who can log in
#   sudo ./deploy/viewer.sh remove tester      revoke, immediately
#
# Every login sees the SAME page. There is no admin tier and no per-user view,
# because there is nothing to tier: nginx serves one static file with
# `try_files ... =404`, so there is no write path through the web at all. A
# viewer can read the account and can do nothing else -- not approve a trade,
# not change a setting, not reach the agent. Handing someone a login is handing
# them a read-only window.
#
# What they WILL see: the account label, cash, positions, every closed trade and
# the full event log. That is a paper account, but decide it is fine to share
# before you share it -- there is no redaction mode.
set -euo pipefail

HT=/etc/nginx/.htpasswd
CMD="${1:-}"
USER_NAME="${2:-}"

die() { echo "error: $*" >&2; exit 1; }

# Usage is printed before the root check, so someone who mistypes gets the help
# rather than a lecture about sudo.
case "$CMD" in
  add|list|remove|rm|revoke) ;;
  *) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 64 ;;
esac

[ "$(id -u)" -eq 0 ] || die "run with sudo -- this reads and writes $HT"

command -v htpasswd >/dev/null 2>&1 || {
  echo "installing apache2-utils for htpasswd..."
  apt-get update -qq && apt-get install -y -qq apache2-utils
}

reload_nginx() {
  # Test before reload. A broken config that is only discovered on the next
  # boot takes the dashboard down at the worst possible moment.
  nginx -t >/dev/null 2>&1 || die "nginx config test failed -- not reloading. Run: nginx -t"
  systemctl reload nginx
}

case "$CMD" in
  list)
    [ -f "$HT" ] || die "$HT does not exist -- run deploy/bootstrap.sh first"
    echo "logins that can reach the dashboard:"
    cut -d: -f1 "$HT" | sed 's/^/  /'
    ;;

  add)
    [ -n "$USER_NAME" ] || die "usage: sudo $0 add <username> [--pass <password>]"
    case "$USER_NAME" in
      *:*|*" "*|"") die "username must not contain a colon or a space" ;;
    esac

    PASS=""
    if [ "${3:-}" = "--pass" ]; then
      PASS="${4:-}"
      [ -n "$PASS" ] || die "--pass needs a value"
    else
      # Generated on the box, printed once, never sent anywhere. Ambiguous
      # characters are dropped so it survives being read aloud or retyped.
      PASS="$(head -c 32 /dev/urandom | base64 | tr -d '/+=lIO01' | cut -c1-16)"
    fi

    # `htpasswd -c` TRUNCATES the file. Using it on an existing .htpasswd would
    # silently delete every other login, including the one bootstrap created --
    # so -c is reachable only when the file genuinely does not exist yet.
    if [ -f "$HT" ]; then
      EXISTING="$(cut -d: -f1 "$HT" | grep -Fx "$USER_NAME" || true)"
      printf '%s\n' "$PASS" | htpasswd -i -B "$HT" "$USER_NAME" >/dev/null 2>&1
      [ -n "$EXISTING" ] && VERB="password rotated for" || VERB="added"
    else
      printf '%s\n' "$PASS" | htpasswd -i -B -c "$HT" "$USER_NAME" >/dev/null 2>&1
      VERB="added (new password file)"
    fi
    chown root:www-data "$HT" 2>/dev/null || true
    chmod 640 "$HT"
    reload_nginx

    DOMAIN="$(grep -hoP 'server_name\s+\K[^;]+' /etc/nginx/sites-enabled/pcs* 2>/dev/null \
              | tr -d ' ' | grep -v '^_$' | head -1 || true)"
    echo
    echo "$VERB: $USER_NAME"
    echo "  url:      https://${DOMAIN:-<your-domain>}/"
    echo "  user:     $USER_NAME"
    echo "  password: $PASS"
    echo
    echo "Shown once. It is stored bcrypt-hashed and cannot be read back --"
    echo "re-run this command to rotate it if it is lost."
    echo "Send it over something private. A password in a chat log is a password"
    echo "in everyone's chat log."
    ;;

  remove|rm|revoke)
    [ -n "$USER_NAME" ] || die "usage: sudo $0 remove <username>"
    [ -f "$HT" ] || die "$HT does not exist"
    cut -d: -f1 "$HT" | grep -Fxq "$USER_NAME" || die "no login named '$USER_NAME'"
    # Refuse to empty the file: no logins means nobody can reach the dashboard,
    # and the fix needs a shell on the box.
    [ "$(wc -l < "$HT")" -gt 1 ] || die "'$USER_NAME' is the only login left. \
Add another first, or you will lock yourself out of the dashboard."
    htpasswd -D "$HT" "$USER_NAME" >/dev/null 2>&1
    reload_nginx
    echo "revoked: $USER_NAME -- the next request with that credential gets a 401"
    ;;

esac
