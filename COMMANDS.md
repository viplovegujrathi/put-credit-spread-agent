# Commands and operations

The single reference for running this agent — a new EC2 box from scratch, every
command, and what to do when something is wrong. **If you change a command, change
this file in the same commit.** Nothing else documents commands; the README says
what the agent is and why, and links here.

- [Set up a new EC2 box](#set-up-a-new-ec2-box)
- [The daily loop](#the-daily-loop)
- [Approval](#approval)
- [Configuration](#configuration)
- [Data and maintenance](#data-and-maintenance)
- [Running on the server](#running-on-the-server)
- [When something is wrong](#when-something-is-wrong)

Commands are shown as run from the repo root on a laptop. On the box everything
runs as the `pcs` service account — see [Running on the server](#running-on-the-server).

---

## Set up a new EC2 box

**Nothing on this instance ever authenticates to your broker.** The Robinhood
connection is an MCP server the *agent* talks to, not something the Python code
calls. The box reads public price data, writes a paper ledger to a JSON file, and
serves an HTML page. A compromise of it costs you a paper ledger and some position
history, not your account. Keep it that way: no broker credentials belong here.

### 1. Launch

| | |
|---|---|
| AMI | Ubuntu Server 24.04 LTS or newer (arm64) |
| Type | `t4g.small` — 2 GB RAM. `t4g.micro` will OOM on the 503-ticker screen |
| Storage | 16 GB gp3 |
| Key pair | create one, download the `.pem`, `chmod 400` it |

Security group inbound — three rules:

| Type | Port | Source | Note |
|---|---|---|---|
| SSH | 22 | **My IP**, never `0.0.0.0/0` | |
| HTTP | 80 | `0.0.0.0/0` | Required for the certificate, and for every renewal |
| HTTPS | 443 | `0.0.0.0/0` | |

Leave 80 open. Certbot re-proves the domain every 60 days over plain HTTP, and
closing it means the certificate silently stops renewing.

Attach an **elastic IP** if the dashboard has a DNS name — the record has to keep
resolving to this box or renewal fails.

### 2. Point a hostname at it

Any DNS that resolves to the instance works (duckdns is fine). Confirm before
going further; certbot allows only 5 failed validations per hostname per hour:

```bash
dig +short your.domain
```

### 3. Install

```bash
ssh -i ~/.ssh/your-key.pem ubuntu@<elastic-ip>
```

```bash
git clone https://github.com/viplovegujrathi/put-credit-spread-agent.git && cd put-credit-spread-agent
```

```bash
sudo PCS_DOMAIN=your.domain ./deploy/bootstrap.sh
```

That is the whole install. It creates the `pcs` system account, installs to
`/opt/pcs`, builds the venv, seeds a paper `settings.json`, initialises the ledger
and dashboard, enables all three timers, starts the login service, sets up nginx and a
Let's Encrypt certificate, and finishes by running the test suite **on the box**.

It **prints a generated dashboard password once**. Save it then, or reset it —
and add logins for other people — with `deploy/viewer.sh`; see
[Dashboard logins](#dashboard-logins--share-rotate-revoke).

Omit `PCS_DOMAIN` to install the agent without the web layer, and re-run with it
once DNS is ready. The script is idempotent: re-running is how you redeploy.

### 4. Prove it works before trusting the timers

Do this **outside market hours**. The job screens, sizes and ranks for real, then
holds at the fill because the market is shut — so it exercises everything except
committing capital:

```bash
sudo systemctl start pcs-propose.service && sudo journalctl -u pcs-propose.service -n 60 --no-pager
```

This is the only thing that tests what a fresh install has never used: outbound
network from the instance, and the yfinance cache under `ProtectSystem=strict`.
A first firing on the timer is a bad place to discover either.

### 5. Decide whether it trades

With `auto_approve` on, the propose timer opens positions unattended. To install
now and start later:

```bash
sudo -u pcs /opt/pcs/.venv/bin/python /opt/pcs/run.py config --set paper_trading=off
```

Marking, exits and the watchlist keep running; only new positions stop.

---

## The daily loop

Global flag, valid on any subcommand: `--source {yfinance,robinhood,model}`
overrides the chain source for one run.

### `./run.py screen`
The two-condition screen over all 503 constituents, every name in a labelled
bucket — `PRIMARY`, `NEAR_BELOW_TIGHT`, `BELOW_STRETCHED`, `BROKEN_DOWN`,
`ABOVE_50DMA`, `NOT_BEATEN_DOWN`, `NO_DATA`. Sizes nothing, opens nothing. ~5 min.

| Flag | Effect |
|---|---|
| `--symbols AAPL MCD` | Limit to these tickers |
| `--verbose` | Print the excluded buckets too |
| `--max-cache-age MIN` | Reuse cached price snapshots up to this old (default 0) |

Buckets never blend: a stock 14% below its 50dma is `BROKEN_DOWN`, not a weaker
`PRIMARY`, and it is reported as such rather than dropped.

### `./run.py propose`
Screen → size → portfolio risk → tickets, written to `data/proposals.json`.
**With approval off this also opens every clear proposal.**

| Flag | Effect |
|---|---|
| `--symbols ...` | Limit the screen |
| `--expiration YYYY-MM-DD` | Force one expiration instead of resolving ~32 DTE |
| `--contracts N` | Size N lots (default 1). Caps are checked against the total |
| `--include-tight` | Also size `NEAR_BELOW_TIGHT`. Off by default: not the setup |
| `--allow-unknown-earnings` | Treat an unknown earnings date as clean. Off by default |
| `--no-auto-open` | Write tickets only, even when approval is off |
| `--max-cache-age MIN` | Reuse cached price snapshots |

### `./run.py watch`
Refreshes the watchlist — every tracked name, the spread the agent would take, and
why it has not. Writes `data/watchlist.json` and rebuilds the dashboard.

**This opens nothing**, which is why it is the one job that runs around the clock.
Out-of-hours quotes are labelled a stale snapshot rather than presented as a
premium you could get.

| Flag | Effect |
|---|---|
| `--symbols ...` | Limit to these tickers |
| `--contracts N` | Price the potential premium at N lots |
| `--max-cache-age MIN` | Reuse cached price snapshots (default 45) |

| Signal | Meaning |
|---|---|
| `HOLDING` | Already an open position |
| `READY` | Clears every rule — the next `propose` run opens it |
| `BLOCKED` | The trade is fine; a *portfolio* cap is in the way. Still priced, so the cost of the cap is visible |
| `EARNINGS` | Earnings inside the expiration window (an unknown date counts) |
| `NO_FIT` | Screened in, but no strike and width clears the credit floor at a safe cushion |
| `NEAR` | 0–3% below the 50dma: near the setup, shallow pullback |
| `STRETCHED` | 8–12% below: drifting toward broken-down |

### `./run.py mark`
Re-prices every open position, then **takes any exit that is due** — books profit
at the target, cuts stops, defends a tested short strike inside 7 DTE. Requires a
live market: a close at 22:00 against the afternoon print is a fill nobody got.

| Flag | Effect |
|---|---|
| `--no-auto-exit` | Decide but do not execute — print the exits that are due |

Also ingests any newly closed trade into the journal and runs self-repair — so
the learning record stays current without a second timer.

### `./run.py learn`
Reads the closed record and prints what it actually supports, then runs the
agent's own self-repair pass. Two halves, deliberately different:

- **Trades → suggestions only.** Wins and losses are grouped by cushion at
  entry, premium richness, quote grade at fill and sector. Nothing is reported
  under 8 closed trades, and no comparison is made unless both sides hold at
  least 4. Every finding prints the `config --set` line and stops — the agent
  applies none of them.
- **Faults → repaired automatically.** A symbol whose chain fails to price 3
  times in 7 days is benched for 5 days and skipped by `propose`. The bench
  expires on its own. This is the only thing the agent changes by itself, and
  it can only ever *remove* a candidate.

Runs as part of every `mark`, so the timers already do it. Call it directly to
read the findings.

```bash
./run.py learn
```

Writes `data/journal.json`. Shown on the dashboard's **Learning** tab.

### `./run.py doctor`
**Why has nothing opened?** Walks every gate between "the market is open" and "a
position exists", in the order they actually bind, and names the first one that
stops a fill. Offline and instant — it reports on the last run rather than
performing a new one, which is the question being asked.

```bash
./run.py doctor
```

Checks, in order: the `paper_trading` master switch → ledger mode → whether
human approval is required → trading day → opening range → market hours →
position count → collateral cap → available balance → sector caps → self-repair
benches → what the last `propose` run produced → whether each systemd unit last
exited cleanly → any `HELD` or traceback lines in `logs/propose.log`.

Exits `1` if anything is blocking, `0` if nothing is — so it can gate a script.

On the box it also answers the question the dashboard cannot: **did the timer
actually fire?** A dead scheduler and a quiet market look identical on the page.
Off a systemd host the unit checks are skipped rather than reported as missing.

"Nothing is blocking" is not "a trade will happen" — the screen may simply have
found no name that clears the rules, which is a valid outcome. `./run.py propose`
runs it now and shows the working.

### `./run.py status`
Cash, collateral held, capital at risk, available balance, net liquidation, open
and closed positions. Read-only.

---

## Approval

### `./run.py approve <proposal-id> --approver "your name"`
The human gate. Fills the proposal into the paper ledger.

| Flag | Effect |
|---|---|
| `--approver NAME` | Who is approving. Required unless `auto_approve` is on |
| `--override` | Approve despite a portfolio limit. Only if you accept the breach |

Four gates run at fill time regardless of who approved: the master trading switch,
a recorded approver, a settled session, and a balance the account can actually pay
the max loss from. A worse fill means *more* collateral, so the balance check runs
on the filled number, not the ticket's.

### `./run.py reject <proposal-id> --reason "..."`
Record a decline against the proposal.

### `./run.py close <position-id>`
Close a paper position at the current mark.

| Flag | Effect |
|---|---|
| `--debit N` | Override the closing debit, per share |
| `--reason "..."` | Recorded on the ledger event |

---

## Configuration

### `./run.py config`
Prints every knob, its value, the strategy's own number beside it, and everything
this account has moved away from the mandate.

### `./run.py config --set KEY=VALUE`
Sets and persists to `data/settings.json`. Repeatable.

```bash
./run.py config --set max_collateral_per_trade=750 --set min_credit_per_trade=150
```

| Key | Default | Effect |
|---|---|---|
| `paper_trading` | `on` | Master switch. Off = nothing opens; **exits keep running** |
| `auto_approve` | `off` | On = the agent opens clear proposals itself. **Paper only** |
| `auto_exit` | `on` | Take profit and cut stops automatically |
| `max_collateral_per_trade` | strategy's $1,000 | Per-trade max loss |
| `max_loss_per_trade` | — | The same cap said the other way; the tighter binds |
| `min_credit_per_trade` | strategy's $100 | Credit floor |
| `max_credit_per_trade` | no cap | A ceiling, if you want one |
| `min_otm_cushion` | strategy's 3% | How far OTM the short strike must sit |
| `take_profit_pct` | strategy's 50% | Where profit is booked. The 50–75% band it sits in is not settable per account |
| `stop_loss_credit_multiple` | `2.0` | Stop when buyback costs this × the credit |
| `stop_loss_pct_of_max_loss` | `0.50` | ... or when down this fraction of max loss |
| `max_total_collateral` | `2400` | Across the whole book |
| `max_open_positions` | `10` | Also editable on the dashboard |
| `max_positions_per_sector` | `2` | |
| `max_positions_per_ticker` | `1` | |
| `mode` | `paper` | `live` is refused in code |
| `chain_source` | `yfinance` | `yfinance` \| `robinhood` \| `model` |
| `opening_settle_minutes` | `30` | No opening inside this many minutes of the bell |
| `self_repair` | `on` | Let the agent bench symbols whose chains keep failing |
| `learning_min_sample` | `8` | Closed trades before any lesson is drawn at all |
| `learning_min_group` | `4` | Trades needed on each side of a comparison |
| `learning_min_effect` | `0.20` | Win-rate gap that counts as a real difference |
| `learning_fault_threshold` | `3` | Data failures before a symbol is benched |
| `learning_quarantine_days` | `5` | How long a bench lasts before it expires |

Unset means *use the strategy's number*. Every override is reported on the ticket,
on the dashboard's Rules tab, and by `config` — a ticket sized under a loosened cap
can never be read as one that met the standard rule.

**No setting can lift the approval requirement off a live ledger.**

**No setting makes the agent apply its own lessons.** The learning knobs move the
floors for *reporting* a pattern; turning them all the way down produces louder
suggestions, not automatic changes.

---

## Data and maintenance

### `./run.py dashboard`
Rebuilds `dashboard.html` from the current ledger, proposals and watchlist.

### `./run.py universe [--refresh]`
Inspect the S&P 500 constituent list, or re-pull it. Membership changes several
times a year; the cached list carries an `as_of` date.

### `./run.py chain-requests`
Emits the chains to pull through the Robinhood MCP, for broker-quality sizing.
MCP tools belong to the agent, not to a Python process, so the flow is two-phase:

```bash
./run.py chain-requests                 # 1. what to pull, over which strikes
#  2. agent runs get_option_instruments + get_option_quotes -> data/rh_raw/
python3 tools/rh_ingest.py              # 3. normalise into data/rh_chains/
./run.py propose --source robinhood     # 4. size on the broker's own book
```

---

## Running on the server

Everything runs as `pcs` out of `/opt/pcs`:

```bash
sudo -u pcs /opt/pcs/.venv/bin/python /opt/pcs/run.py status
```

### Timers

| Unit | When | What |
|---|---|---|
| `pcs-watch.timer` | **hourly, 24/7** | Refresh the watchlist. Observation only |
| `pcs-propose.timer` | 10:15 ET weekdays | Screen, size, open clear proposals |
| `pcs-mark.timer` | every 15 min, 09:35–15:50 ET | Re-price, take due exits |

The market-hours jobs carry their timezone in the calendar spec
(`Mon..Fri 10:15 America/New_York`), so they follow DST **without the box clock
being involved** — leave the instance on UTC. `pcs-watch` has no window because
nothing it does can trade.

```bash
systemctl list-timers 'pcs-*'
```

```bash
sudo systemctl start pcs-watch.service && sudo journalctl -u pcs-watch.service -n 50 --no-pager
```

### Redeploy

```bash
cd ~/put-credit-spread-agent && git pull && sudo ./deploy/bootstrap.sh
```

Idempotent. It never overwrites the box's ledger, proposals or settings, and runs
the test suite before finishing.

### Logs

```bash
sudo tail -n 50 /opt/pcs/logs/propose.log
```

One per task: `propose.log`, `mark.log`, `watch.log`.

### Dashboard logins — share, rotate, revoke

The dashboard is behind a real login page, not the browser's credential popup.
nginx authorises every request with an `auth_request` subrequest to `pcs-authd`,
a small service on loopback that verifies the password and issues a signed
session cookie. Basic auth was dropped because its `WWW-Authenticate` header is
what summons the browser dialog, and that dialog cannot be styled.

Logins live in `/etc/pcs/viewers`, salted and hashed with PBKDF2. Use the
script rather than editing the file:

```bash
sudo /opt/pcs/deploy/viewer.sh add tester
```

Set the password yourself instead of generating one:

```bash
sudo /opt/pcs/deploy/viewer.sh add tester --password 'a-strong-password'
```

Re-running `add` for an existing name **rotates** that password rather than
adding a duplicate — that is also how you reset your own:

```bash
sudo /opt/pcs/deploy/viewer.sh add pcs
```

See who can log in:

```bash
sudo /opt/pcs/deploy/viewer.sh list
```

Revoke. Removing the last remaining login is refused, because the fix would
need a shell on the box:

```bash
sudo /opt/pcs/deploy/viewer.sh remove tester
```

Sessions are signed and stateless, so revoking a viewer stops the next login
but leaves a session they already hold valid until it expires (7 days). To cut
every session on the box immediately:

```bash
sudo rm /var/lib/pcs/session.key && sudo systemctl restart pcs-authd
```

Everyone signs out, including you. There is also a **Sign out** button in the
dashboard header, which drops only your own session.

Two directories, on purpose: `/etc/pcs` holds the credentials and is root-owned
and read-only to the login service, so that process can check a password and
cannot rewrite the file it checks against. `/var/lib/pcs` is the service's own
state — the signing key and any setting changed from the page. Neither is
inside `/opt/pcs`, which is rsynced with `--delete` on every redeploy.

### Sorting the watchlist

The Watchlist table sorts on any column: click a header, or use the **Sort by**
select above it. Clicking the same column again reverses it, and the choice is
remembered per browser.

The first click shows the useful end first — biggest premium, widest cushion,
best estimated win, but *cheapest* collateral and READY-first for signal. A
name with no sizeable spread has no premium to be ranked on, so it carries no
key and sinks to the bottom whichever way round the sort goes.

The select is not a nicety: at phone widths the table collapses to a stack of
cards and `thead` is `display:none`, so there is no header left to click.

### Changing the position cap from the dashboard

`Max open positions` is editable behind the gear icon in the page header, next
to the light/dark toggle. Saving posts to the
login service, which writes `/var/lib/pcs/overrides.json`; the agent applies it
on its **next run** — no restart and no redeploy. The page itself is a static
file, so the number shown catches up when something re-renders it (the mark
timer does that every 15 minutes while the market is open).

The portfolio limits it belongs to are read-only prose and live under the
**Rules** tab; the gear is where the one writable value sits, because a write
control dropped into the middle of a paragraph is not where anyone looks for
one.

It is the only setting editable there, and that is a boundary rather than a
backlog: every login sees the same page and there is no admin tier, so anything
reachable from the page is reachable by every viewer. `paper_trading`,
`auto_approve`, `mode` and `starting_cash` are not on the allowlist — nothing
reachable from a browser can arm trading or waive the human approval gate.

`./run.py config --set max_open_positions=N` still works and takes precedence:
it clears the dashboard override for that key, so the last change wins whichever
way it was made.

If the login page is unreachable, the agent
itself is unaffected — timers, marks and exits do not go through it:

```bash
sudo journalctl -u pcs-authd -n 50 --no-pager
```


---

## When something is wrong

### The timers fire at the wrong time

```bash
systemctl list-timers 'pcs-*'
```

`NEXT` must read **ET**. If `pcs-propose` shows 10:15 UTC the zone in the calendar
spec did not take — check `systemd-analyze calendar 'Mon..Fri 10:15 America/New_York'`
parses. It needs systemd v252+.

### certbot: "Timeout during connect (likely firewall problem)"

Let's Encrypt cannot reach port 80. Almost always the security group. Confirm from
somewhere outside the box before retrying — you get 5 failures per hostname per hour:

```bash
curl -sS -m 8 -o /dev/null -w '%{http_code}\n' http://your.domain/
```

### The domain serves the wrong page

Two server blocks naming one host on 443 is **not an nginx error** — the first one
loaded wins, and `sites-enabled` sorts alphabetically. Older versions of this
installer let `certbot --nginx` edit config, which put the domain into
`sites-enabled/default`:

```bash
sudo grep -l "your.domain" /etc/nginx/sites-enabled/*
```

If `default` is listed, disable it — Ubuntu's welcome page has no purpose here:

```bash
sudo rm /etc/nginx/sites-enabled/default && sudo nginx -t && sudo systemctl reload nginx
```

### Check what the dashboard actually serves

From your laptop, not the box. It must be **401**, and the ACME path must **not**
redirect or renewal breaks:

```bash
curl -sS -m 10 -o /dev/null -w '%{http_code}\n' https://your.domain/
```

```bash
curl -sS -m 10 -o /dev/null -w '%{http_code}\n' http://your.domain/.well-known/acme-challenge/probe
```

### A run produced no proposals

Often correct, not a failure. Some days nothing clears ≥15% off the high *and*
3–8% below the 50dma *and* $100 credit within $1,000 collateral. The watchlist
shows which names came closest and what stopped them.
