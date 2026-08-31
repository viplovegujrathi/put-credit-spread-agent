# What building this agent actually taught us

Notes taken while turning the strategy document into running code, on
2026-08-31. Every claim here was observed on live data, not assumed.

---

## 1. Free option-chain data has holes at exactly the strikes you need

Yahoo returned **16 put strikes** for `MCD 2026-10-02`. Robinhood returned
**44** for the same contract month. Yahoo drops strikes with no recent trading
activity — which is precisely the 3–8% OTM band this strategy sells into on a
quiet name.

The consequence is worse than "less choice": the optimizer searches
`(short, long)` pairs on the ladder it is given, so a missing $250 strike does
not produce a slightly worse spread, it silently removes the $5-wide spread
from consideration entirely and leaves only $10-wide ones. Early runs against
Yahoo kept reporting "needed wider width" for names where a narrow spread
existed and simply was not in the feed.

**What we did:** Yahoo stays the default so the pipeline runs unattended, but
Robinhood is the confirmation layer and is required before a live trade. The
chain source is a pluggable provider (`pcs/chains.py`), not a hardcoded call.

## 2. MCP tools belong to the agent, not to the script

The Robinhood connection is an MCP server the *agent* can call. A Python
process started from the CLI cannot reach it. This is easy to paper over with
a fake abstraction and painful to discover later.

**What we did:** made the seam explicit and two-phase rather than pretending
the script has broker access.

```
./run.py chain-requests   ->  agent pulls via MCP  ->  tools/rh_ingest.py  ->  ./run.py propose --source robinhood
```

## 3. The same bid/ask means different things at different times of day

At 18:06 ET, `MCD $255p` quoted `2.35 / 3.25` — a 32%-of-mid spread that any
sane liquidity filter rejects. But Robinhood's `updated_at` was
`19:59:59Z` = **15:59:59 ET**: this was the closing print, not a degraded
after-hours quote. Rejecting it as "illiquid" would have been wrong; treating
it as a tradeable market would have been equally wrong.

**What we did:** `pcs/session.py` grades every run `live` / `closing_snapshot`
/ `stale`, scales the bid/ask tolerance by that grade (1.0 / 1.8 / 2.5), and
stamps the grade onto every proposal, the ticket, and the dashboard banner. A
proposal built after the bell says so in writing.

## 4. A vertical fills as a package — per-leg gates reject tradeable spreads

The first liquidity filter checked each leg's bid/ask independently. It
rejected essentially everything, including spreads that trade fine, because a
cheap OTM put legitimately quotes `0.73 / 1.18` while the *package* is far
tighter than the sum of its legs.

**What we did:** the gate that matters is on the package
(`short.ask - long.bid` vs `short.bid - long.ask`, measured against the strike
width). Per-leg checks were kept only to weed out genuinely dead strikes —
no offer, or open interest below 25.

## 5. Which price you size on is a real decision, and mid is the honest default

Three defensible numbers for the same MCD 255/250 spread:

| basis | credit | what it means |
|---|---|---|
| natural (`short_bid - long_ask`) | $29 | hit the bid, lift the offer, simultaneously |
| **package mid** | **$111** | fair value, using Robinhood's own mark |
| Robinhood's high-fill-rate prices | $60 | where its model says a sell fills quickly |

The strategy document's own reference table lists MCD at *~$110 credit on
~$390 collateral* — it was calibrated on **mid**. Sizing on natural would have
disqualified nearly every reference example; sizing on mid alone hides the
risk that the fill never comes.

**What we did:** size at `mid − 0.15 × (mid − nat)`, carry `nat` on every
proposal, and flag `fill_risk` when the natural credit falls under $100 rather
than presenting the spread as a clean pass. Paper fills take a **worse**
haircut (0.25) than the sizing basis, so a paper track record cannot flatter
itself into a live-trading decision.

## 6. Ranking by return-on-collateral implements "narrowest width" for free

Section 1.4 says to default to the narrowest width that clears $100 rather
than widening toward the $1,000 cap. That reads like it needs special-case
logic. It does not:

```
$5-wide  clearing $110 on $390 collateral -> 28% ROC
$10-wide clearing $110 on $890 collateral -> 12% ROC
```

Sorting by `credit / collateral` puts the capital-efficient spread first by
construction. The only addition needed was cushion as a tie-break, so two
spreads with the same return resolve toward the safer strike.

## 7. An unknown earnings date is not a clean earnings date

Yahoo returns no forward earnings date for a meaningful slice of the index.
The tempting shape is `if earnings_date and earnings_date <= expiry: skip`,
which lets every unknown through as a pass.

The screen caught **NKE reporting 2026-10-01, one day before a 2026-10-02
expiration** — exactly the trade this rule exists to prevent. That only works
if missing data blocks too.

**What we did:** `earnings_in_window` is a tri-state (`True` / `False` /
`None`). `None` blocks by default, says *why* it blocked, and needs
`--allow-unknown-earnings` to override.

## 8. Not every S&P 500 name lists weeklies

`PODD` jumps from `2026-09-18` (18 DTE) straight to `2026-10-16` (46 DTE) —
nothing in the 28–38 day window. The first implementation probed one
candidate's chain for a batch-wide expiration; because PODD sorted first, the
entire run aborted with "could not resolve a listed expiration."

**What we did:** each symbol resolves its own expiration against its own live
chain, the batch-wide date is display-only, and a name with no listing in the
window is skipped with a reason instead of taking the whole run down. Earnings
are then checked against *that symbol's* expiration, not the batch's.

## 9. Order the pipeline by what is expensive

The screen over 503 names takes ~4m40s of mostly network wait; per-ticker
earnings lookups are slow and serial. The original order checked earnings for
all 56 shortlisted names *before* sizing — but sizing typically eliminates
half of them for having no qualifying spread, so most of that work was thrown
away.

**What we did:** size first, then look up earnings only for names that
produced a tradeable spread. Price snapshots also cache to
`data/snapshots.json`, so iterating on the sizing logic no longer costs a
full index download (`--max-cache-age 180`).

## 10. The account that can trade is not the account that can trade *this*

The Robinhood connection is live and the agentic account is real. It is also
**`option_level_2`** — long options and cash-secured puts. **Credit spreads
need level 3.** The level-3 account in the same login is not the one the agent
can reach.

This is a hard blocker for live execution that no amount of correct code fixes,
and it is exactly the kind of thing that stays invisible until the first order
rejects. It is now the first item in the README's go-live list.

## 11. Small environment things worth writing down

- This macOS python.org build has no CA bundle wired into `urllib`, so
  `pandas.read_html(url)` dies on SSL verification. `requests` carries
  `certifi` and works — route every fetch through it.
- Wikipedia's constituent table is the good source (503 names, with GICS
  sectors for the concentration cap), but it needs a browser User-Agent and a
  fallback. Ticker punctuation differs by venue: `BRK.B` on Wikipedia,
  `BRK-B` everywhere the options live.
- A partial `--symbols` run was overwriting `data/last_screen.json`, which the
  chain-request path reads. Partial runs now write to a separate file.

---

## What the tests actually protect

52 tests, and they are deliberately aimed at the rules that would fail
*silently* rather than at coverage:

- every 50dma bucket boundary, so near / stretched / broken / above never blend
- each per-trade constraint asserted **independently** (a spread must satisfy
  credit ≥ $100 *and* collateral ≤ $1,000 *and* cushion ≥ 3% on its own)
- no upper cap on credit — a rich premium must not be throttled back
- when nothing at ≥3% OTM clears $100, the name is skipped rather than the
  strike being pulled to the money
- portfolio caps bind across a *batch*, not one proposal at a time
- max loss can never exceed collateral, even on a gap to zero
- the paper fill is never better than the sizing basis
- exit advice never suggests removing the long leg
- an unknown earnings date does not pass
