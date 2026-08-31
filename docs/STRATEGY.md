# Beaten-Down S&P 500 Put Credit Spread Agent — Strategy & Architecture

*Reference document for an automated/semi-automated agent. Not financial advice. This agent proposes trades only — it never places an order without explicit human approval. All modeled prices must be replaced with live broker/chain data before any real trade.*

---

## 1. Strategy Definition (the rules an agent must follow exactly)

### 1.1 Universe
- S&P 500 constituents only. Refresh the constituent list periodically (index membership changes a few times a year).
- Liquid options only: minimum average daily option volume and open interest at the target strikes (reject illiquid names even if they pass the price screen — a technically-attractive spread with a 20%-wide bid/ask is not tradeable at the modeled price).

### 1.2 Screen — "beaten down AND near the 50-day average"
A candidate must pass BOTH filters:

1. **Off its 52-week high** by a meaningful amount. Use 15% or more off the 52-week high as the floor; there is no need to cap how far off the high a name is (a stock 50% off its high, like NKE in our examples, still qualifies — it's simply more beaten down).
2. **Near its 50-day simple moving average (50dma)** — within roughly 3-8% of it, on either side. Two important distinctions the agent must apply, not blend together:
   - Price sitting **below** the 50dma by 3-8%: the classic "pulling back to test support from underneath" setup.
   - Price **more than ~10-12% below** its 50dma: this is a broken-down stock, not a "near the average" stock — **exclude it** from this screen (it may still qualify for the separate 50-day-low screen in section 1.6, but the mechanics differ).
   - Price **above** its 50dma: this reads as a recovering / continuation setup rather than a beaten-down bounce play. Flag it explicitly as a different thesis rather than silently including it (see the Disney example below).

3. **No earnings report inside the expiration window.** Pull the next scheduled earnings date for every candidate and exclude (or explicitly flag) any name reporting before the option expires.

### 1.3 Structure — Put Credit Spread ONLY
This agent trades exactly one structure: the **bull put credit spread**.
- **Sell** a put at a strike a few percent below current price (the short strike defines your support/entry level).
- **Buy** a put at a lower strike, same expiration (the long strike defines your max loss).
- Do **not** substitute a naked cash-secured put, a debit spread, or any other structure under this skill. (Earlier exploration in this project covered cash-secured puts and bear put debit spreads as alternatives — this agent's mandate is credit spreads only.)

### 1.4 Sizing constraints — apply both, every time
- **Collateral (max loss) per trade ≤ $1,000.** Collateral = (strike width × 100) − (net credit × 100).
- **Minimum credit per trade ≥ $100** (at 100-share/1-contract size).
- **No upper cap on credit/profit.** If a name's real option chain offers $250, $400, or more in credit while still keeping collateral ≤ $1,000, take the higher credit — more premium for the same capped risk is strictly better. Do not throttle back to "under $300" for its own sake.
- **Do not force the collateral toward $1,000 by default.** Size the width to what the stock's actual premium supports:
  - If a $5-wide spread at a sensible OTM strike (3-8% out) already clears $100 in credit, use it — that's more capital-efficient (e.g., MCD/LHX/UNH/BA/GOOGL in the worked examples below cleared $100+ credit on only ~$380-400 of collateral).
  - Only widen the spread (up to the $1,000 ceiling) when the stock's premium genuinely requires it to reach the $100 floor (e.g., WYNN needed a $9-wide spread; NKE and LVS needed either a tighter, riskier strike or a $10-wide spread to clear $100).
  - If no combination of width and a reasonable OTM strike (≥3% out) clears $100 in credit and stays ≤ $1,000 collateral, **skip the name** rather than moving the short strike to the money just to hit the target.

### 1.5 Expiration
- Target **~30-35 days to expiration**, landing on a standard Friday expiration.
- Confirm the exact date against the live chain — don't assume a generic "32 days from today" lands on a real listed expiration; pick the nearest actual Friday (weekly or monthly) at or near that target.

### 1.6 Variant screens this agent may also be asked to run (kept separate from the primary mandate above)
- **50-day low screen** (not the 50dma): stocks trading at/near their 50-day *low* rather than their 50-day *average* — a deeper, more technical oversold signal. Same credit-spread sizing rules apply, but expect wider gaps between price and any "support" strike.
- **Bear put debit spread**: for a name breaking down further rather than bouncing (e.g., the ACN $190/$160 example) — this is a directional bet, sized by debit paid (not collateral), and is explicitly NOT part of the standing put-credit-spread mandate. Only run this on direct request, and always label it as the opposite thesis.

### 1.7 Exit / management rules
- Take profit at 50-65% of max credit received; don't hold for the last few dollars of theta.
- If the short strike is tested with time remaining: roll down-and-out for a further credit, or accept the defined max loss. Never remove the long put leg to "save on cost" once in the trade — that re-introduces undefined risk.
- Recompute the whole screen on a fixed cadence (weekly is reasonable) rather than trading ad hoc.

### 1.8 Portfolio-level risk rules (apply across all open positions, not just per-trade)
- Cap total collateral deployed across all open spreads at a level the user sets explicitly (this is a portfolio limit, distinct from the $1,000-per-trade cap).
- Watch for sector concentration — several candidates in this project clustered in Consumer Discretionary and Industrials; don't stack multiple correlated names without flagging the correlation to the user.
- Never increase risk on a losing position by removing the hedge leg.

---

## 2. Worked examples this agent should treat as reference calibration

| Ticker | Sell put | Buy put | Width | Expiration | Est. credit | Collateral | Note |
|---|---|---|---|---|---|---|---|
| MCD | $255 | $250 | $5 | 10/02/26 | ~$110 | ~$390 | Efficient — near 50dma, low collateral |
| LHX | $255 | $250 | $5 | 10/02/26 | ~$116 | ~$384 | Efficient |
| UNH | $370 | $365 | $5 | 10/02/26 | ~$120 | ~$380 | Efficient |
| BA | $197 | $192 | $5 | 10/02/26 | ~$106 | ~$394 | Efficient |
| GOOGL | $320 | $315 | $5 | 10/02/26 | ~$106 | ~$394 | Efficient |
| WYNN | $85 | $76 | $9 | 10/02/26 | ~$102 | ~$798 | Needed wider width to clear $100 |
| NKE | $38 | $28 | $10 | 10/02/26 | ~$104 | ~$896 | Needed a tighter (~2.7% OTM) strike to clear $100 |
| DIS | — | — | — | — | — | — | Excluded: price is *above*, not below, its 50dma — different thesis, flagged not traded |

These were modeled from each stock's implied volatility, not live quotes — the agent must replace this style of estimate with real bid/ask data from the broker's option chain before proposing an actual trade.

---

## 3. Agent Architecture

```
┌─────────────────────┐
│ 1. Universe Loader    │  S&P 500 constituent list (refreshed periodically)
└──────────┬───────────┘
           ▼
┌─────────────────────┐
│ 2. Market Data Feed   │  Price, 52-wk high/low, 50dma, next earnings date,
│                       │  per ticker. Live data source required — modeled/
│                       │  estimated data (as used in this conversation) is
│                       │  fine for prototyping, not for real trades.
└──────────┬───────────┘
           ▼
┌─────────────────────┐
│ 3. Screener           │  Applies section 1.2 filters: %-off-high, %-from-
│                       │   50dma (with direction check), earnings-date
│                       │  exclusion. Outputs a candidate list.
└──────────┬───────────┘
           ▼
┌─────────────────────┐
│ 4. Options Chain      │  Pulls the live put chain for each candidate at the
│    Fetcher            │  target expiration (~30-35 DTE, nearest real Friday).
│                       │  Needs real bid/ask/OI/volume, not a pricing model.
└──────────┬───────────┘
           ▼
┌─────────────────────┐
│ 5. Spread Optimizer   │  For each candidate: searches short/long strike
│                       │  combinations for the narrowest width that clears
│                       │  credit ≥ $100 while collateral ≤ $1,000, subject to
│                       │  a minimum OTM cushion (e.g., ≥3%) and liquidity
│                       │  filters on both legs. Ranks by credit ÷ collateral.
└──────────┬───────────┘
           ▼
┌─────────────────────┐
│ 6. Risk Manager       │  Applies portfolio-level checks (section 1.8):
│                       │  total collateral cap, sector concentration,
│                       │  earnings blackout, max open positions.
└──────────┬───────────┘
           ▼
┌─────────────────────┐
│ 7. Trade Proposer     │  Formats a human-readable ticket per candidate
│                       │  (ticker, strikes, width, credit, collateral,
│                       │  cushion, expiration, rationale) — see section 2
│                       │  table format. NEVER auto-submits an order.
└──────────┬───────────┘
           ▼
   ┌───────────────┐
   │ Human review    │ ← required gate, every time, no exceptions
   │ & approval      │
   └───────┬───────┘
           ▼
┌─────────────────────┐
│ 8. Execution Adapter  │  Only after explicit approval: submits the approved
│                       │  spread as a limit order (net credit, at or better
│                       │  than the price shown to the human) via the
│                       │  connected broker integration.
└──────────┬───────────┘
           ▼
┌─────────────────────┐
│ 9. Position Logger /  │  Records every open/closed position, credit
│    Dashboard          │  received, current mark, days to expiration, and
│                       │  P&L. Drives the roll/close decisions in 1.7.
└─────────────────────┘
```

Design principles baked into the architecture:
- **Propose, never auto-place.** Every trade this agent surfaces goes through a human approval gate before any order reaches a broker. This mirrors how the existing DITM-calls agent in this environment operates and should not be relaxed for this strategy.
- **Modeled prices are a prototyping stand-in, not a data source for live trades.** Everything in this conversation's worked examples used volatility-model estimates because no live option chain was reachable. A production version of step 4 must use the broker's real chain.
- **Separate the screen from the sizing.** The universe/screener (steps 1-3) answers "is this stock a candidate at all," while the optimizer (step 5) answers "what specific strikes satisfy the collateral/credit constraints" — keeping these separate makes it easy to swap in the 50-day-low variant or the bear-debit-spread variant (section 1.6) without touching the rest of the pipeline.
- **Everything is logged.** Step 9 exists so the agent (and the user) can audit exactly why a trade was proposed and what happened to it — required for any strategy trading real capital.

---

## 4. Skills / tools required to build this agent

| Capability | What it's for | Notes |
|---|---|---|
| **Market/reference data access** (price, 52-wk range, 50dma, earnings calendar) | Screener (step 2-3) | In this environment: WebSearch/WebFetch against a data site. For production: a real market-data API (e.g., a broker's data endpoint or a dedicated provider) is far more reliable and scriptable than scraping. |
| **Live options chain access** (bid/ask, strikes, expirations, OI, volume, IV) | Chain fetcher + optimizer (steps 4-5) | This is the piece this conversation could not do live — every credit/collateral figure above is modeled, not quoted. This is the highest-priority gap to close before real trading. |
| **Broker execution integration** | Execution adapter (step 8) | This environment already has a `ditm-robinhood-agent` skill wired to a `robinhood-trading` MCP server for a different (DITM calls) strategy — the same connector pattern (propose → human approval → place order) is the template to reuse here. |
| **Scheduling** | Re-running the screener on a cadence (e.g., weekly) | Use scheduled tasks (`create_trigger`), never an in-process cron — those don't survive session end. |
| **Persistent logging/dashboard** | Step 9 | A simple `dashboard.html` (or equivalent) tracking open positions, credit collected, and days-to-expiration, refreshed each time the agent runs — same pattern as the DITM agent's dashboard. |
| **Options pricing math (fallback only)** | Estimating credit/collateral when live chain data isn't available (prototyping, or a quick sanity check) | Standard Black-Scholes put pricing from spot, strike, IV, days-to-expiration, and risk-free rate — used throughout this conversation. Treat it as a fallback, never as the number a real trade is sized on. |

### Proposing this as a saved skill
Since this agent needs to be followed "strictly" across sessions, the cleanest way to make that durable is as an actual Claude skill (SKILL.md) rather than a one-off document — a skill loads directly into an agent's instructions and persists across conversations. I'll draft one from this document for your review (see the skill proposal alongside this file) — you can save it, edit it, or discard it.

---

## 5. Open items before this can run on real capital

1. **Live option chain data source.** Nothing above should be traded on the modeled premium numbers — confirm real bid/ask before sizing anything.
2. **Broker connection for execution.** Needs an actual brokerage MCP/API wired up with the human-approval gate enforced in code, not just in instructions.
3. **Constituent list refresh mechanism** for the S&P 500 universe.
4. **Earnings calendar source** reliable enough to automatically exclude names reporting inside the expiration window.
5. **User-set portfolio limits** (total collateral cap, max positions, sector caps) — this document leaves the numbers to you; only the per-trade $1,000/$100 rule is fixed.
