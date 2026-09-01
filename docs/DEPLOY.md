# Deploying to EC2

The agent screens, sizes, marks and takes exits on a schedule; the dashboard is
a static HTML file it rewrites each run. So the box needs Python, a timer, and
a web server — nothing else.

**One thing to be clear about before you start.** The Robinhood connection is an
MCP server the *agent* talks to, not something the Python code calls. Nothing on
this instance ever authenticates to your broker. It reads public price data from
Yahoo, writes a paper ledger to a JSON file, and serves an HTML page. That means
a compromise of this box loses you a paper ledger and some position history —
not your account. Keep it that way: do not put broker credentials on it.

Approving a trade stays a human action, run from wherever you are. The timers
only run `propose` and `mark`.

---

## 1. Launch the instance

| | |
|---|---|
| AMI | Ubuntu Server 24.04 LTS (arm64) |
| Type | `t4g.small` — 2 GB RAM. `t4g.micro` (1 GB) will OOM on the 503-ticker screen |
| Storage | 16 GB gp3 |
| Key pair | create one, download the `.pem`, `chmod 400` it |

Security group — two rules, no more:

| Type | Port | Source |
|---|---|---|
| SSH | 22 | **My IP** (not `0.0.0.0/0`) |
| HTTPS | 443 | `0.0.0.0/0` |

Add HTTP/80 only while you are issuing the certificate in step 5; remove it
after, since nginx redirects anyway.

```bash
ssh -i ~/.ssh/your-key.pem ubuntu@<public-ip>
```

## 2. Base system

```bash
sudo timedatectl set-timezone America/New_York
```

The timers use market-local times, so this is what makes them follow DST
without you touching them again.

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip git nginx apache2-utils
```

## 3. Install the agent

```bash
sudo useradd --system --home /opt/pcs --shell /usr/sbin/nologin pcs
sudo git clone https://github.com/viplovegujrathi/put-credit-spread-agent.git /opt/pcs
sudo chown -R pcs:pcs /opt/pcs
```

```bash
sudo -u pcs python3 -m venv /opt/pcs/.venv
sudo -u pcs /opt/pcs/.venv/bin/pip install -r /opt/pcs/requirements.txt
```

Seed the constituent list and confirm it runs:

```bash
sudo -u pcs /opt/pcs/.venv/bin/python /opt/pcs/run.py universe --refresh
```

```bash
sudo -u pcs /opt/pcs/.venv/bin/python /opt/pcs/run.py status
```

That should print a $3,000 paper account. If you want to carry over the ledger
from your laptop, copy `data/ledger.json`, `data/proposals.json` and
`data/settings.json` across now — they are gitignored, so the clone does not
include them.

## 4. Schedule it

```bash
sudo mkdir -p /var/www/pcs && sudo chown pcs:pcs /var/www/pcs
```

```bash
sudo cp /opt/pcs/deploy/pcs-*.service /opt/pcs/deploy/pcs-*.timer /etc/systemd/system/
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now pcs-mark.timer pcs-propose.timer
```

**Decide what the propose timer is allowed to do before you enable it.** On this
account per-trade approval is off, so `propose` does not stop at the ticket — it
opens every clear proposal into the paper ledger itself and records the approver
as `agent (auto-approve, paper)`. Check, and change, on the box:

```bash
sudo -u pcs /opt/pcs/.venv/bin/python /opt/pcs/run.py config
```

```bash
sudo -u pcs /opt/pcs/.venv/bin/python /opt/pcs/run.py config --set auto_approve=off
```

`paper_trading=off` is the wider switch: it stops anything opening at all while
leaving marking and exits running. Neither setting can reach a live order —
`open_approved` refuses a non-paper ledger in code, and **no broker credentials
belong on this instance**.

Two timers, doing different jobs:

- **`pcs-mark.timer`** — every 15 minutes, 09:35–15:50 ET, weekdays. Re-prices
  every open position and **takes any exit that is due**. This is what gives the
  stop loss teeth: it has to fire while nobody is watching. A firing outside
  regular hours is refused by `apply_exits` rather than acted on, so an
  over-broad schedule costs a log line, not a bad fill.
- **`pcs-propose.timer`** — once a day at 10:15 ET. Deliberately after the
  30-minute opening gate lifts, so proposals are sized on a live market instead
  of a closing snapshot.

Check the calendar expressions parse to the times you expect before trusting
them — this was written against the spec, not against a running systemd, and a
malformed `OnCalendar` is accepted at load time and simply never fires:

```bash
systemd-analyze calendar 'Mon..Fri 10..15:05,20,35,50'
```

Then check they are actually scheduled:

```bash
systemctl list-timers 'pcs-*'
```

Run one by hand to prove the whole path works:

```bash
sudo systemctl start pcs-mark.service && sudo journalctl -u pcs-mark.service -n 30 --no-pager
```

## 5. Serve the dashboard

Point a DNS A record at the instance's public IP first — certbot needs to
resolve it. Then:

```bash
sudo htpasswd -c /etc/nginx/.htpasswd yourname
```

```bash
sudo cp /opt/pcs/deploy/nginx-pcs.conf /etc/nginx/sites-available/pcs
sudo sed -i "s/PCS_DOMAIN/dash.example.com/g" /etc/nginx/sites-available/pcs
sudo ln -sf /etc/nginx/sites-available/pcs /etc/nginx/sites-enabled/pcs
sudo rm -f /etc/nginx/sites-enabled/default
```

```bash
sudo snap install --classic certbot && sudo ln -sf /snap/bin/certbot /usr/bin/certbot
sudo certbot --nginx -d dash.example.com
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Visit `https://dash.example.com`. Basic auth, then the dashboard.

**Do not skip the auth.** The page shows your positions, balances and full
trade history. Without `auth_basic` it is a public record of what you hold.

### No domain?

Skip certbot and reach it over an SSH tunnel instead — no port 443, no
certificate, nothing exposed:

```bash
ssh -i ~/.ssh/your-key.pem -L 8080:localhost:80 ubuntu@<public-ip>
```

Then open `http://localhost:8080`. This is the better option if the dashboard
is only ever for you.

## 6. Approving trades

Approval is not automated and is not on this box. SSH in and run it yourself:

```bash
sudo -u pcs /opt/pcs/.venv/bin/python /opt/pcs/run.py approve P260901-01 --approver yourname
```

The agent refuses without a named approver, inside the first 30 minutes after
the bell, and when the position's max loss exceeds the available balance.

## 7. Updating

```bash
cd /opt/pcs && sudo -u pcs git pull && sudo -u pcs .venv/bin/pip install -r requirements.txt
```

The ledger and settings are gitignored, so a pull never touches your positions.

---

## Operating notes

**Costs.** A `t4g.small` on-demand is roughly $12/month plus ~$1.30 for the
volume. A 1-year no-upfront reserved instance cuts the compute by about 40%.

**Logs.** `/opt/pcs/logs/mark.log` and `propose.log` hold the full CLI output,
including every exit the agent took and why. `journalctl -u pcs-mark.service`
has the systemd view. Add logrotate if you leave it running for months.

**Memory.** The screen downloads a year of history for 503 tickers in batches.
If `propose` gets OOM-killed on a smaller instance, add swap:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
```

**Yahoo rate limits.** A daily `propose` is well inside them. Do not schedule it
more often; if it starts returning empty chains, that is the first thing to
suspect.

**What this deployment cannot do.** It cannot place a live order. `open_approved`
and `apply_exits` both refuse a non-paper ledger in code, and no broker
credentials exist on the instance. Going live is a separate decision with its
own prerequisites — see the Go-live tab on the dashboard for where you actually
stand.
