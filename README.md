# S&P 500 Beaten-Down Put Credit Spread Agent

Screens the S&P 500 for stocks that are **meaningfully off their 52-week high**
and **resting near their 50-day moving average**, sizes a **bull put credit
spread** on each one against a fixed risk rule, and writes a reviewable ticket.

**It proposes. It never places.** Every trade needs explicit per-trade human
approval, and the approval path currently fills into a **$3,000 paper account**.

```bash
pip install -r requirements.txt
./run.py screen            # who qualifies, and why everyone else didn't
./run.py propose           # screen -> size -> portfolio risk -> tickets
./run.py approve P260831-01 --approver "your name"
./run.py mark              # refresh marks, surface exit decisions
open dashboard.html
```

---

## The rules it enforces

Fixed by the strategy, asserted at import in [`pcs/config.py`](pcs/config.py),
and covered by tests:

| Rule | Value |
|---|---|
| Off the 52-week high | **≥ 15%**, no ceiling |
| Distance from the 50dma | **3–8% below** = the setup |
| More than ~12% below the 50dma | **excluded** — broken down, not "near the average" |
| Above the 50dma | **excluded** — recovery thesis, reported separately |
| Structure | **bull put credit spread only** |
| Collateral (max loss) per trade | **≤ $1,000** |
| Credit per trade | **≥ $100**, **no upper cap** |
| Short strike | **≥ 3% OTM** — never pulled to the money to hit the number |
| Expiration | **~32 DTE**, a real listed Friday confirmed against the chain |
| Earnings inside the window | **excluded** |
| Opening range | **no position opened in the first 30 min** after the bell — paper included |
| Available balance | **no position opened that the account cannot pay the max loss on** |
| Take profit | **booked automatically at 55%** of max credit (50–65% band) |
| Stop loss | **2× the credit taken in**, or 50% of the defined max loss, whichever comes first |
| Short strike tested inside 7 DTE | **closed** rather than carried into expiration — **never remove the long leg** |

Portfolio caps are yours to set ([`data/settings.json`](pcs/config.py)); the
defaults for a $3,000 account are $2,400 total collateral, 4 open positions,
2 per sector, 1 per ticker.

Names are never silently blended: every ticker lands in exactly one labelled
bucket (`PRIMARY`, `NEAR_BELOW_TIGHT`, `BELOW_STRETCHED`, `BROKEN_DOWN`,
`ABOVE_50DMA`, …) and the excluded buckets are reported alongside the
tradeable one.

---

## Commands

| Command | What it does |
|---|---|
| `./run.py screen [--verbose]` | Run the screen over all 503 constituents, print every bucket |
| `./run.py propose` | Screen → size → portfolio risk → proposal tickets |
| `./run.py approve <id> --approver <name>` | **The human gate.** Fills into the paper ledger |
| `./run.py reject <id> --reason "..."` | Record a decline |
| `./run.py mark` | Re-mark open positions, then **take any exit that is due** |
| `./run.py mark --no-auto-exit` | Decide but do not execute — print the exits that are due |
| `./run.py status` | Cash, collateral, available balance, net liq, open/closed |
| `./run.py close <position-id>` | Close a paper position at the current mark |
| `./run.py dashboard` | Rebuild `dashboard.html` |
| `./run.py universe --refresh` | Refresh the S&P 500 constituent list |
| `./run.py chain-requests` | Emit the chains to pull through the Robinhood MCP |

Useful flags: `--source {yfinance,robinhood,model}`, `--symbols AAPL MCD`,
`--include-tight`, `--contracts N`, `--max-cache-age 180`, `--expiration YYYY-MM-DD`.

---

## Data sources

| Source | Used for | Notes |
|---|---|---|
| **Yahoo (yfinance)** | price history, 52-week range, 50dma, earnings dates, fallback chains | Free, bulk. Its option chains are **incomplete** — 16 put strikes for MCD 2026-10-02 where Robinhood lists 44 |
| **Robinhood (MCP)** | the confirmation chain: full strike ladder, mark, greeks, broker chance-of-profit, fill-rate prices | Preferred for sizing. **Required before any live trade** |
| **Black-Scholes** | fallback only | Every number it produces is tagged `modeled` |

MCP tools belong to the agent, not to a Python process, so the broker path is
two-phase by design:

```bash
./run.py chain-requests                 # 1. what to pull, and over which strikes
#  2. agent runs get_option_instruments + get_option_quotes,
#     drops raw payloads into data/rh_raw/
python3 tools/rh_ingest.py              # 3. normalise into data/rh_chains/
./run.py propose --source robinhood     # 4. size on the broker's own book
```

---

## An open position can never exceed the available balance

The account can always pay its own max loss. `open_approved` refuses otherwise:

```
HELD - this position needs $636.12 of free balance and the account has $300.00
  (cash $300.00 less $0.00 already at risk on 0 open position(s)). Close
  something or size down -- an account cannot hold more max loss than it can pay.
  P260831-01 stays pending; the ledger is unchanged.
```

Three details this depends on:

**Available balance is `cash − capital at risk`, not `cash − collateral`.**
`cash` already includes the credit received and collateral is measured net of
that same credit, so the obvious formula counts the premium twice. It reported
$2,720 free on an account holding $2,610. The number the agent uses nets off the
full width the book could be called on to pay, which reduces to the intuitive
statement: *starting cash + realised P/L − collateral − fees*.

**It is checked on the filled collateral, not the ticket's.** A worse fill means
less credit, which means *more* collateral — a $624 ticket filled at $636 in a
live run. The gate sees the number the account actually ends up holding, and the
same check keeps a fill from drifting past the $1,000 per-trade cap.

**It binds across a batch.** Proposals ranked ahead of a given one have already
spoken for their share of the balance, so five proposals that each fit alone
cannot all pass.

---

## Profit booking and stop losses are the agent's own decision

`./run.py mark` re-prices every position and then acts:

```
EXITS TAKEN (2) -- decided and executed by the agent
  TAKE PROFIT  AAA 97/92 [pos-AAA]  realized $63.88
      at 64% of max credit (target 55%, band 50%-65%) -- book $64 for a $36
      debit rather than grind the last 36% against 20 days of gamma
  STOP LOSS  BBB 97/92 [pos-BBB]  realized $-131.12
      buying it back costs $231, 2.3x the $100 credit taken in (stop is 2x)
      -- down $131, cut it
```

A profit target only works if it is taken mechanically, and a stop has to be
able to fire while nobody is watching — so exits do **not** go through the
per-trade approval gate that entries do. The autonomy is bounded in code
instead:

- **Closing only.** `apply_exits` can only buy back a short the account already
  carries. It cannot open exposure. The entry gate is untouched.
- **Paper only.** It refuses a live ledger and prints the closing order for you
  to place yourself.
- **Fresh marks only.** A position whose mark failed to update decides nothing.
- **Not blocked by the opening range.** That gate stops *new* risk; holding a
  losing position open through the first 30 minutes would be the opposite of
  risk management.

Two stop conditions, whichever hits first — because `2×` the credit is
unreachable when the credit is large relative to the width, and those are
exactly the positions with the thinnest collateral:

| Trigger | Default | Setting |
|---|---|---|
| Buyback costs a multiple of the credit | `2.0×` | `stop_loss_credit_multiple` |
| Down a fraction of the defined max loss | `50%` | `stop_loss_pct_of_max_loss` |
| Profit booked at | `55%` of max credit | fixed by the strategy |

`--no-auto-exit` decides without executing; `auto_exit: false` in
`data/settings.json` makes that the default.

---

## Nothing opens in the first 30 minutes

The opening auction is still clearing, overnight orders are being absorbed, and
spreads have not converged — the first half hour is the widest, thinnest book
of the day. `open_approved()` refuses to fill inside it and the proposal stays
pending:

```
HELD - inside the opening range - no positions are opened until 10:00 ET
  (20 min away). The opening book is the widest and thinnest of the day;
  a fill taken here is not one the live account could count on.
```

This is a **hard gate in the fill path**, not a warning, and it applies to
paper too — a paper record built on opening-auction fills would overstate what
the live account could have achieved. Quotes inside the range are still graded
`live` (they are genuinely live), so sizing is not penalised as if they were
stale; only *opening* waits. The window is
`Settings.opening_settle_minutes` (default 30; set it to 0 to opt out).

Screening and proposing during the opening range are fine — the ticket carries
a warning saying it was sized there.

## Quote quality is not constant

A `2.35 / 3.25` market at the closing bell is a real market; the same market at
18:00 ET tells you nothing about tomorrow's fill. [`pcs/session.py`](pcs/session.py)
classifies every run as `live`, `closing_snapshot`, or `stale`, widens the
bid/ask gates accordingly, and stamps the state onto every proposal and the
dashboard banner.

A vertical fills as one package, so pricing and liquidity are judged on the
package, not leg by leg:

```
nat  = short_bid - long_ask       worst realistic fill
mid  = short_mid - long_mid       fair value (broker mark when available)
used = mid - 0.15 x (mid - nat)   the sizing basis
```

`nat` travels with every proposal, and a spread whose natural credit falls
under $100 is flagged `fill_risk` rather than quietly presented as a clean
pass. Paper fills take a deliberately worse haircut (0.25) than the sizing
basis, so a paper track record cannot flatter itself.

---

## Layout

```
pcs/
  config.py        strategy rules (fixed, validated at import) + user settings
  session.py       market-hours awareness and quote-quality grading
  universe.py      S&P 500 constituents + GICS sector, with staleness tracking
  marketdata.py    price, 52-week range, 50dma, earnings dates, snapshot cache
  screener.py      the two-condition screen; one labelled bucket per name
  chains.py        option chains: yfinance | robinhood snapshot | modeled
  optimizer.py     the strike search and every per-trade constraint
  risk.py          portfolio-level caps, the balance floor, correlation warnings
  exits.py         take-profit / stop-loss / defend decisions (pure policy)
  ledger.py        paper account, positions, append-only event log
  paper_broker.py  the three open gates, fill simulation, marking, exit execution
  proposer.py      human-readable tickets
  pipeline.py      screen -> size -> risk -> propose
  dashboard.py     self-contained dashboard.html
  bs.py            Black-Scholes fallback pricing
run.py             CLI
tools/rh_ingest.py Robinhood MCP payloads -> chain snapshots
tests/             99 tests over the rules that must not silently break
docs/              STRATEGY.md, ARCHITECTURE.md
LEARNING.md        what building this actually taught us
```

```bash
python3 -m pytest      # 99 passing
ruff check .
```

---

## Before this touches real money

1. ~~**Options level.**~~ Cleared 2026-08-31: the agentic account is now
   `option_level_3`, which is what a spread requires. Re-verify with
   `get_accounts` rather than trusting this line.
2. **Fund it, or resize to it.** That same account holds **$14.72** of buying
   power — its value is in stock, not cash. Every current proposal needs
   $624–$991 of collateral, so the balance rule above would refuse all of them.
   Live sizing has to start from the real buying power, and at that level the
   $1,000 collateral cap and $100 minimum credit leave very little that
   qualifies. Permission to trade spreads is not the same as being able to
   afford one.
3. **Size on the broker's chain**, not Yahoo's — see the two-phase flow above.
4. **Re-confirm during market hours.** A proposal built on a closing snapshot
   is indicative; the package may not be there at 09:30.
5. **Paper first.** Run the full weekly cadence long enough to see winners,
   losers, and at least one tested short strike.
6. The human gate is not a formality. Read the ticket. The agent has no path to
   placing a live order — `open_approved` refuses a non-paper ledger in code,
   and the level upgrade does not change that.

*Not financial advice. This is a tool for evaluating a strategy the user
already decided to run.*
