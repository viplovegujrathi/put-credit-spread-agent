# Council review

Three reviewers sit against this agent: a **design** seat, a **trading
operator** seat, and a **trust & safety** seat.

**All three have now reported.** The operator seat first sat on 2026-09-01 and
its six must-haves were implemented; it re-sat on 2026-09-05 against the three
commits that landed since. Design and trust & safety were convened on
2026-09-01 and both terminated on an API rate limit without returning findings;
they reported for the first time on 2026-09-05.

Nothing in this document was written on a seat's behalf. Where a seat did not
report, its section said so and stayed empty until it did.

Reviewed at `13e3a28`, against a rendered page and a **stale local `data/`** —
the live record is only on the EC2 box, so the findings are about rendering and
enforcement logic, not the specific numbers.

---

## Convergence — what more than one seat found independently

These carry more weight than anything on a single seat's list, because three
briefs that share no vocabulary arrived at the same line of code.

| Found by | The thing |
|---|---|
| Operator #1, T&S #4 | **A position that refused to price this run still renders as decided.** `_mark_state` reads `marked_at`, which `mark_positions` deliberately does not touch on a refusal — so the row keeps the previous good mark's age and `_exit_pill` states an action the agent will not take. Both seats note the alert banner says the opposite at the top of the same screen. The operator seat found the row; T&S found the pill; the fix is one join, and `run.py:430` already persists the fact. |
| Operator (nice-to-have), Design #3 | **The heartbeat can only go red during market hours.** `_heartbeat`'s bad flag requires `sess.is_open` (`pcs/dashboard.py:774`), so a mark loop last seen five days ago prints in the calm style at a weekend. Design measured the contradiction in the DOM: `hb-ok marks 5d ago` at the top, `agemark stale marked 5d ago` 1,900px below. Both seats independently observed that a once-a-day reader reads in exactly the sessions where a dead timer cannot be flagged. |
| Operator #3, T&S #2 | **A proposal carries a verdict it was given in the past and is filled on it.** The operator seat found the green "clear" pill left behind by a `CoolingOff` refusal; T&S found that `cmd_approve` fills on a stored price *and* a stored risk verdict, and that `open_approved` re-checks the cooldown and the balance but not the count caps. Same root: nothing ages a proposal. |
| Operator (nice-to-have), Design (nice-to-have) | **`_sign()` has no dead-band**, so `-$0` and `-0%` render red on a position that has not moved. Filed by the operator seat in the first sitting, still live. |

---

## Seat 1 — trading operator · **reported twice**

### First sitting · 2026-09-01

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

### Status of the first sitting

**All six must-have findings and all three nice-to-haves were implemented**, as of
2026-09-01, and the 2026-09-05 re-review confirms none has regressed. What changed:

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

### Re-review · 2026-09-05

**Verdict: the engine got materially safer and the page did not keep up — three
new rules now act on my behalf and not one of them can be seen on the dashboard,
and the position row still cannot tell a price from a refusal to price.**

All six must-haves from 2026-09-01 remain in place; none has regressed. The cash
ladder (`_balance_table`, `pcs/dashboard.py:794`) is the best thing on the page —
it reconciles to a statement and then says, in its own row, that the agent stops
$669 short of it. `cost_to_close` refusing a one-sided book is the right call in
the right direction. The problem is that every one of these changes ends at the
edge of `pcs/dashboard.py`.

#### Must have

| # | Finding | Why it bites |
|---|---|---|
| 1 | **A mark the agent REFUSED to take renders identically to one it took.** `_mark_state()` (`pcs/dashboard.py:588`) reads only `Position.marked_at`, and `mark_positions()` deliberately leaves `marked_at` untouched when `cost_to_close` returns `None` (`pcs/paper_broker.py:271-279`). `pcs/health.py:72-74` states in its own comment that `marked_at` cannot identify a failed re-price — and the positions table is the last surface still using it. | Reconstructed and run: GOOGL 325/320p, good mark at 10:00, long-leg bid gone at 10:15. The row the 10:15 run writes says **`marked 15m ago`** in the fresh style, `cost to close $130`, `P&L −$14`, `−12%`. Nothing on the row says the run that just wrote it could not price the spread. `NOT DECIDED` — which I got last round for a stale mark — only appears once the row ages past 22 minutes. The alert banner *does* say "1 position(s) failed to re-price: GOOGL", so the two halves of one page contradict each other, and the half that is right is the one that vanishes if `health.json` fails to write (`pcs/health.py:135-136`, swallowed by design). |
| 2 | **The failing leg's reason reaches stdout and `journal.json` and no screen.** `unpriceable()` (`pcs/paper_broker.py:186`) exists specifically so the record names *which* leg and *why*; `cmd_mark` files it as a `MARK_FAILED` fault (`run.py:380-382`). The dashboard renders journal faults as a **bare count** — `("faults logged", f'{s["faults"]}', …)` at `pcs/dashboard.py:1263` — and there is no faults table anywhere in `_learning_panel`. The `mark_failed` alert (`pcs/health.py:261-267`) lists symbols only. | "long leg 320 quoted one-sided (bid 0, ask 0.05, no broker mark)" and "long leg 320 missing from the chain" are the same page to me: `faults logged 14`. The first means wait ten minutes; the second means the strike is gone and I should close by hand. LEARNING.md §38 says calling them both one thing "sends whoever reads it to the wrong place" — that is still exactly what the page does. |
| 3 | **A proposal `open_approved` refused still renders green "clear".** `_auto_open` catches `OpenBlocked`, prints `HELD`, files a fault, and leaves `p.status="pending"` with `p.risk_ok=True` untouched (`run.py:251-254`, `run.py:265-266`). `Proposal` has no field for a fill-time refusal (`pcs/proposer.py:20-46`). `_proposals_table` tags purely on `risk_ok` (`pcs/dashboard.py:949-950`). | The exact §40 sequence: ticket written 13:00 reading `risk_ok`, stop fires 13:50, `CoolingOff` refuses the fill. The Pending proposals table shows that ticket with a green **clear** pill and the caption "opened automatically on the next run" — which is false, and will stay false for five days. The only trace is `faults logged` going up by one. `doctor` catches it generically ("a gate inside open_approved refused the fill", `pcs/doctor.py:198-202`) and points me at `logs/propose.log`, which is the file this whole review exists because nobody reads. |
| 4 | **`doctor` — the designated "why has nothing opened?" answer — does not know the cooldown or the ticker cap exist.** `open_approved` documents five gates (`pcs/paper_broker.py:83-88`); `diagnose()` checks four and omits `CoolingOff` entirely (`pcs/doctor.py:91-175`). `risk.check` enforces six caps; doctor's "room in the book" block covers total collateral, count, balance and sector — not ticker (`pcs/risk.py:85-89`), not cooldown (`pcs/risk.py:93-98`). Zero matches for `cool` or `ticker` in `pcs/doctor.py`. | Two names stop out in a bad week. For the next five days `./run.py doctor` walks every check green and prints `verdict()` verbatim: *"Nothing is blocking a fill. If the book is still flat, the screen found no name that cleared the rules — which is a valid outcome, not a fault."* (`pcs/doctor.py:245-247`). It is not a valid outcome; it is a rule I configured, doing its job, lying to the only tool built to explain it. The module docstring claims it "walks the whole chain in the order the gates actually bind" — it no longer does. |
| 5 | **Neither new rule appears on the page at all, and `Entry.held` was added and then not rendered.** `max_positions_per_ticker` and `reentry_cooldown_days` occur **zero times** in `pcs/dashboard.py`, and `grep -ci "cooldown\|cooling\|per-ticker\|re-entry" dashboard.html` returns **0**. Rules → Portfolio limits (`pcs/dashboard.py:1522-1539`) lists collateral cap, count, sector cap, worst case, available balance, per-trade caps, and stops. `deviations()` walks only `_OVERRIDABLE` (`pcs/config.py:347-359`), so changing either value never shows in the Configuration panel either. `Entry.held` — the count added by 13e3a28 so the page could say *2 of 5 on this name* — is set at `pcs/watchlist.py:152` and appears in no row cell (`pcs/dashboard.py:1216-1221`). | This is my finding #6 from last round, re-committed. A HOLDING row says "already at the per-ticker cap on this name" and will not say **which** cap or **how many** I hold; a BLOCKED row will say "cap is 1 per ticker" in a blocker string only if `propose` happened to size that name. On the sector-ceiling question the CLI is right — `run.py:689` says plainly that a ticker has one sector so the sector cap is the ceiling the per-ticker cap can reach — but the page is where I look, the gear exposes only `max_open_positions` (`pcs/config.py:56-58`), and the "open positions" card subtitle reads "max 2 per sector · change it under the gear" for a cap the gear cannot change. |

#### Nice to have

- **The heartbeat is green when it matters least and silent when it matters most.** `_heartbeat`'s red flag requires `sess.is_open` (`pcs/dashboard.py:774`) and `alerts()`'s `mark_stalled` requires `trading_now` (`pcs/health.py:254`). On the page I just rendered, a mark loop last seen **five days ago** prints `marks 5d ago` in `hb-ok` green with no alert. I open this once a day, often before the bell or at a weekend — the two windows in which a dead timer is guaranteed to look healthy.
- **The ladder gives the word "buying power" to the number the code does not mean by it.** The page's `= Free cash / buying power $2,337.76` (`pcs/dashboard.py:823`) is `Ledger.free_cash`; `Ledger.buying_power` (`pcs/ledger.py:272`) is $1,668.76 and is what `run.py:461` calls *available balance*. The ladder itself reads correctly because the ↳ row does the work — but a page-to-CLI reconciliation crosses a name collision on the one figure that gates a trade. Call the ladder row "free cash (broker basis)" and let `buying power` mean one thing.
- **HOLDING is a first-class watchlist signal that the panel forgot twice.** It is absent from the summary pills (`pcs/dashboard.py:1142-1143`) and from the sort order (`pcs/dashboard.py:1178-1179`), so `order.get(sig, 9)` sorts it **last** on the signal column while `watchlist._RANK` (`pcs/watchlist.py:36`) puts it **first** in the default order. The same list reorders itself into two different opinions about the same rank.
- **A stricter mark now feeds a quarantine counter calibrated for a broken chain.** `MARK_FAILED` is in `QUARANTINE_KINDS` (`pcs/learning.py:62`) and the bench fires at 3 faults in 7 days (`pcs/config.py:314-315`). Post-cb6e8a1, one open position with a quiet long leg files one fault per 15-minute mark run — benched inside one morning for 5 days, with the Learning tab asserting "the chain will not price this name reliably" when what actually happened is that a free feed dropped a bid at 09:35, which §38 says is routine. The bench can only subtract a candidate, so nothing unsafe follows; it does mean the self-repair log is about to fill with noise.
- **`run.py learn` does not mislead me, and that is worth recording.** `feature_gaps(journal)` counts rather than describes (`pcs/learning.py:556-562`), `_split` filters `None` before comparing, the "Not learnable yet" block is prefaced with "absence means unmeasured, not no effect" (`pcs/dashboard.py:1312-1316`), and `lessons()` still refuses to conclude anything under 8 closes. I looked for the failure mode the brief points at and it is not there. The only thing I would add: the block counts closed trades only, so it will not tell me that the positions **currently open** also predate the four features — which is the number that says how long until the screen becomes measurable.

#### The alert list, revisited

Still detected, still not delivered. `health.alerts()` implements all five states and `_alerts_panel()` puts them above the cards; `grep -rl "smtp\|webhook\|mailto\|twilio\|slack"` across `deploy/`, `pcs/` and `run.py` returns nothing but a false hit in `marketdata.py`. **This remains the top operational gap**, and finding #1 sharpens it: the banner is now the *only* place the page tells the truth about an unpriceable position, and it is a banner on a page nobody is looking at.

The new refusals belong on that list, but not as five equal peers — two of them, split by whether I could have known:

6. **A fill was refused by a gate the proposal had already cleared** (`CoolingOff`, `InsufficientFunds` at fill). This is #3 above; it is the state where the agent tried, failed, and left a green ticket behind. Push it.
7. **A cooldown is holding names out**, as a standing fact with dates — `Ledger.cooling_off()` already returns exactly `{symbol: clears_on}` and nothing consumes it for display. This one is not urgent enough to push; it is a two-line panel on the Rules tab that would close must-have #5 and finding #4 at once.

The per-ticker cap does not need an alert. It needs to appear on the page at all.

---

## Seat 2 — design · **reported 2026-09-05**

*(Convened 2026-09-01 and terminated on an API rate limit before returning
anything. This is the seat's first actual report. The palette and layout work
recorded under this heading previously was done outside the review; it is kept
below the findings so the two are not confused.)*

**Verdict: the page is well-written and badly ranked — every fact the operator
seat asked for is on it, but the type scale, the alert chrome and the column
order all put the four priorities *below* the numbers that never change, and on
the phone-and-tablet widths this page is actually read at, the exit pill is the
thing most likely to be off-screen.**

Reviewed against the rendered page at 375, 768, 1024 and 1440px, in both themes,
with dichromat simulation (Viénot) and measured contrast. Where a claim is a
measurement it is given as one.

Two checks pass and should be recorded as passing: **the dark palette is
complete** — 34 tokens on `:root`, 34 in `:root[data-theme="dark"]`, no dark-only
definition, no `var()` referencing an undefined token, so `LEARNING.md` §17's
claim holds; and **the readiness checklist is the one place on the page that
gets colour right** — `.chk .m` carries ✓ / ✗ / ! glyphs
(`pcs/dashboard.py:1077`), so it survives with no colour at all. It is the model
the rest of the page should copy.

### Must have

| # | Finding | Why it bites |
|---|---|---|
| 1 | **The alerts panel encodes severity in hue alone, and its chrome is red no matter what it contains.** `.alert.critical` / `.warning` / `.info` differ only in a 3px `border-left-color` and the title colour (`pcs/dashboard.py:219`–`224`). Under deuteranope simulation `--neg #d0342c → #7e7e1c` and `--warn #a35c05 → #777700`: separation 3.6 in linear RGB, i.e. **the same colour**. Dark is worse — `--pos #4ac26b → #aaaa6f` and `--neg #f4726a → #a6a664`, separation 4.3, so green and red are one colour. Separately, `.alerts` hard-codes `border:1px solid var(--negln)` and `.ahead` hard-codes `var(--negbg)`/`var(--neg)` (`pcs/dashboard.py:211`–`215`) with no severity modifier. | Verified in the live DOM: the only alert present is `class="alert warning"`, and it renders inside a fully red-bordered panel under a red header reading `1 THING(S) WORTH KNOWING`. A colour-blind reader given three alerts and a header saying "1 thing(s) need you" **cannot identify which of the three it is** — the count is there and the pointer is not. And every reader is being trained by a red panel that is only ever amber news, which is exactly the failure `LEARNING.md` §29 already recorded once ("it was red, which is precisely how a reader learns to skip the panel that will matter later"). |
| 2 | **Between 701px and ~1090px the positions table clips `P&L` and `status` off the right edge, behind a scroll with no visible affordance.** Measured at 768px: the table's natural width is **1051px inside a 728px box — 323px hidden**, and the columns past the fold are `COLLATERAL`, `COST TO CLOSE`, `P&L`, `STATUS`. `.scroll{overflow-x:auto}` (`pcs/dashboard.py:135`) plus `table{min-width:760px; border-radius:13px; overflow:hidden}` (`:136`) means the clipped edge renders as a *finished, rounded* edge, and macOS/iOS overlay scrollbars are invisible at rest. | This is priority 3 failing structurally. `status` is the column that holds `HELD` / `NEEDS YOU` / `NOT DECIDED` — the states the operator seat said cost the most. At iPad portrait, at any phone in landscape (iPhone 15 Pro Max is 932px), and in a half-width desktop window, the page renders a complete-looking table of eight tidy columns and the pill saying *nobody closed this* is simply not on screen. Nothing tells the reader a column is missing. |
| 3 | **The heartbeat and the row it describes give opposite verdicts on the same number, and the heartbeat can only go red during market hours.** Measured in the DOM: `<span class="hb-ok">marks 5d ago</span>` at the top of the page, sitting above `<div class="agemark stale">marked 5d ago</div>` in the table below it. `_mark_state()` has an unconditional `age > 60*24 → stale` rule (`pcs/dashboard.py:608`); `_heartbeat()` gates its only bad state on `sess.is_open` (`:774`–`776`), so outside RTH no age is ever flagged. `.hb-ok` is `--ink2` and `.hb-bad` is `--neg`+650 (`:209`–`210`) — two tiers where the row-level mark has four. | The whole point of the heartbeat is priority 2. A once-a-day reader on a phone reads in the evening or at the weekend — **precisely the sessions in which the heartbeat is incapable of turning red.** The page currently states, in calm ink, at the top, that the marks are fine, and then states in bold red 1,900px further down that the same marks are stale. The reader who stops after ten seconds gets the wrong one. |
| 4 | **The type scale ranks by how big the number is, not by what needs attention.** 29 distinct size/weight pairs are live on the Positions tab alone: 14 sizes (10 / 10.5 / 11 / 11.5 / 12 / 12.5 / 13 / 13.5 / 14 / 15 / 16 / 16.5 / 18 / 20px) and 9 weights, six of them (600/620/640/650/670/680) inside an 80-unit band on a `ui-sans-serif` stack with no variable axis — most of those render identically, so the scale has more steps than it has distinguishable values. The largest type on the page is `.card .v` at 23px desktop / 18px mobile (`pcs/dashboard.py:131`, `:446`). `.atitle` — the alert headline — is **13px**/650 (`:222`). `.agemark` is 11px (`:227`). `.note`, which carries "due, but the market is closed", is 12px (`:277`). `th` and `.card .k` are 10px at 4.67:1. | **The thing that says "you need to act" is set ten pixels smaller than the thing that says "your balance is unchanged."** On a phone in daylight the eye lands on `$3,668.76` at 18px/620; the "marked 5d ago" that invalidates every figure in that ladder is 11px `--dim`. A scale this crowded cannot rank anything — six weights that render the same are six ways of saying nothing — so hierarchy defaults to whichever cell needed the most room, and the four priorities are all short strings. |

**Fixes, concretely.**

*(1)* Emit the severity as a word and let the container follow its worst member —
in `_alerts_panel()` (`pcs/dashboard.py:736`–`742`):

```python
word = {health.CRITICAL: "NEEDS YOU", "warning": "WATCH", "info": "FYI"}
rows = "".join(
    f'<div class="alert {a.severity}"><div class="atitle">'
    f'<span class="sev">{word.get(a.severity, a.severity.upper())}</span>'
    f'{_e(a.title)}</div><div class="adetail">{_e(a.detail)}</div></div>'
    for a in alerts)
return (f'<div class="alerts{" has-critical" if n_crit else ""}">'
        f'<div class="ahead">{_e(head)}</div>{rows}</div>')
```

```css
/* Chrome follows the worst thing inside, not the panel's own name. */
.alerts{border-color:var(--warnln)}
.alerts.has-critical{border-color:var(--negln)}
.ahead{background:var(--warnbg);color:var(--warn);border-bottom-color:var(--warnln)}
.alerts.has-critical .ahead{background:var(--negbg);color:var(--neg);
border-bottom-color:var(--negln)}
.sev{display:inline-block;margin-right:8px;padding:1px 7px;border-radius:5px;
font-size:10px;font-weight:700;letter-spacing:.07em;vertical-align:2px;
background:var(--chip);color:var(--dim);border:1px solid var(--line)}
.alert.critical .sev{background:var(--negbg);color:var(--neg);border-color:var(--negln)}
.alert.warning .sev{background:var(--warnbg);color:var(--warn);border-color:var(--warnln)}
.alert.critical{border-left-width:5px}   /* width, not only hue */
```

*(2)* The highest-leverage single change on the page: **reorder the columns** in
`_positions_table()` (`pcs/dashboard.py:901`–`922`) to `ticker · status · to
short · spot · expiration · breakeven · P&L · spread · qty · credit ·
collateral · cost to close`. Because the phone card order *is* the column order
(`td::before{content:attr(data-l)}`), one edit fixes both this and the burial in
the first nice-to-have below. Then make the clip visible anyway:

```css
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:13px;
scrollbar-color:var(--dim) var(--chip);
background:
 linear-gradient(90deg,var(--panel) 30%,transparent),
 linear-gradient(90deg,transparent,var(--panel) 70%) 100% 0,
 radial-gradient(farthest-side at 0 50%,rgba(16,24,40,.16),transparent),
 radial-gradient(farthest-side at 100% 50%,rgba(16,24,40,.16),transparent) 100% 0;
background-repeat:no-repeat;
background-size:36px 100%,36px 100%,14px 100%,14px 100%;
background-attachment:local,local,scroll,scroll}
```

*(3)* Give the heartbeat the tiering the rows already have, and a word so hue is
not the only channel — in `_heartbeat()` (`pcs/dashboard.py:774`):

```python
if age is None:
    cls, tail_word = "hb-ok", ""
elif kind == "mark" and sess.is_open and led.open_positions \
        and age > health.MARK_MISSING_AFTER_MIN:
    cls, tail_word = "hb-bad", " · overdue"
elif age > 60 * 24:                      # a day old is stale in any session
    cls, tail_word = "hb-bad", " · stale"
elif age > 60 * 8:
    cls, tail_word = "hb-aging", " · aging"
else:
    cls, tail_word = "hb-ok", ""
```

```css
.hb{font-size:13px}
.hb-aging{color:var(--warn);font-weight:600;font-variant-numeric:tabular-nums}
```

*(4)* Seven steps, three weights, and invert the two that matter:

```css
:root{
--t-xs:12px;    /* provenance: mark age, cell sub-lines, column labels */
--t-sm:13px;    /* secondary prose, notes */
--t-md:14.5px;  /* body and table cells */
--t-lg:16px;    /* ladder amounts */
--t-xl:20px;    /* the one figure a section is about */
--t-alert:17px; /* the alert headline, above every figure on the page */
}
.atitle{font-size:var(--t-alert);font-weight:700}
.agemark{font-size:var(--t-xs);font-weight:600}
.note{font-size:var(--t-sm)}
.card .v{font-size:var(--t-xl);font-weight:600}
th,.card .k{font-size:11px}
/* and snap every 620/640/650/670/680 in the sheet to 600 or 700 --
   on ui-sans-serif they already render as one of the two. */
```

### Nice to have

- **The first open position is 1,875px down the page on a phone — 2.3 viewport heights.** Measured at 375×812: alerts end at 464, then the balance ladder (402px) + the money story (214px) + nine stat cards stacked (701px) = **1,317px of account arithmetic** before "Open positions", and each position card is **650px**, i.e. one whole screen per spread. The ladder, the story and the cards state the same six dollar figures three times over in three formats; two of the three would be enough above the fold. Cheapest lever: move `<div class="cards">` *below* the positions table in `render()` (`pcs/dashboard.py:1486`–`1489`) — the story already says everything the cards say, in sentences.
- **`.note` is one colour for four opposite meanings** (`pcs/dashboard.py:277`, `color:var(--warn)`). "the agent closes this on the next mark" (nothing to do) and "due, but the market is closed — Still open, still moving" (the most expensive state on the page) render in identical 12px amber. Split it: `.note{color:var(--dim)} .note.warn{color:var(--warn)} .note.bad{color:var(--neg);font-weight:600}`, and pick the class in `_exit_pill()` (`:646`–`665`) — `bad` for HELD, NEEDS YOU and NOT DECIDED, plain for the will-close case.
- **`_sign()` has no dead-band** (`pcs/dashboard.py:459`–`465`). The page currently renders `-$0` and `-0%` in red on a position that has not moved, and "down $0.24 (-0.01%)" in red in the money story for fill fees — the same complaint the operator seat filed as a nice-to-have, still live. Fix inside `_sign`: `if abs(v) < 0.005: v = 0.0` before choosing the class, so a rounding artifact cannot render as a loss.
- **No `color-scheme` anywhere in the sheet** (verified: zero occurrences). In dark mode the UA therefore paints all native chrome light — the `<select>` popup, which `LEARNING.md` §32 established is *the only sort affordance that exists on a phone*, opens as a white menu with black text; the number spinners stay light (`-moz-appearance:textfield` only covers Firefox); `.scroll` and `.tabs` scrollbars are light-on-dark. One-line fix: `:root{color-scheme:light}` and `:root[data-theme="dark"]{color-scheme:dark}`. Add `<meta name="theme-color">` in the same pass so iOS Safari's chrome does not stay white above a near-black page.
- **`.gearpop` hard-codes a light-theme shadow** — `box-shadow:0 18px 44px rgba(15,23,42,.20)` (`pcs/dashboard.py:185`). Over `--bg #0b0e14` that is invisible, and its only other separation is `--line` at **1.24:1** against `--panel`. The one overlay on the page reads as painted into it. Use `var(--sh2)`. Same class of thing: `.setf button:hover{color:#fff}` on dark `--accent #5c9bff` is **2.77:1** (`:161`), and `.bar span.low/.mid/.high` carry three light-tuned hexes (`:352`–`354`).
- **`h1` is `color:transparent` with `background-clip:text`** (`pcs/dashboard.py:91`–`93`; computed colour confirmed as `rgba(0,0,0,0)`). In forced-colors / high-contrast the title disappears entirely. Add `@media (forced-colors:active){h1{color:CanvasText;background:none}}` and an `@supports not (background-clip:text)` fallback.
- **Dead and colliding rules.** `.cush.near` and `.cush.tight` are byte-identical (`:234`–`235`). `.s-nofit` and `.s-near` are byte-identical (`:316`–`317`) and `STRETCHED` also maps to `s-near` (`:1099`) — three watchlist signals, one appearance. `--bg2`, `--infobg` and `--infoln` are defined in both themes and used nowhere.
- **Two sub-AA pairs in the light palette,** both on the smallest text: `--pos` on `--posbg` is **3.76:1** (`.tag.ok` at 11px, `.sig.s-ready` at 10px) and `--accent` on `--accbg` is **4.14:1** (`.sig.s-hold` at 10px, the tab count pill). Everything else clears 4.2:1. Darkening `--pos` to ~`#0a7f46` and `--accent` to ~`#2a5fd0` fixes both without changing the palette's character. Worth noting the deeper cause: the six semantic hues were tuned to equal weight, so their relative luminances sit between 0.126 and 0.203 — the palette is deliberately luminance-flat, which is why removing the red-green axis leaves nothing behind. Every semantic must therefore carry a word, a glyph or a sign; hue can only ever be the second cue.
- **Tap targets below the thumb minimum.** `.sortsel` and `.sortdir` measure ~32px tall at ≤700px (`:408`–`409`) — the sort control being, again, the only one that exists on a phone. `.themer` and `.logoutf button` are 34px until the 520px breakpoint lifts them to 36px, so a 768px tablet keeps 34. 44px is the target.
- **`1 THING(S) WORTH KNOWING`** (`pcs/dashboard.py:740`–`741`) is the first line of the highest-priority element on a page that pluralises carefully everywhere else ("2 open spreads", `noun = "spread" if n == 1`). Give it the same treatment.

### The logo at favicon size

**It finds the tab, and it loses the thesis.** Rendered at 16, 24, 32, 64 and
128px side by side. At 16px the 32-unit viewBox scales 0.5×, so
`stroke-width:2.6` becomes a 1.3px pen (`pcs/brand.py:44`–`49`). Three of the
mark's four elements fail at that size: the arrowhead (`MARK_ARROW`, two
5.1-unit legs = 2.55px each) is barely twice the stroke width and fills into the
line's own terminus, reading as a thickened end rather than an arrow; the
pullback (`L12.5 15.5 → L17 19`, a 2.25 × 1.75px excursion) is entirely absorbed
by the stroke and **disappears** — which is the whole point of the mark per
`pcs/brand.py:15`–`17`; and `MARK_BASE` at 65% opacity, 1.4px, three pixels from
the bottom of a tile with a 4.25px corner radius, reads as a hairline artifact
and competes for the little ink available. What survives is "a white diagonal in
a blue rounded square", which is enough to pick out of a tab strip.

Two cheap improvements: drop `MARK_BASE` from the favicon variant only (it costs
contrast and returns nothing below 24px) and thicken the remaining strokes to
~3.2; and give the favicon the header's blue→green gradient, because as it
stands the tab icon is flat `#2f7fe0` while the masthead is a gradient — two
marks for one brand. Separately, there is no `<link rel="apple-touch-icon">`:
iOS ignores an SVG `rel=icon`, so a page like this one — read daily on a phone
behind a login, i.e. the archetypal home-screen bookmark — gets a screenshot of
itself as its tile instead of the mark.

### Palette work done outside the review

Recorded here so it is not mistaken for a council finding. From direct
inspection of the rendered page, before this seat sat:

- The page is **light by default**, with a header toggle for dark persisted per
  browser. It deliberately does not follow `prefers-color-scheme` — see
  `LEARNING.md` §17.
- `.tag` was defined twice — once for the brand tagline, once for the table
  pills — and the pill rules won, wrapping the tagline in a rounded box.
  Renamed to `.brandtag`.
- The tab strip wrapped and orphaned "Rules" onto a centred second row. Now
  `flex-wrap: nowrap` with horizontal scroll and a hidden scrollbar.
- Stat cards carry a semantic accent stripe (risk amber, P&L green/red, neutral
  blue). A flat P&L stays neutral rather than tinting red.

---

## Seat 3 — trust & safety · **reported 2026-09-05**

*(Convened 2026-09-01 and terminated on an API rate limit before returning
anything. This is the seat's first actual report. It is the seat whose findings
would gate a deploy.)*

**Verdict: I would not gate a deploy.** Every invariant in my brief is enforced
in code and pinned by a test; there is no path from a browser, a config file, an
override file, a proposal file or a journal file to arming trading or waiving
the approval gate. What I found is record integrity and page honesty — six
items, three of which should land before this account's closed record is used to
argue for going live.

### The brief

**1. Is the paper-vs-real distinction unmissable on every screen? — Yes,
narrowly.** The line at `dashboard.py:1463` sits above the tab strip, outside
every `<section class="panel">`, so `Paper $3,000 · mode paper` renders on all
six tabs, not just Rules. Two caveats. `account_label` is free operator text
(`config.py:188`) — nothing forces the word "paper" into it, and the only
structural half of that line is `led.mode`, rendered at 13px in `--dim`. And the
money is described in unhedged second person everywhere else: "what you put in",
"your cash today" (`dashboard.py:815-823`), "the ${…} you would pay out"
(`dashboard.py:1431`). The label is present on every screen; it is not weighted
like the most important word on the page.

**2. Is "per-trade human approval is OFF" prominent enough? — No.** It appears
in exactly three places, and two of them are the Rules tab: the `Approval:`
bullet (`dashboard.py:1563`, text built at `1374-1378`) and the deviations panel
(`config.py:357-358` → `dashboard.py:1542`). The only appearance on the default
screen is as a subordinate clause in a section heading — "Pending proposals —
opened automatically on the next run" (`dashboard.py:1491`, `pending_txt` at
`1379`). It is absent from the header line, from the alert panel
(`health.alerts` has no criterion for it), and from History, Go-live, Watchlist
and Learning entirely. The single most consequential setting on the page is one
tab away from four of six screens, and the `dev_flag` link that would carry a
reader there is itself only rendered inside Rules (`dashboard.py:1366`, used at
`1538`).

**3. Does the page overstate what the agent knows? — Mostly no, with one real
exception.** The honest work here is genuine and unusually thorough:
`_mark_state` grades every row by mark age (`dashboard.py:588-612`),
`_exit_pill` distinguishes closed / held / not-decided (`637-665`),
`_cushion_cell` refuses to render an unknown cushion as a wide one (`615-634`),
the watchlist banner says outright that a non-live grade "shows where each name
stands, not what it would fill at" (`1170-1173`), and a refused mark surfaces by
name as a WARNING alert (`health.py:261-267`). The exception is the proposal
queue: `Proposal.created_at` exists (`proposer.py:22`) and is rendered nowhere,
`proposer.load` discards the file's `generated_at` (`proposer.py:113-117`),
`_proposals_table` has no age column (`dashboard.py:968`), and nothing expires a
proposal — so a ticket priced days ago sits in the queue looking current, and
`cmd_approve` will fill it from the stored quote (`run.py:302-312`) with no
staleness check at all. That is a stale quote presented as tradeable.

**4. Is anything exposed pre-authentication? — No, verified in code.**
`auth_request /_auth` is declared at server scope (`nginx-pcs.conf:46`) and only
three locations turn it off: `/50x.html` (line 69, also `internal`), `/login`
(72) and `/logout` (92). `/settings` deliberately does not (lines 86-91) and
authd re-checks the session anyway (`authd.py:333-336`). authd itself serves 404
on every path but those four (`authd.py:372`, `397`), holds no mutable state,
and binds loopback only (`authd.py:416-419`). Both pre-auth pages are
account-free: `login_page` (`authd.py:195-227`) and `error_page` (`230-253`)
contain the brand, a form and static prose. The port-80 block's unauthenticated
`/.well-known/acme-challenge/` (`nginx-pcs.conf:24`) shares the web root with
`index.html` but cannot reach it — nginx normalises `..` before location
matching, so a traversal falls through to the blanket redirect on line 25.

### Must have

| # | Finding | Why it bites |
|---|---|---|
| 1 | Both open positions in `data/ledger.json` carry `approved_by: "viplove (blanket paper approval)"`. `auto_approver()` (`config.py:370-376`) is honoured only on the auto path (`run.py:241`, `299`); `--approver` at `run.py:293` is unvalidated free text, and `_event_line` renders every event field verbatim (`dashboard.py:992`). | The History tab today shows two individual fills stamped with a person's name and a phrase that literally records standing consent. This is precisely the artefact `auto_approver()`'s docstring exists to prevent, and `test_the_auto_approver_is_never_a_persons_name` does not cover the path that produced it. A record that reads as a human approval is worse than no record when nobody looked. |
| 2 | A pending proposal has no age anywhere on the page or in the loaded object (`proposer.py:22`, `113-117`; `dashboard.py:968`), never expires, and `cmd_approve` fills it on both a stored price (`run.py:307` → `Spread(**p.spread)`) and a stored risk verdict (`run.py:284` reads `p.risk_ok`). `open_approved` re-checks the cooldown, balance and per-trade collateral (`paper_broker.py:118-149`) but not `max_open_positions`, the sector cap or the per-ticker cap. | Two silent widenings from one gap. A week-old ticket fills at a week-old quote with no warning on screen; and the concentration caps are evaluated only at the moment the ticket was written. `CoolingOff`'s own docstring (`paper_broker.py:50-55`) gives the exact argument for re-checking at fill time — "a proposal carries the verdict it was given when it was written" — and the count caps do not get it. |
| 3 | Expiry settlement is the one close path outside the honest-mark guard. `mark_positions` closes an expired position against `spot or pos.mark_spot` (`paper_broker.py:264-267`) — outside `fresh`, outside the market-open gate `apply_exits` holds itself to (`paper_broker.py:307-311`) — and writes a definitive reason ("expired worthless (max profit)"). | §38's fix stopped a bad mark from firing a stop; it does not stop a bad spot from booking a settlement. If the snapshot fetch misses the symbol, `spot` is `0.0` and the fallback is a `mark_spot` that may be days old, and that one number decides max profit versus max loss. The resulting `realized_pl` then feeds the go-live win rate and the journal as though it were measured. |
| 4 | `_exit_pill` renders "the agent closes this on the next mark" (`dashboard.py:664-665`) for any row whose mark age is under the stale threshold — but `decide()` is called on every position regardless of `fresh` (`dashboard.py:894`), and a position that refused to price this run stays "fresh"-looking for ~22 minutes (`dashboard.py:610`, `health.py:37`). `run.py:430` already records `stale_symbols`; nothing joins it to the row. | The page states an action the agent will not take. The position is excluded from `fresh`, so `exits.review` skips it (`exits.py:133-135`) and nothing fires until it re-prices. The alert panel says so at the top of the same screen — two parts of one page disagreeing about one position, with the reassuring half attached to the row. The fact needed to fix it is already persisted. |
| 5 | The login page footer says "Read-only: there is no write path through this page" (`authd.py:226`). `POST /settings` changes `max_open_positions`, a risk limit (`authd.py:393`, `320-354`). | The one sentence a person reads *before* authenticating understates what the session they are about to create can do. The gear popover on the dashboard gets this right (`dashboard.py:713-715`); the two pages contradict each other, and the wrong one is the one that runs unauthenticated. |
| 6 | Two go-live criteria can read true off a record that does not mean it. "A loss has actually been taken" is `realized_pl <= 0` (`readiness.py:81`, `115-118`), so a $0.00 fee-only scratch counts as a loss. "A stop or defend has fired" is a substring match on free-text `close_reason` (`readiness.py:83`, `122-124`), and `run.py close --reason` accepts any string (`run.py:577`, default `"manual close"`). | These are the two criteria that exist specifically to prove the downside path has been exercised. Both are satisfiable without it — one by bookkeeping noise, one by typing. Given that the strategy's only real loss on record was a pricing bug rather than the strategy (LEARNING §38), the criteria that are supposed to catch exactly that are the ones with the softest definitions. `learning._result` already draws the honest line at ±$0.50 (`learning.py:167-172`); `readiness` does not use it. |

### Nice to have

- `"modelled mid"` is a hardcoded label on every cost-to-close cell (`dashboard.py:915`). It is neither a mid (`cost_to_close` haircuts 25% toward the natural, `paper_broker.py:235`) nor modelled when the chain was live — and on a never-marked position it labels the fill price. The adjacent age chip corrects it; the label should track `basis`/`source` rather than being a constant.
- `Ledger.cooling_off` **fails open**, not safe, on an unparseable `closed_at` (`ledger.py:363-365`). This is deliberate and documented (LEARNING §40) and the direction is defensible — a re-permitted entry still has to clear every other gate — but the brief asked, so: it is not fail-safe.
- `learning.blocked_symbols` calls `dt.date.fromisoformat(q.until)` with no guard (`learning.py:241-242`), unlike `self_repair`, which guards the equivalent parse (`learning.py:278-281`). A corrupt `journal.json` takes down the whole dashboard render via `dashboard.py:1135`.
- `bootstrap.sh:177-178` passes the dashboard password on argv (`run.py viewer add … --password "$PCS_PASS"`), and `viewer.sh:5` documents the same pattern. `/proc/<pid>/cmdline` is world-readable by default, so the credential is visible to any local user for the life of the process.
- `/etc/pcs` is created `0755` twice (`bootstrap.sh:159`, `171`) and only corrected to `0750` fifty lines later (`bootstrap.sh:211`). Nothing actually leaks — `viewers.save` opens the file `0640` before any bytes reach it (`viewers.py:107`) — but the safety of that window rests entirely on the file mode, and the script does not say so.
- The header renders `led.mode` (`dashboard.py:1463`) while `_exit_pill` branches on `settings.mode` (`dashboard.py:652`). They can disagree; showing the ledger's is the right call, but the divergence is undocumented.
- `settings.max_open_positions` is interpolated into an HTML attribute unescaped (`dashboard.py:690`). Unreachable from the browser (`set_override` int-coerces at `config.py:102-105`), reachable from a hand-edited `settings.json`, which `Settings.load` type-checks not at all (`config.py:393-398`).
- `deviations()` covers only the five strategy knobs plus the two switches (`config.py:176-177`, `347-359`). Turning off `auto_exit`, tightening `stop_loss_credit_multiple`, or zeroing `reentry_cooldown_days` are all silent — the first is caught per-row by `_exit_pill`, the other two are not caught anywhere.
- No CSP header on the 443 block (`nginx-pcs.conf:38-41`). Everything reaching the page is escaped through `_e` (`dashboard.py:455-456`) — ticker, sector, close reason, event fields, provider-derived watchlist reasons, risk warnings, lesson text, alert text and `account_label` all checked — so this is depth, not a gap.

### Invariants verified

- **The agent proposes and never places a live opening order.** ENFORCED — `paper_broker.py:109-112` (`open_approved` raises `ApprovalRequired` on a non-paper ledger) and `paper_broker.py:303-306` (`apply_exits` raises on a non-paper ledger). Pinned by `tests/test_config_overrides.py:127`.
- **Auto-approve is paper-only and no setting can change it.** ENFORCED — `config.py:361-368`. The `and self.mode == "paper"` is inside the negation, so a non-paper mode returns `True` regardless of `auto_approve`.
- **Nothing reachable from a browser may arm trading or waive the approval gate.** ENFORCED at four independent layers — allowlist `config.py:56-58`; `load_overrides` filters to it `config.py:92`; `set_override` re-checks key and bounds `config.py:99-107`; `authd._settings` checks session, origin, then delegates `authd.py:333-350`. Reinforced outside the code: `deploy/pcs-authd.service` runs `ProtectSystem=strict` with `StateDirectory=pcs` and no `ReadWritePaths`, so the web service has no write path to `data/ledger.json` even though it shares a UID with the agent.
- **Auto-approved fills are recorded as `agent (auto-approve, paper)`, never a person's name.** ENFORCED FOR THE AUTO PATH ONLY — `config.py:370-376`, used at `run.py:241` and `run.py:299`. NOT enforced on `run.py:293`, where `--approver` is arbitrary text; the live ledger carries a person's name. See Must-have 1.
- **`pcs/learning.py` may bench a symbol and nothing else.** ENFORCED — `self_repair` (`learning.py:245-298`) writes only `journal.quarantines` and `journal.repairs`; the module contains no assignment to any `Settings` attribute, no reference to `STRATEGY`, and no call to `open_position`/`close_position`. `lessons()` returns text and a `config --set` string it never executes (`learning.py:402-521`).
- **Per-trade human approval is currently OFF, paper only.** CONFIRMED in `data/settings.json` (`auto_approve: true`, `mode: "paper"`, `paper_trading: true`), surfaced via `config.py:357-358` and rendered at `dashboard.py:1374-1379`, `1491`, `1563`. See brief Q2 on prominence.
- **`cost_to_close` may return `None`, and a refused mark can never reach `apply_exits`.** ENFORCED — `paper_broker.py:205`/`229-230` return `None`; `paper_broker.py:273-275` leaves the id out of `fresh`; `exits.review` skips anything not in `fresh` (`exits.py:133-135`); `run.py:406` passes `fresh` through; `run.py:394-395` computes the unpriced set purely to report it and acts on nothing. One exception: the expiry branch at `paper_broker.py:264-267` closes without consulting `fresh` at all. See Must-have 3.
- **Neither the cooldown nor the per-ticker cap can block an exit.** ENFORCED structurally — `cooling_off` is referenced at exactly three call sites (`paper_broker.py:118`, `pipeline.py:175`, `watchlist.py:135`), all on the entry side, and `apply_exits` does not route through `open_approved`. `risk.check` is not called from any exit path.
- **A refusal is always attributable.** ENFORCED — every `OpenBlocked` subclass carries a message naming the gate, the number and the remedy (`paper_broker.py:102-149`), and `risk.check` appends one reason per cap hit naming that cap (`risk.py:75-98`).
- **`Ledger.cooling_off` fails safe.** NOT ENFORCED — it fails **open** on an unparseable timestamp (`ledger.py:363-365`), deliberately and per LEARNING §40. Bounded: it can only re-permit an entry that still has to clear every other gate.
- **The per-ticker cap counts.** ENFORCED — `ledger.py:322-334` returns counts, `risk.py:85-89` compares `>=` against the setting, and `risk.py:71-73` folds in-batch `pending` into the same counters. At proposal time only; see Must-have 2.
- **A not-recorded entry feature is never rendered or learned from as a real zero.** ENFORCED end to end — `ledger.py:86-89` (typed `| None`, defaulting `None`), `paper_broker.py:161-166` (`iv_at_open=spread.iv or None`, so the 0.30 fallback cannot masquerade as a measurement), `learning.py:103-106` (`Outcome` defaults `None`), `learning.py:336-338` (`_split` drops `None` before comparing), `learning.py:321-324` (`_abs` preserves `None` so the filter can call the key). None of the four is rendered on the dashboard.
- **`learning.feature_gaps()` cannot overstate what the record supports.** ENFORCED — `learning.py:556-562` counts rows where the attribute is `None` against `len(rows)`, the true closed count. If anything it *under*states the gap, because `SCRATCH` rows are also dropped from every split (`learning.py:304-308`, `352`) and are not counted as blind. The surrounding page prose states the distinction correctly (`dashboard.py:1312-1316`).
- **Nothing is exposed pre-authentication.** ENFORCED — see brief Q4 for the full path trace.

Test suite green (413 passed) at `13e3a28`.

---

## Status

### The first sitting's six — holding

Confirmed unregressed by the 2026-09-05 re-review. See the table under Seat 1.

### Open after 2026-09-05

**Nothing here gates a deploy.** Trust & safety — the seat whose findings would —
verified every safety invariant as enforced in code, and named the line for each.
What is open is record integrity and page honesty.

Three that should land before this account's closed record is used to argue for
going live, because each one lets a number mean something it did not measure:

1. **The ledger records standing consent as a per-trade approval** (T&S #1).
   `--approver` is unvalidated free text and the open positions carry a person's
   name beside the phrase "blanket paper approval". `auto_approver()` exists to
   prevent exactly this artefact and does not cover the path that produced it.
2. **Expiry settlement is outside the honest-mark guard** (T&S #3).
   `cb6e8a1` stopped a bad mark firing a stop; a bad or stale spot can still book
   a settlement, and that one number decides max profit versus max loss before
   feeding the go-live win rate.
3. **Two go-live criteria are softer than they read** (T&S #6). "A loss has been
   taken" counts a fee-only scratch; "a stop has fired" is a substring match on
   free text. These are the two criteria that exist to prove the downside path
   was exercised.

Then the convergent pair, which are one fix each and both about the page telling
the truth about what the agent did:

4. **Join the refused-mark fact to the row** (Operator #1, T&S #4).
5. **Let the heartbeat go red outside RTH** (Operator nice-to-have, Design #3).

The rest sit on their seats' lists: the new rules being invisible on the page and
absent from `doctor` (Operator #4, #5), the alert panel's hue-only severity and
the clipped `status` column (Design #1, #2), the type scale (Design #4).

### Still true from the first sitting

**The alert list is detected, not delivered.** `health.alerts()` implements all
five states the operator wanted pushed and `_alerts_panel()` renders them above
the cards — which makes them impossible to miss *on the page*, and the page is
still a pull. The re-review sharpens this: the banner is now the only place the
page tells the truth about an unpriceable position, and it is a banner on a page
nobody is looking at. A real push needs a delivery channel and a place to put a
secret — a decision, not an implementation detail.

The operator seat added two states to that list (a fill refused by a gate the
proposal had already cleared; a cooldown holding names out, as a standing fact
with dates) and was explicit that the per-ticker cap does not need an alert — it
needs to appear on the page at all.
