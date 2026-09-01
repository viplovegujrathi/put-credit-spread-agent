# Council review — the dashboard

Three reviewers were convened against `pcs/dashboard.py` and the rendered page:
a **design** seat, a **trading operator** seat, and a **trust & safety** seat.

**Only the trading operator seat reported.** The design and trust & safety seats
both terminated on an API rate limit partway through, and neither returned
findings. Their sections below are empty on purpose — nothing has been written
into them on their behalf. Re-run those two seats before treating this document
as a complete review.

Reviewed at commit-in-progress, 2026-09-01, against the dark palette that was in
place at the time. The palette has since changed (§ *Design*, below); nothing
the operator seat found was about colour.

---

## Seat 1 — trading operator · **reported**

> *Brief: you have $3,000 in a paper account running unattended. You open this
> page once a day. Does it answer "what did the agent do, and am I in trouble?"*

**Verdict: it fails the 10-second test.** The page shows net liq, cash, and two
tickers. It does not show where the underlying is trading relative to the short
strikes, when those numbers were last true, or whether the agent tried to act
and could not. Those three things are what determine whether you are in trouble.

Worse: the page currently reads "flat, nothing happening" in a state where the
marks have never been refreshed since fill — which is exactly what a *broken
15-minute timer* also looks like.

### Must have

| # | Finding | Why it bites |
|---|---|---|
| 1 | **No spot price, no distance to short strike, no breakeven.** `_positions_table()` renders ticker, spread, expiry, qty, credit, collateral, cost-to-close, P&L, % of max credit — and throws away `pos.mark_spot`, which the exit engine reads. Breakeven (`short_strike − credit_open`) is derivable from stored fields and absent. | A 205/195p opened at 211.66 has 3.2% cushion. Overnight it prints 206 — 0.5% from the short strike — and the P&L column still says `$0`. Between "comfortable" and "already tested" there is no gradient displayed at all. |
| 2 | **Marks are undated, and every headline number silently inherits their staleness.** `Position.marked_at` is stored and never rendered. Net liq, unrealized P&L, total return, cost-to-close and P&L are all functions of `mark_cost_to_close`. A 3-day-old mark is displayed in the same typeface as one from 90 seconds ago. The top banner describes the *session clock*, not the *age of the data*. | yfinance drops a strike for a week. `cost_to_close()` returns `None`, `mark_positions` appends "could not mark" and moves on. The page keeps showing P&L `$0` and "everything fine" while the position is down $400. The warning goes to stdout, which nobody reads on a timer. |
| 3 | **An exit that was DUE but HELD is indistinguishable from no exit at all.** `cmd_mark` has three outcomes — taken, held (market closed), skipped (stale mark) — and the dashboard collapses all three into the same amber pill. It also re-runs `decide()` at render time on marks `exits.review()` would have refused to act on. | META gaps down Thursday after hours. `apply_exits` refuses (market shut), stdout says HELD. Friday's page shows an amber `STOP LOSS` pill; you assume the agent cut it — the Rules tab promises stops are cut by the agent — and don't look again. Held over the weekend, loss doubled. |
| 4 | **No heartbeat: a dead timer looks identical to a quiet market.** The only liveness signal is `rebuilt <timestamp>` as dim text. No last-mark-run, no next-scheduled-run, no runs-today count. The page only rewrites when a command runs, so a dead scheduler freezes it in a state that looks healthy. | The 10:15 timer dies Monday. All week the page shows the same two positions and the same $0 P&L. You read it every morning and conclude the market has been quiet. One spread went to max loss and the stop never fired. |
| 5 | **Concentration and worst-case are never stated as a fraction of the account.** Cards show `collateral at risk $1,331` and `available balance $1,668.76`. Nowhere does it say that is **44% of a $3,000 account** in two positions that are both high-beta tech despite passing the GICS sector cap. | A 4% Nasdaq down day takes both spreads through their short strikes at once. The sector cap in `risk.py` was satisfied the whole way in. The "diversified" book was one bet. |
| 6 | **Watchlist: the blockers are computed and then thrown away.** `watchlist.Entry.blockers` holds the real reasons from `risk.check()`; `_watchlist_panel()` never renders them. Also dropped: `pop_est`, the earnings *date* (EARNINGS with no date — tomorrow, or in three weeks?), and any liquidity signal at all. | Four names sit BLOCKED for a week. You cannot tell whether it is the position count, the sector cap, or the balance floor — so you cannot tell whether closing one winner would unblock three setups or none. |

### Nice to have

- **Returns are on the wrong base.** `total return` is `(net_liq − starting_cash) / starting_cash` and renders `-0.01%` in red — that is $0.24 of fees shown as a loss. For a defined-risk premium seller the meaningful figures are return **on collateral deployed** and **annualised on DTE**. History shows win rate but not **average credit capture %** or **exit-reason counts**: an 80% win rate can be 8 trades taken at 20% of max credit and 2 stopped at 2×, i.e. a net loser presented as healthy.
- **Mid-based marks presented as closeable.** `cost_to_close()` returns `mid + 0.25 × (nat − mid)`. The column is labelled `cost to close` with no qualifier, so P&L reads as money you could take now. The proposals table already does this correctly (`$101 / nat $93`); positions should match.
- **The event log will bury itself.** `mark_positions()` logs `marked` on every run — ~26 events per trading day — and `_history_panel()` renders every event, unfiltered and unpaginated. Within a week the two `position_opened` rows you care about are 200 rows down. The audit trail degrades fastest exactly as the account gets more interesting.

### What the operator wanted **alerted**, not displayed

Push, not pull — the whole premise is that nobody is looking:

1. Any position closed by the agent (stop, defend, or take-profit).
2. Short strike breached on any open position, the moment `mark_spot <= short_strike` — at any DTE, not only at the `defend_dte: 7` threshold where `exits.decide()` currently acts.
3. **An exit was due and was NOT taken** (held for a closed market, or skipped for a stale mark). The state that costs the most and is currently the quietest.
4. The mark loop hasn't run for more than 2 intervals during RTH, or ≥1 position failed to re-price on the last pass.
5. Unrealised drawdown crossing ~50% of collateral at risk book-wide, before any individual stop fires.

---

## Seat 2 — design · **did not report**

Terminated on an API rate limit before returning findings. Nothing here is the
council's; the palette work described below was done outside the review and is
recorded so the two are not confused.

Changed anyway, from direct inspection of the rendered page:

- The page is now **light by default**, with a header toggle for dark persisted
  per browser. It deliberately does not follow `prefers-color-scheme` — see
  `LEARNING.md` §17.
- `.tag` was defined twice — once for the brand tagline, once for the table
  pills — and the pill rules won, wrapping the tagline in a rounded box.
  Renamed to `.brandtag`.
- The tab strip wrapped and orphaned "Rules" onto a centred second row. Now
  `flex-wrap: nowrap` with horizontal scroll and a hidden scrollbar.
- Stat cards carry a semantic accent stripe (risk amber, P&L green/red, neutral
  blue). A flat P&L stays neutral rather than tinting red.

**Still unreviewed by a design seat:** typography scale, information density on
mobile, whether the colour semantics survive for a colour-blind reader, and
whether the logo reads at favicon size.

---

## Seat 3 — trust & safety · **did not report**

Terminated on an API rate limit before returning findings. The brief was:

- Is the paper-vs-real distinction unmissable on every screen, or only on the
  Rules tab?
- Is "per-trade human approval is OFF" prominent enough given it is the single
  most consequential setting on the page?
- Does the page overstate what the agent knows — modelled numbers presented as
  quoted, stale quotes presented as tradeable?
- Is anything exposed pre-authentication? (Separately verified from outside the
  box: the 401 page leaks no account data.)

**This seat should be re-run.** It is the one whose findings would gate a
deploy, and it is the one that did not report.

---

## Status

**All six must-have findings and all three nice-to-haves are implemented**, as
of 2026-09-01. What changed:

| # | Finding | Where it landed |
|---|---|---|
| 1 | spot, distance to short strike, breakeven | `_positions_table()` gained `spot`, `to short` and `breakeven` columns; `Position.breakeven` and `Position.cushion` are derived from stored fields |
| 2 | undated marks | `Position.mark_age_minutes` + `_mark_state()`; every row says when it was priced, and stale/aging/never render differently. `cost to close` is labelled **modelled mid** |
| 3 | due-but-held is indistinguishable from handled | `_exit_pill()` renders four distinct states — will close / HELD (market shut) / NEEDS YOU (auto-exit off or live) / NOT DECIDED (stale mark). The page no longer presents a render-time `decide()` on a mark `review()` refused to act on |
| 4 | no heartbeat | `pcs/health.py` persists every run to `data/health.json`; `_heartbeat()` shows last-run age and runs-today per timer, red when the mark loop has missed two intervals during RTH |
| 5 | concentration never stated as a fraction | the collateral card carries `% of net liq` and turns red above 50%; the limits list states the worst case in dollars *and* percent, and says the sector cap counts labels, not correlation |
| 6 | watchlist blockers thrown away | done earlier; `pop_est` and the earnings **date** now render too (`Entry.earnings_date` is carried from the candidate) |

Nice-to-haves: average credit capture % and exit-reason counts sit next to the
win rate; `cost to close` is qualified; the event log filters routine `marked`
rows and caps at 250 with a footer saying what was hidden — they stay in
`data/ledger.json`, which is the audit trail.

**The alert list is detected, not delivered.** `health.alerts()` implements all
five states the operator wanted pushed, and `_alerts_panel()` renders them above
the cards. That makes them impossible to miss *on the page*, which is still a
pull. A real push needs a delivery channel (email, webhook, SMS) and that is a
decision — an outbound integration and a place to put a secret — not an
implementation detail. The detection half is done and tested; wiring a channel
to it is a small change once the channel is chosen.

Seats 2 and 3 (design, trust & safety) still have not reported. Seat 3 is the
one whose findings would gate a deploy.
