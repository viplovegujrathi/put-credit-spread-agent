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
| Take profit | 50–65% of max credit |
| Short strike tested | roll down-and-out or accept the defined loss — **never remove the long leg** |

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
| `./run.py mark` | Re-mark open positions, surface section-1.7 exit advice |
| `./run.py status` | Cash, collateral, buying power, net liq, open/closed |
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
  risk.py          portfolio-level caps and correlation warnings
  ledger.py        paper account, positions, append-only event log
  paper_broker.py  the approval gate, fill simulation, mark-to-market, exit advice
  proposer.py      human-readable tickets
  pipeline.py      screen -> size -> risk -> propose
  dashboard.py     self-contained dashboard.html
  bs.py            Black-Scholes fallback pricing
run.py             CLI
tools/rh_ingest.py Robinhood MCP payloads -> chain snapshots
tests/             52 tests over the rules that must not silently break
docs/              STRATEGY.md, ARCHITECTURE.md
LEARNING.md        what building this actually taught us
```

```bash
python3 -m pytest      # 52 passing
ruff check .
```

---

## Before this touches real money

1. **Options level.** The Robinhood account this agent can reach is
   `option_level_2` — long options and cash-secured puts only. **Spreads need
   level 3.** Until that is upgraded, live execution is impossible regardless
   of what the code does.
2. **Size on the broker's chain**, not Yahoo's — see the two-phase flow above.
3. **Re-confirm during market hours.** A proposal built on a closing snapshot
   is indicative; the package may not be there at 09:30.
4. **Paper first.** Run the full weekly cadence long enough to see winners,
   losers, and at least one tested short strike.
5. The human gate is not a formality. Read the ticket.

*Not financial advice. This is a tool for evaluating a strategy the user
already decided to run.*
