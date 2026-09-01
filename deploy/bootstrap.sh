#!/usr/bin/env bash
# One-shot installer. Runs ON the EC2 instance, as a user with sudo.
#
#   sudo PCS_DOMAIN=pcs.example.org ./deploy/bootstrap.sh
#
# Idempotent: safe to re-run after a code change. It will NOT touch anything it
# did not create -- this box may already be running something else, and a
# deploy script that assumes it owns the machine is how the other thing breaks.
#
# Deliberately absent: broker credentials. This box reads public price data and
# keeps a paper ledger. It has no path to a live order and needs no secrets.
set -euo pipefail

APP=/opt/pcs
WEB=/var/www/pcs
SVC_USER=pcs
DOMAIN="${PCS_DOMAIN:-}"
SRC="${SRC:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
warn() { printf '\033[33m!! %s\033[0m\n' "$*" >&2; }
die() { printf '\033[31m!! %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo"

# --- 1. packages ----------------------------------------------------------
say "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip rsync apache2-utils >/dev/null
python3 --version

# --- 2. service account ---------------------------------------------------
say "service account: $SVC_USER"
if id "$SVC_USER" &>/dev/null; then
  echo "  exists"
else
  useradd --system --home-dir "$APP" --shell /usr/sbin/nologin "$SVC_USER"
  echo "  created (system account, no shell, no login)"
fi

# --- 3. code --------------------------------------------------------------
say "code -> $APP"
mkdir -p "$APP" "$WEB"
# --exclude keeps the box's own account state: a redeploy must never overwrite
# the ledger with whatever happened to be on the laptop.
rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude '.ruff_cache' \
  --exclude 'data/ledger.json' --exclude 'data/proposals.json' \
  --exclude 'data/settings.json' --exclude 'data/snapshots.json' \
  --exclude 'data/last_screen*.json' --exclude 'logs' \
  "$SRC"/ "$APP"/
chmod +x "$APP"/deploy/*.sh "$APP"/run.py
mkdir -p "$APP/data" "$APP/logs"

say "virtualenv"
[ -x "$APP/.venv/bin/python" ] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
# pytest is not a runtime dependency, but the box should be able to prove the
# risk rules still hold after a deploy without needing a second toolchain.
"$APP/.venv/bin/pip" install -q pytest
echo "  $("$APP/.venv/bin/python" --version)"

# --- 4. account state (created once, then left alone) ---------------------
say "account state"
if [ -f "$APP/data/settings.json" ]; then
  echo "  data/settings.json exists -- left untouched"
else
  cat > "$APP/data/settings.json" <<'JSON'
{
  "mode": "paper",
  "paper_trading": true,
  "auto_approve": true,
  "auto_exit": true,
  "starting_cash": 3000.0,
  "account_label": "Paper $3,000 (ec2)"
}
JSON
  echo "  seeded: paper mode, auto-approve on, auto-exit on"
fi
chown -R "$SVC_USER:$SVC_USER" "$APP" "$WEB"

# A ledger has to exist before the timers run, or the first fire is the thing
# that creates it -- and a failure there looks like a broken timer.
sudo -u "$SVC_USER" "$APP/.venv/bin/python" "$APP/run.py" status >/dev/null
sudo -u "$SVC_USER" "$APP/.venv/bin/python" "$APP/run.py" dashboard >/dev/null
install -o "$SVC_USER" -g "$SVC_USER" -m 0644 "$APP/dashboard.html" "$WEB/index.html"
echo "  ledger + dashboard initialised"

# --- 5. timers ------------------------------------------------------------
say "systemd"
cp "$APP"/deploy/pcs-*.service "$APP"/deploy/pcs-*.timer /etc/systemd/system/
for cal in 'Mon..Fri 10..15:05,20,35,50' 'Mon..Fri 09:35,50' 'Mon..Fri 10:15'; do
  systemd-analyze calendar "$cal" >/dev/null || die "bad OnCalendar: $cal"
done
echo "  calendar expressions parse"
systemctl daemon-reload
systemctl enable --now pcs-mark.timer pcs-propose.timer
systemctl list-timers 'pcs-*' --no-pager | head -4

say "self-check"
sudo -u "$SVC_USER" "$APP/.venv/bin/python" -m pytest "$APP/tests" -q 2>&1 | tail -2

# --- 6. web ---------------------------------------------------------------
if [ -z "$DOMAIN" ]; then
  warn "PCS_DOMAIN not set -- skipping nginx."
  warn "The dashboard is at $WEB/index.html. Serve it when you have a name:"
  warn "  sudo PCS_DOMAIN=your.domain $APP/deploy/bootstrap.sh"
  exit 0
fi

say "nginx for $DOMAIN"
command -v nginx >/dev/null || apt-get install -y -qq nginx >/dev/null

CONF=/etc/nginx/sites-available/pcs
if [ -f "$CONF" ] && ! grep -q "put credit spread agent" "$CONF"; then
  die "$CONF exists and was not written by this script -- refusing to overwrite it"
fi

# Basic auth is not optional: this page shows positions and balances.
HT=/etc/nginx/.htpasswd
if [ -s "$HT" ]; then
  echo "  htpasswd exists -- left untouched"
else
  PCS_USER="${PCS_USER:-pcs}"
  PCS_PASS="${PCS_PASS:-$(head -c 18 /dev/urandom | base64 | tr -d '/+=' )}"
  htpasswd -bc "$HT" "$PCS_USER" "$PCS_PASS" >/dev/null 2>&1
  chown root:www-data "$HT"; chmod 640 "$HT"
  echo "  dashboard login -> user: $PCS_USER   password: $PCS_PASS"
  echo "  (shown once; store it in your password manager now)"
fi

sed "s/PCS_DOMAIN/$DOMAIN/g" "$APP/deploy/nginx-pcs.conf" > "$CONF"
ln -sf "$CONF" /etc/nginx/sites-enabled/pcs

# TLS must exist before nginx can load a config that references the cert.
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
  command -v certbot >/dev/null || apt-get install -y -qq certbot python3-certbot-nginx >/dev/null
  warn "no certificate for $DOMAIN yet. $DOMAIN must already resolve to this box."
  rm -f /etc/nginx/sites-enabled/pcs          # don't break nginx meanwhile
  nginx -t && systemctl reload nginx
  certbot --nginx -d "$DOMAIN" --agree-tos --register-unsafely-without-email --non-interactive
  ln -sf "$CONF" /etc/nginx/sites-enabled/pcs
fi

nginx -t || die "nginx config is invalid -- NOT reloading; the existing site is untouched"
systemctl reload nginx
say "done -- https://$DOMAIN"
