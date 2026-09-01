# Commands

Every command this agent has, what it does, and when you would reach for it.
Run from the repo root. On the EC2 box the interpreter is the venv's, so prefix
with `sudo -u pcs /opt/pcs/.venv/bin/python /opt/pcs/run.py` — see
[On the server](#on-the-server).

Global flags, valid on any subcommand: `--source {yfinance,robinhood,model}`
overrides the chain source for one run.

---

## The daily loop

### `./run.py screen`
Runs the two-condition screen over all 503 constituents and prints every name in
a labelled bucket — `PRIMARY`, `NEAR_BELOW_TIGHT`, `BELOW_STRETCHED`,
`BROKEN_DOWN`, `ABOVE_50DMA`, `NOT_BEATEN_DOWN`, `NO_DATA`. Nothing is sized and
nothing is opened. Takes about 5 minutes.

| Flag | Effect |
|---|---|
| `--symbols AAPL MCD` | Limit to these tickers, for a fast check |
| `--verbose` | Print the excluded buckets too, not just the tradeable one |
| `--max-cache-age MIN` | Reuse cached price snapshots up to this old (default 0) |

Names never blend: a stock 14% below its 50dma is `BROKEN_DOWN`, not a weaker
`PRIMARY`, and it is reported as such rather than dropped.

### `./run.py propose`
The full pipeline: screen → size → portfolio risk → tickets, written to
`data/proposals.json`. **With per-trade approval off, this also opens every
clear proposal** — see [Approval](#approval).

| Flag | Effect |
|---|---|
| `--symbols ...` | Limit the screen |
| `--expiration YYYY-MM-DD` | Force one expiration instead of resolving ~32 DTE |
| `--contracts N` | Size N lots (default 1). Every cap is checked against the total |
| `--include-tight` | Also size `NEAR_BELOW_TIGHT`. Off by default: it is not the setup |
| `--allow-unknown-earnings` | Treat an unknown earnings date as clean. Off by default |
| `--no-auto-open` | Write tickets only, even when approval is off |
| `--max-cache-age MIN` | Reuse cached price snapshots |

### `./run.py watch`
Refreshes the **watchlist** — every name the agent is tracking, the spread it
would take, and why it has not. Writes `data/watchlist.json` and rebuilds the
dashboard.

**This never opens anything**, which is why it is the one job that runs around
the clock. Watching at 02:00 is free; filling at 02:00 is a price nobody could
have traded on, so out-of-hours quotes are labelled a stale snapshot rather than
presented as a premium you could get.

| Flag | Effect |
|---|---|
| `--symbols ...` | Limit to these tickers |
| `--contracts N` | Price the potential premium at N lots |
| `--max-cache-age MIN` | Reuse cached price snapshots (default 45) |

Each row carries a **signal**:

| Signal | Meaning |
|---|---|
| `HOLDING` | Already an open position |
| `READY` | Clears every rule — the next `propose` run opens it |
| `BLOCKED` | The trade is fine; a *portfolio* cap is in the way. Still priced, so you can see what the cap is costing |
| `EARNINGS` | Earnings land inside the expiration window (an unknown date counts) |
| `NO_FIT` | Passed the screen, but no strike and width clears the credit floor at a safe cushion |
| `NEAR` | 0–3% below the 50dma: near the setup, shallow pullback |
| `STRETCHED` | 8–12% below: drifting toward broken-down |

### `./run.py mark`
Re-prices every open position, then **takes any exit that is due** — books
profit at the target, cuts stops, defends a tested short strike inside 7 DTE.
Requires a live market: a close at 22:00 against the afternoon print is a fill
nobody could have got.

| Flag | Effect |
|---|---|
| `--no-auto-exit` | Decide but do not execute — print the exits that are due |

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

Four gates run at fill time regardless of who approved: the master trading
switch, a recorded approver, a settled session, and a balance the account can
actually pay the max loss from. A worse fill means *more* collateral, so the
balance check runs on the filled number, not the ticket's.

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
Prints every configurable knob, its current value, the strategy's own number
beside it, and **everything this account has moved away from the mandate**.

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
| `take_profit_pct` | strategy's 55% | Where profit is booked |
| `stop_loss_credit_multiple` | `2.0` | Stop when buyback costs this × the credit |
| `stop_loss_pct_of_max_loss` | `0.50` | ... or when down this fraction of max loss |
| `max_total_collateral` | `2400` | Across the whole book |
| `max_open_positions` | `4` | |
| `max_positions_per_sector` | `2` | |
| `max_positions_per_ticker` | `1` | |
| `mode` | `paper` | `live` is refused in code |
| `chain_source` | `yfinance` | `yfinance` \| `robinhood` \| `model` |
| `opening_settle_minutes` | `30` | No opening inside this many minutes of the bell |

Unset means *use the strategy's number*. Every override is reported on the
ticket, on the dashboard's Rules tab, and by `config` — a ticket sized under a
loosened cap can never be read as one that met the standard rule.

**No setting can lift the approval requirement off a live ledger.**

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

## On the server

Everything runs as the `pcs` service account out of `/opt/pcs`.

```bash
sudo -u pcs /opt/pcs/.venv/bin/python /opt/pcs/run.py config
```

### Timers

| Unit | When | What |
|---|---|---|
| `pcs-watch.timer` | **hourly, 24/7** | Refresh the watchlist. Observation only |
| `pcs-propose.timer` | 10:15 ET, weekdays | Screen, size, and open clear proposals |
| `pcs-mark.timer` | every 15 min, 09:35–15:50 ET | Re-price, take due exits |

The market-hours jobs carry their timezone in the calendar spec
(`Mon..Fri 10:15 America/New_York`), so they follow DST without the box clock
being involved. `pcs-watch` has no window because nothing it does can trade.

```bash
systemctl list-timers 'pcs-*'
```

```bash
sudo systemctl start pcs-watch.service && sudo journalctl -u pcs-watch.service -n 50 --no-pager
```

Run a job by hand **outside market hours** to prove the whole path — it screens,
sizes and ranks for real, then holds at the fill because the market is shut.

### Redeploy

```bash
cd ~/put-credit-spread-agent && git pull && sudo ./deploy/bootstrap.sh
```

Idempotent. It never overwrites the box's ledger, proposals or settings, and it
runs the test suite on the box before finishing.

### Logs

```bash
sudo tail -n 50 /opt/pcs/logs/propose.log
```

`propose.log`, `mark.log`, `watch.log`, one per task.
