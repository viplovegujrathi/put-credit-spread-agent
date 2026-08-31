# Architecture

Nine stages, one direction, one mandatory human gate. Each stage is a module
with a narrow job, so the 50-day-*low* variant or the bear-debit-spread variant
can replace the screen without touching anything downstream.

```
┌─ 1. Universe ────────────┐  pcs/universe.py
│  503 S&P 500 names +     │  Wikipedia (fallback: datasets CSV), cached with
│  GICS sector, staleness  │  an as_of date. Sector feeds the concentration cap.
└────────────┬─────────────┘
             ▼
┌─ 2. Market data ─────────┐  pcs/marketdata.py
│  spot, 52wk range,       │  Bulk yfinance download, 40 tickers/request.
│  50dma, avg volume,      │  Cached to data/snapshots.json.
│  next earnings date      │  Earnings pulled per-ticker, late (see step 5).
└────────────┬─────────────┘
             ▼
┌─ 3. Screener ────────────┐  pcs/screener.py
│  ≥15% off the high AND   │  Every name gets exactly ONE bucket:
│  3-8% below the 50dma    │  PRIMARY · NEAR_BELOW_TIGHT · BELOW_STRETCHED ·
│                          │  BROKEN_DOWN · ABOVE_50DMA · NOT_BEATEN_DOWN ·
│                          │  ILLIQUID_UNDERLYING · NO_DATA
└────────────┬─────────────┘  Nothing is dropped silently.
             ▼
┌─ 4. Chain fetcher ───────┐  pcs/chains.py + pcs/session.py
│  real bid/ask/OI/IV at   │  yfinance | robinhood snapshot | modeled (tagged).
│  ~32 DTE, per symbol     │  Expiration read off the LIVE chain, per symbol —
│                          │  not every S&P name lists weeklies.
└────────────┬─────────────┘  Session grades quotes live/closing/stale.
             ▼
┌─ 5. Optimizer ───────────┐  pcs/optimizer.py
│  search every (short,    │  credit ≥ $100 · collateral ≤ $1,000 ·
│  long) pair; rank by     │  cushion ≥ 3% · natural credit ≥ 75% of the floor ·
│  credit ÷ collateral     │  tradeable package market.
└────────────┬─────────────┘  Ranking by ROC IS the narrowest-width rule.
             ▼
     earnings check          Only for names that produced a spread, against
             │               THAT symbol's expiration. Unknown ⇒ blocked.
             ▼
┌─ 6. Risk manager ────────┐  pcs/risk.py
│  total collateral cap ·  │  Caps bind across the whole batch, not one
│  max positions · sector  │  proposal at a time. Correlation warns, never
│  cap · one per ticker    │  silently blocks.
└────────────┬─────────────┘
             ▼
┌─ 7. Trade proposer ──────┐  pcs/proposer.py
│  one readable ticket     │  Strikes, credit (with the natural alongside),
│  per candidate           │  collateral, cushion, POP, liquidity, pricing
└────────────┬─────────────┘  basis, earnings status, warnings, rationale.
             ▼
    ╔═══════════════════════╗
    ║   HUMAN APPROVAL      ║  ./run.py approve <id> --approver "<name>"
    ║   required, every     ║  Enforced in code: open_approved() raises
    ║   time, no exceptions ║  ApprovalRequired without a named approver.
    ╚═══════════┬═══════════╝
                ▼
┌─ 8. Execution adapter ───┐  pcs/paper_broker.py
│  paper fill, deliberately│  Fill is never better than the ticket. In live
│  worse than the ticket   │  mode this refuses outright — a human places the
└────────────┬─────────────┘  order at the broker.
             ▼
┌─ 9. Logger / dashboard ──┐  pcs/ledger.py + pcs/dashboard.py
│  positions, marks, P&L,  │  Append-only event log. Exit advice per section
│  exit advice, HTML       │  1.7 is surfaced, never acted on.
└──────────────────────────┘
```

## Design principles

**Propose, never place.** The gate is code, not documentation.
`open_approved()` raises without a named approver, and refuses entirely when
`mode == "live"`.

**Modeled prices can never masquerade as quotes.** Every price carries a
`basis` (`live` / `modeled`) and a `source`, all the way to the ticket.

**The screen and the sizing are decoupled.** Steps 1–3 answer "is this a
candidate"; steps 4–5 answer "what strikes satisfy the constraints." Swapping
in the 50-day-low screen touches only `screener.py`.

**Everything is logged.** `data/ledger.json` carries an append-only event list
so any past proposal or fill can be reconstructed.

## Data flow between the agent and the script

MCP tools belong to the agent; the Python process cannot call them. Rather
than fake that, the broker path is explicitly two-phase:

```
./run.py chain-requests                 # symbols, expiration, strike range
   ↓  agent: get_option_instruments + get_option_quotes  →  data/rh_raw/
python3 tools/rh_ingest.py              # normalise      →  data/rh_chains/
./run.py propose --source robinhood
```

## Where the numbers live

| Kind | Where | Changeable? |
|---|---|---|
| Strategy rules (`$1,000`, `$100`, 15%, 3–8%, 32 DTE) | `Strategy` in `pcs/config.py`, frozen, asserted at import | Only on explicit request |
| Portfolio caps (total collateral, max positions, sector) | `Settings` → `data/settings.json` | Yours |
| Liquidity gates, fill model, slippage | `Settings` | Yours |
