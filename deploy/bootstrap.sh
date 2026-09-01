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
  --exclude 'data/last_screen*.json' --exclude 'data/watchlist.json' \
  --exclude 'data/journal.json' --exclude 'logs' \
  "$SRC"/ "$APP"/
chmod +x "$APP"/deploy/*.sh "$APP"/run.py
mkdir -p "$APP/data" "$APP/logs"

say "virtualenv"
[ -x "$APP/.venv/bin/python" ] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
# requirements-dev pulls in requirements plus pytest/ruff. The test tools are
# not needed at runtime, but the box should be able to prove the risk rules
# still hold after a deploy without needing a second toolchain.
"$APP/.venv/bin/pip" install -q -r "$APP/requirements-dev.txt"
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
# `learn` creates data/journal.json and runs the first self-repair pass, so the
# Learning tab has a file to read rather than an empty state that looks broken.
sudo -u "$SVC_USER" "$APP/.venv/bin/python" "$APP/run.py" learn >/dev/null
sudo -u "$SVC_USER" "$APP/.venv/bin/python" "$APP/run.py" dashboard >/dev/null
install -o "$SVC_USER" -g "$SVC_USER" -m 0644 "$APP/dashboard.html" "$WEB/index.html"
echo "  ledger + dashboard initialised"

# --- 5. timers ------------------------------------------------------------
say "systemd"
cp "$APP"/deploy/pcs-*.service "$APP"/deploy/pcs-*.timer /etc/systemd/system/
# Every schedule here is market-local, because the bell moves against UTC twice
# a year and never against America/New_York. The timezone rides in the calendar
# spec so the box clock is left alone -- this box may not stay dedicated, and a
# system-wide `timedatectl set-timezone` would silently move anything else that
# is scheduled on it. Test the capability rather than parsing a version number.
if ! systemd-analyze calendar 'Mon..Fri 10:15 America/New_York' >/dev/null 2>&1; then
  die "this systemd cannot put a timezone in OnCalendar (needs v252+; this box has
  $(systemctl --version | head -1)). The schedules are market-local, so either
  upgrade systemd or set the box clock with
      sudo timedatectl set-timezone America/New_York
  -- but only if nothing else on this box schedules against UTC. Not doing it
  for you: on a stock EC2 image the clock is UTC and these timers would then
  fire about four hours before the market opens."
fi
for cal in 'Mon..Fri 10..15:05,20,35,50 America/New_York' \
           'Mon..Fri 09:35,50 America/New_York' \
           'Mon..Fri 10:15 America/New_York'; do
  systemd-analyze calendar "$cal" >/dev/null || die "bad OnCalendar: $cal"
done
echo "  calendar expressions parse, in America/New_York"
echo "  box clock left on $(timedatectl show -p Timezone --value)"
systemctl daemon-reload
systemctl enable --now pcs-mark.timer pcs-propose.timer pcs-watch.timer
systemctl list-timers 'pcs-*' --no-pager | head -4
echo "  ^ LEFT is what matters: propose fires 10:15 ET, i.e. 14:15 UTC in EDT."
echo "    If NEXT reads 10:15 UTC the timezone did not take -- stop and say so."

# Run from $APP: pytest resolves its rootdir from the working directory, and the
# service account has no business being able to read wherever this was invoked.
say "self-check"
( cd "$APP" && sudo -u "$SVC_USER" "$APP/.venv/bin/python" -m pytest -q 2>&1 | tail -3 )

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

install -o "$SVC_USER" -g "$SVC_USER" -m 0644 "$APP/deploy/401.html" "$WEB/401.html"
sed "s/PCS_DOMAIN/$DOMAIN/g" "$APP/deploy/nginx-pcs.conf" > "$CONF"
ln -sf "$CONF" /etc/nginx/sites-enabled/pcs

# TLS must exist before nginx can load a config that references the cert.
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
  command -v certbot >/dev/null || apt-get install -y -qq certbot python3-certbot-nginx >/dev/null

  # Pre-flight. Let's Encrypt allows 5 failed validations per hostname per hour,
  # so a run that cannot possibly succeed is worth catching before it spends one.
  say "pre-flight for $DOMAIN"

  RESOLVED="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1)"
  MYIP="$(curl -fsS --max-time 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
  echo "  $DOMAIN -> ${RESOLVED:-<unresolved>}"
  echo "  this box  -> ${MYIP:-<unknown>}"
  [ -n "$RESOLVED" ] || die "$DOMAIN does not resolve. Point it at this box first."
  if [ -n "$MYIP" ] && [ "$RESOLVED" != "$MYIP" ]; then
    die "$DOMAIN resolves to $RESOLVED but this box is $MYIP. The challenge would
  be answered by the wrong machine. Fix DNS (or the elastic IP) first."
  fi

  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "^Status: active"; then
    ufw status | grep -qE "^(80|Nginx|Nginx Full)" \
      || warn "ufw is active and does not obviously allow 80. sudo ufw allow 80,443/tcp"
  fi

  # Whether the internet can reach port 80 is not answerable from the box, and
  # it is not worth a third-party proxy to find out -- certbot answers it in one
  # attempt and the failure below says exactly what to do. The local checks
  # above are the ones that catch a mistake certbot would misreport.

  # Serve the challenge from a throwaway HTTP-only vhost and use `certonly`, so
  # certbot obtains the certificate without editing a single line of nginx
  # config. The full vhost cannot be enabled yet -- it references a certificate
  # that does not exist -- and letting certbot pick a block for itself is how
  # the domain ends up attached to the default site.
  rm -f /etc/nginx/sites-enabled/pcs
  sed "s/PCS_DOMAIN/$DOMAIN/g" "$APP/deploy/nginx-pcs-acme.conf" \
    > /etc/nginx/sites-available/pcs-acme
  ln -sf /etc/nginx/sites-available/pcs-acme /etc/nginx/sites-enabled/pcs-acme
  nginx -t && systemctl reload nginx

  if ! certbot certonly --webroot -w "$WEB" -d "$DOMAIN" --agree-tos \
        --register-unsafely-without-email --non-interactive; then
    rm -f /etc/nginx/sites-enabled/pcs-acme
    nginx -t && systemctl reload nginx
    cat >&2 <<EOF

!! certbot could not prove you control $DOMAIN.

   "Timeout during connect" means Let's Encrypt could not reach port 80 of this
   box at all. DNS is fine or you would have seen a different error -- this is
   almost always the EC2 SECURITY GROUP, which blocks inbound 80/443 by default.

   Open them for this instance, then re-run this script:

     AWS console -> EC2 -> Instances -> select this one -> Security tab
       -> click its security group -> Inbound rules -> Edit
       -> Add rule: HTTP  80  Anywhere-IPv4
       -> Add rule: HTTPS 443 Anywhere-IPv4

   Or, with credentials that can change it:

     SG=\$(aws ec2 describe-instances --instance-ids $(cat /var/lib/cloud/data/instance-id 2>/dev/null || echo YOUR_INSTANCE_ID) \\
            --query 'Reservations[].Instances[].SecurityGroups[].GroupId' --output text)
     aws ec2 authorize-security-group-ingress --group-id \$SG --protocol tcp --port 80  --cidr 0.0.0.0/0
     aws ec2 authorize-security-group-ingress --group-id \$SG --protocol tcp --port 443 --cidr 0.0.0.0/0

   Note: Let's Encrypt allows 5 failed validations per hostname per hour. Do not
   re-run this in a loop -- confirm the port is open first:

     curl -sS -m 8 -o /dev/null -w '%{http_code}\\n' http://$DOMAIN/

   nginx is untouched and still serving whatever it served before. The agent
   itself is installed and its timers are running; only the web layer is missing.
EOF
    exit 1
  fi
  rm -f /etc/nginx/sites-enabled/pcs-acme
fi

ln -sf "$CONF" /etc/nginx/sites-enabled/pcs

# Two server blocks claiming one name on 443 is not an nginx error -- the first
# one loaded simply wins, and `sites-enabled/default` sorts before `pcs`. That
# is exactly how this box ended up serving the welcome page over TLS.
for other in /etc/nginx/sites-enabled/*; do
  [ "$(basename "$other")" = "pcs" ] && continue
  if grep -qE "server_name[^;]*\b$(echo "$DOMAIN" | sed 's/\./\\./g')\b" "$other" 2>/dev/null; then
    OTHER_NAME="$(basename "$other")"
    cat >&2 <<EOF

!! $OTHER_NAME also claims $DOMAIN.

   Two server blocks naming one host on 443 is not an nginx error: the first one
   loaded wins, and sites-enabled sorts alphabetically -- so "$OTHER_NAME" beats
   "pcs" and you get that site instead of the dashboard, with no error anywhere.

   An older version of this script let \`certbot --nginx\` edit config, which is
   how the name got there. If $OTHER_NAME is Ubuntu's stock welcome page, it has
   no purpose on a box dedicated to this agent -- disable it:

       sudo rm /etc/nginx/sites-enabled/$OTHER_NAME
       sudo nginx -t && sudo systemctl reload nginx

   Reversible: ln -s /etc/nginx/sites-available/$OTHER_NAME /etc/nginx/sites-enabled/
   If you need that site, delete just the "$DOMAIN" server_name lines from it.

EOF
  fi
done

nginx -t || die "nginx config is invalid -- NOT reloading; the existing site is untouched"
systemctl reload nginx
say "done -- https://$DOMAIN"
