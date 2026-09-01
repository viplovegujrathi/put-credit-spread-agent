---
name: sp500-put-credit-spread-agent
description: >
  Screen S&P 500 stocks that are beaten down (meaningfully off their 52-week
  high) and trading near their 50-day moving average, then propose bull put
  credit spreads sized so collateral (max loss) stays at or under $1,000 and
  credit received is at least $100, with no upper limit on credit. Use whenever
  the user asks to screen for beaten-down put credit spread candidates, size a
  put credit spread against a collateral or profit target, run/refresh this
  standing options-income strategy, check or manage open spread positions, or
  mentions "50-day average," "beaten down stocks," "put credit spread," or a
  collateral cap alongside a minimum premium target. Never places trades
  automatically — always proposes for human approval.
---

# S&P 500 Beaten-Down Put Credit Spread Agent

This strategy is implemented as a runnable agent in this repository. **Prefer
running it over re-deriving the analysis by hand** — the CLI enforces every
rule below in code and produces an auditable record; hand analysis does not.

```bash
./run.py screen                                  # who qualifies, and why not
./run.py propose                                 # screen → size → risk → tickets
./run.py approve <id> --approver "<name>"        # the human gate
./run.py mark                                    # marks + exit decisions
./run.py status                                  # account and positions
```

Read [`../../README.md`](../../README.md) for flags, [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md)
for how the stages fit together, and [`../../LEARNING.md`](../../LEARNING.md)
for the data-quality traps that shaped the implementation.

## Why this skill exists

It captures a specific, previously-worked-out options income strategy so it
gets applied the same way every time. It trades exactly one structure (a bull
put credit spread) against exactly one setup (a beaten-down S&P 500 stock
resting near its 50-day average), sized to a fixed risk/reward rule. Treat the
rules below as fixed unless the user explicitly asks to change one for this run.

## Trigger

Use when the user asks to screen for beaten-down stocks near their 50-day
average for options income, size a put credit spread to a collateral/profit
target, refresh this standing strategy, or manage positions it opened.

The mandate is the **bull put credit spread only**. If the user asks for a
naked cash-secured put, a debit spread, or any other structure, treat that as a
related-but-different request — do not silently apply these rules to it.

## The rules

### 1. Universe
S&P 500 constituents only. `./run.py universe` reports how old the cached list
is; refresh it with `--refresh` if it has drifted (membership changes several
times a year).

### 2. Screen — both conditions required
- **Beaten down**: ≥15% off the 52-week high. **No ceiling** — a stock 50% off
  its high still qualifies and is simply more extreme.
- **Near the 50dma**: this is the part most likely to get blurred. Apply it
  carefully and never merge the cases:

  | Position vs 50dma | Bucket | Treatment |
  |---|---|---|
  | 3–8% **below** | `PRIMARY` | the intended setup — pulling back to test support |
  | 0–3% below | `NEAR_BELOW_TIGHT` | near, but barely pulled back — separate table, opt in with `--include-tight` |
  | 8–12% below | `BELOW_STRETCHED` | past the band — excluded by default |
  | >12% below | `BROKEN_DOWN` | broken down, *not* "near the average" — excluded (see the 50-day-low variant) |
  | **above** | `ABOVE_50DMA` | recovery/continuation — a **different thesis**. Flag it, never fold it in |

- **No earnings inside the expiration window.** An **unknown** earnings date
  blocks too — it is not a clean pass. Override only deliberately, with
  `--allow-unknown-earnings`.

### 3. Expiration
~30–35 days out, on an **actual listed Friday** confirmed against the real
chain. Not every S&P name lists weeklies, so each symbol resolves its own —
skip a name with no listing in the window rather than dragging it off target.

### 3a. Never open in the first 30 minutes
No position is opened inside the **opening range** — the first 30 minutes after
the 09:30 ET bell — and this applies to paper trades as well as live ones. The
opening book is the widest and thinnest of the day, so a fill taken there is not
one the live account could count on, and a paper record built on such fills
overstates the strategy. Screening and proposing during that window are fine;
only opening waits. Enforced as a hard gate in `paper_broker.open_approved`.

### 4. Sizing — bull put credit spread only
Sell a put a few percent OTM; buy a lower strike, same expiration.

- **Collateral = (width × 100) − (credit × 100) ≤ $1,000.**
- **Credit × 100 ≥ $100.** **No upper cap** — if the chain supports $250 or
  $400 within the collateral limit, keep it. Do not throttle back.
- **Default to the narrowest width** that clears $100 at ≥3% OTM. A $5-wide
  clearing $110 on ~$400 beats a $10-wide using $900 for the same $110. Only
  widen when the premium genuinely requires it.
- If no width/strike combination at ≥3% OTM clears $100 within $1,000
  collateral, **skip the name** — never move the short strike to the money to
  hit the number.

### 5. Use live data, not modeled prices
Size on real bid/ask, open interest, and volume. Two things the implementation
learned the hard way and that any manual analysis must also respect:

- **A vertical fills as a package.** Judge price and liquidity on
  `short_bid − long_ask` (natural) versus `short_mid − long_mid` (mid), not leg
  by leg.
- **Mid overstates most where the book is widest.** Always carry the natural
  credit alongside the mid, and do not present a spread whose realistic fill
  falls well under $100 as a clean pass.

If only a pricing model is available, label every figure **modeled/estimated**
and say plainly that live chain data must be checked before trading.

Yahoo's chains are incomplete (16 put strikes for MCD 2026-10-02 against
Robinhood's 44). For anything approaching a real trade, confirm on the broker's
own chain — `./run.py chain-requests` → agent MCP pull → `tools/rh_ingest.py` →
`./run.py propose --source robinhood`.

### 6. Present results as a table
Ticker, price, 50dma, % from 50dma, % off the 52-week high, sell strike, buy
strike, width, expiration, credit, collateral. Call out which candidates were
**capital-efficient** (cleared $100 easily on low collateral) versus which
**needed a tighter strike or a wider spread** — that distinction says something
real about risk. Note what was excluded and why.

### 7. Management (open positions)
Exits are the agent's own decision, and it acts on them. A profit target only
works if it is taken mechanically, and a stop has to fire while nobody is
watching — so these do **not** wait for the per-trade approval that entries do.

- **Book profit at 55%** of max credit (the 50–65% band); don't hold for the
  last few dollars of theta.
- **Stop out** when buying the spread back costs **2× the credit taken in**, or
  when the position is down **50% of the defined max loss** — whichever comes
  first. Both are needed: 2× the credit is unreachable when the credit is large
  relative to the width, and those spreads have the thinnest collateral.
- **Short strike breached inside 7 DTE**: close rather than carry assignment
  risk into expiration. Rolling down-and-out for a credit is the alternative.
  **Never remove the long put leg** — that re-introduces undefined risk.
- Recompute the screen on a fixed cadence (weekly is reasonable).

The autonomy is bounded and the bounds are enforced in code, not here: exits can
only ever **close** (they buy back risk the account already carries, and cannot
open exposure), they run against a **paper** ledger only — a live close is an
order and is rendered as a ticket for a human — and they are only ever decided
off a mark that actually re-priced. They are deliberately not blocked by the
opening-range rule in §3a; that gate exists to stop *new* risk.

### 7a. Never open beyond the available balance
No position is opened that the account cannot pay the max loss on. Available
balance is `cash − capital at risk`, where capital at risk is the **full strike
width**, not the width net of credit — `cash` already includes the premium and
collateral is measured net of it, so the obvious formula counts the credit
twice and overstates the balance. The check runs on the **filled** collateral,
which is worse than the ticket's whenever the fill is worse than the sizing
basis, and it binds across a batch.

### 8. Portfolio-level risk
Total collateral cap, max open positions, and sector caps are the user's to
set (`data/settings.json`; defaults for a $3,000 paper account are $2,400 /
4 positions / 2 per sector). Flag sector concentration rather than silently
stacking correlated names.

### 9. Never auto-place an opening trade
This skill proposes candidates and sizing for human review. It does not submit
opening orders, even though a broker integration is connected. Every trade that
*adds* risk needs explicit per-trade approval first, given in the conversation —
approval is never inferred from a previous one and never stored.

Exits are the exception, and only in the direction that reduces risk: see §7.

## Verification

Before presenting results, check:

- [ ] Every proposed spread independently satisfies collateral ≤ $1,000 **and**
      credit ≥ $100.
- [ ] No candidate has earnings inside the expiration window — and unknown
      dates were treated as blocking, not passing.
- [ ] The 50dma direction check was applied: near-and-below, broken-far-below,
      and above were kept in separate buckets, not blended.
- [ ] Every credit/collateral figure is labelled live-quoted vs. modeled,
      honestly — including which source and how fresh the quotes are.
- [ ] The natural (worst-case) credit is shown next to the sizing credit.
- [ ] No trade was described as placed or executed — only proposed.
- [ ] Nothing was opened inside the 30-minute opening range.
- [ ] No position was opened that the available balance could not cover, using
      the filled collateral rather than the ticket's.
- [ ] Any exit taken was a close, on a fresh mark, against a paper ledger — and
      was reported with the trigger that fired.
