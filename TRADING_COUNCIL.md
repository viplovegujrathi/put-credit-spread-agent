# Trading council

A panel of five analyst seats that reads what the agent has actually done, and
says what it could have done better and why it did not take the names it
skipped. It is a different panel from [COUNCIL.md](COUNCIL.md), which reviews
the software (design, operator, trust and safety). This one reviews the
**trades**.

**Sitting 1 · 2026-10-23, after the close · reviewed at `0e3dc4c`.**

Everything here is analysis of a **paper** account. It is not investment advice,
and it changes nothing by itself: every recommendation below is a proposal that
becomes a commit only when the owner says so, and `LEARNING.md` records what was
learned after it is decided, never what was permitted.

**The seats are lenses, not five reviewers.** One analyst worked through all
five questions in one session. The split keeps the questions apart; it does not
give independent confirmation, and no finding here should be read as if five
people had agreed on it.

---

## 1. Charter

| Seat | Owns the question | Reads | May not claim |
|---|---|---|---|
| **Chair (evidence)** | Would we still believe this if the same trades had come in a different order? | everything; owns the grades in §2 | anything graded below B as a reason to change a parameter |
| **Structure** | expiry, strike distance, width, delta, IV, ranking | `optimizer.py`, `chains.py`, the proposal tickets | that a split of 5 against 16 trades is a rule |
| **Execution** | fills, quote quality, when stops and take-profits fire | `mark.log`, `paper_broker.py`, `exits.py` | that paper fills are what a live account would get |
| **Risk** | capital, caps, concentration, size of a loss | `risk.py`, ledger collateral and sectors | that a cap which never bound is a cap that works |
| **Opportunity** | what the screen and the funnel skip, and why | `propose.log` skip lists, `watchlist.json` | that a name the agent skipped would have made money |

The Chair sits in every discussion. A seat that wants to propose a change needs
the Chair to attach a grade first.

---

## 2. Rules of evidence

**Grades.** Every finding in this file carries one.

| Grade | Means | Acted on? |
|---|---|---|
| **A** | An accounting fact. Recomputable from the ledger or the logs; no inference in it. | Yes |
| **B** | A mechanism visible in the code, or a pattern that repeats across many sittings and names. | Yes, with a test |
| **C** | A hypothesis. Small sample, one market regime, or found after many looks. | Only as a pre-registered forward test |
| **D** | An anecdote, or a number that falls out of a search over many splits. | No |

**Rules.**

1. **Count sittings and names, not trades.** The agent proposes the same name on
   consecutive days; 109 spreads are not 109 independent draws. Intervals here
   are Wilson for proportions and a bootstrap that resamples whole sittings.
2. **Every split is a hypothesis.** With 28 closed trades and a dozen candidate
   splits, the best-looking one is expected to look good by chance. A split that
   has no mechanism stays at D, whatever its p-value says.
3. **Hold-to-expiry is not the strategy.** The agent takes profit at 50% of
   credit and stops at 2×. "What the spread would have done if held" measures the
   *entry*; it says nothing about the exits unless the exit is replayed on the
   recorded marks (§4.6).
4. **One window.** 39 sittings between 2026-09-01 and 2026-10-23. Nothing here
   says how the strategy behaves in a different market.
5. **Rule changes split the record.** Count trades under a rule by
   `opened_at >= the change date`, using `SELECTION_CHANGES` in
   `pcs/expectancy.py`. The three live rows are 2026-10-19 (`d488761`, farther
   strike and POP-first ranking), 2026-10-23 (`e5a774e`, earnings no longer
   excluded) and 2026-10-26 (`4538794`, the expiry fallback). From 10-26 all
   three apply, so they cannot be told apart afterwards, except that the
   fallback's trades are identifiable from `propose.log`.
6. **Pre-register the test before the data arrives.** §8 says what is looked at,
   when, and what would change the council's mind.

---

## 3. Data and method

| Source | What was taken | Where it lives on the box |
|---|---|---|
| Ledger | 32 positions, 28 closed and 4 open | `/opt/pcs/data/ledger.json` |
| `propose.log` | 40 headers; the 09-01 pre-market run is excluded, leaving **39 live sittings**. Shortlist, target expiry, qualifiers, 233 proposal tickets, 3,145 skip lines, fills. | `/opt/pcs/logs/propose.log` |
| `mark.log` | 1,014 mark runs, 26 a day, from 13:35 UTC | `/opt/pcs/logs/mark.log` |
| Prices | daily history from yfinance, to settle each expiry | fetched at analysis time |

Read the box without writing to it:

```bash
ssh pcs 'sudo -n -u pcs cat /opt/pcs/data/ledger.json'
```

```bash
ssh pcs 'sudo -n -u pcs cat /opt/pcs/logs/propose.log'
```

Do **not** run `./run.py learn` for this work: it runs self-repair and writes
the journal.

**Definitions.**

- *Pool.* Every unique `(symbol, expiry, short, long)` the optimizer sized into
  a ticket, first sighting kept, marked "opened" if any sitting opened it.
  **109** have an expiry on or before 10-22 and so have settled.
- *Hold value.* Dollars at the expiry-day close: sizing mid credit minus the
  intrinsic value of the spread, capped at the width. "At natural" uses the
  natural credit instead.
- *Exit replay.* Walk each closed position's recorded live marks in order and
  apply a rule to them. The closing mark is missing from the OPEN table, so the
  recorded close debit is appended as the last point. The replay of the actual
  rule gives **−$100.71** against the ledger's **−$101.63**, which is the check
  that the method reproduces the record.
- *Hold-to-expiry for a closed position* is the ledger's `shadow_pl`, filled
  only after the expiry. Ten closed positions have none yet (AVGO, UBER, APP,
  ORCL, AVGO, HD, HD, LEN, ADBE, DAL).

**The scripts that did this are not in the repo.** They sat in a session
scratchpad. Promoting them to `tools/council/` with tests is the first thing to
do before the next sitting (§8).

---

## 4. Sitting 1 findings

### 4.1 The record (grade A)

| | |
|---|---|
| Closed | 28: **18 wins, 10 losses**, realized **−$101.63** |
| Average win / loss | +$124 / −$232, so break-even needs about **65%** of trades to win; the book delivered **64.3%** |
| Open | LULU 95/90, C 120/115, UAL 95/90, AMD 415/405. At the 15:50 ET mark they were −$7, −$79, −$75 and −$3. |
| Account | net liquidation **$2,734.53**, −8.85% on $3,000 |
| Stops | C is at 1.78× its credit and UAL at 1.71×; the stop is at 2× |

The ten losses are written up in `LEARNING.md` §61. This sitting does not redo
them; it adds what the record cannot show on its own.

One thing the totals hide: **APP's single stop was −$666**, which is 29% of all
loss dollars and 6.5 times the whole net loss. Without it the other 27 trades
are +$565. The stop is 2× the credit, so a spread that collects a large credit
carries a large stop loss; APP collected $539 on $961 of collateral, the largest
ticket the agent has opened.

### 4.2 Why so many names were skipped (grade A for the counts, B for the cause)

3,145 skip lines across 39 sittings. Each is a name that passed the stock screen
and then produced no spread. First matching reason, in this order:

| Reason | Lines | Share |
|---|---|---|
| Open interest under 25 on a leg | 1,468 | **46.7%** |
| No listed expiration in the 28–38 day window | 1,015 | **32.3%** |
| Wide or missing bid/ask on a leg | 398 | 12.7% |
| Earnings blockout (rule since removed) | 190 | 6.0% |
| Credit under the $100 floor, no strike in range, empty chain | 74 | 2.3% |

343 different names reached a shortlist. **76 ever produced a ticket. 267
never did.** Almost none of this is a judgement about the stock.

**The cause is the expiry choice (B).** `chains.pick_expiration` takes the
listed expiry nearest 32 days out, inside 28–38, with a 3-point penalty for a
non-Friday. It has no idea which expiry is liquid, and sizing never falls back to
another one. The log says weeklies are thin:

| Target expiry on the day | Sittings | Shortlisted names | Produced a spread |
|---|---|---|---|
| A monthly (third Friday) | 10 | 846 | 206 (**24.3%**) |
| Any other expiry | 28 | 2,532 | 217 (**8.6%**) |

(38 of the 39 sittings printed a target.)

**The 10-23 check (B, one day).** The 58 shortlisted names were sized again with
the repo's own optimizer on two expiries. On the 11-27 weekly the agent used,
only 31 names had any chain and **7** produced a spread. On the 11-20 monthly,
all 58 had a chain and **20** produced one. Thirteen names qualify only on
11-20: BA, BX, CLX, COIN, DAL, DECK, EIX, HPE, KKR, RTX, TPR, UBER, VST. Seven
qualify on both and none only on 11-27. The two expiries are 28 and 35 days out,
so the weekly won by one day on the "nearest to 32" test. 11-27 is also the
Friday after US Thanksgiving, which is plausibly thin; that part is a guess. The
re-sizing ran on after-close quotes, so the exact counts could move by a name or
two at the open (the live run that morning found 8).

### 4.3 The pool: what the skipped spreads would have done (B for "no edge shown", C for anything finer)

Over the 109 settled spreads, held to expiry, at the sizing mid credit:

| | n | Win | Mean P/L | Sum |
|---|---|---|---|---|
| All | 109 | **69.7%** (Wilson 61–78%) | **−$12.6** | −$1,376 |
| The agent opened | 18 | 66.7% (44–84%) | −$11.7 | −$210 |
| The agent did not open | 91 | 70.3% (60–79%) | −$12.8 | −$1,166 |

At the natural credit the pool's mean is **−$67.6** a trade.

- **The break-even win rate for these spreads is 74.8% at mid.** The pool won
  69.7%. The tickets' own printed POP averaged **70.3%**, so it was calibrated:
  the market's price of risk was about right and no premium over it shows.
- **What the agent skipped was no better than what it took.** Splitting the
  skips by why they were blocked gives groups of 1 to 44 spreads with means from
  −$712 to +$364. None of that is readable and it is not used.
- The 109 cover only expiries 10-02, 10-09 and 10-16, which is sittings up to
  mid-September, and they cluster by name. Read the table as "more volume was
  not obviously worth anything in this window", not as a measure of the strategy.

So the honest answer to "what can be done to pick the skipped positions" is in
two halves. **Making more of them eligible** is a real, mechanical fix (§5, R1).
**Taking more of them** is not supported: the pool gives no reason to think
volume adds profit. Filter quality is the lever, and the data cannot yet say
which filter.

### 4.4 The book is limited by capital, not by opportunity (grade A)

- 182 of the 233 tickets (**78%**) were blocked by the $2,400 collateral cap or
  the balance floor. The 8-position cap never binds; the most open at once was 6
  and the median at a sitting is 4.
- On 10-23, after AMD opened, $2,041 was committed and the cap left $359. None
  of the seven tickets behind AMD could open. MRVL, next in line, also missed the
  balance test by $12 ($869 needed, $857 free).
- With spreads about $850 each, the cap holds two or three positions.

### 4.5 What `d488761` did to the shape of a trade (grade A for the numbers, C for what they mean)

Tickets before the 10-19 rule against tickets from 10-19 on (28 tickets over
5 sittings), one row per name per day:

| | Before | Since |
|---|---|---|
| Mean collateral | $640 | **$840** |
| $10-wide share | 65% | **89%** |
| Mean credit | $210 | **$130** |
| Return on collateral | 35% | **16%** |
| Cushion | 4.9% | **11.9%** |
| IV of the underlying | 40% | **49%** |
| Printed POP | 70% | **83%** |

The rule did what it was written to do: strikes are farther out. The side
effects are the part to watch.

- **POP sits below break-even, and it did before too.** Before the rule the
  settled pool needed 75% and printed 70%. Since it, a $130 credit on about $970
  of width needs about 86% and the tickets print about 83%. The gap is the same
  size, and at the printed POP the expected value at expiry is negative in both
  (about −$40 a trade before, −$36 since). So the rule did not create this. It
  does mean the strategy's result depends on the exits and on fill quality, not
  on the entry odds (grade B on the arithmetic).
- **Ranking by POP favours high-IV names.** The top of the 10-23 list was AMD at
  63% IV, MRVL 65%, COIN 69%, RDDT 74%. In the old record the high-IV names did
  worst (`LEARNING.md` §61: IV of 55% or more, 2 wins in 5 for −$827, against
  11 in 16 for +$452).
- **The old pool cannot judge the new rule.** Different strikes mean different
  spreads. Replaying the old pool with POP-first ordering gave the top two names
  each sitting a **50% win rate and −$223 a trade** against the agent's own
  ROC-first order at 69% and +$88 (13 sittings, 26 picks, held to expiry). Ten
  orderings were tried, so treat that as a reason to look, not a result (C).

### 4.6 Stops and take-profits (grade C, a hypothesis and not a finding)

Replaying the recorded marks on the **18 closed trades whose expiry has
settled**, so that every variant has a defined answer:

| Rule | Sum | vs actual |
|---|---|---|
| **Actual** (take profit 50%, stop 2×, backstop 50% of max loss) | +$399 | — |
| Take profit 30% / 40% / 60% / 75% | −$170 / +$128 / +$71 / +$261 | worse in all four |
| Stop at 1.5× | +$515 | +$116 |
| Stop at 2.5× or 3× | +$778 | +$379 |
| No 2× stop | +$778 | +$379 |
| No stops at all | +$827 | +$428 |
| Hold to expiry, no exits | +$689 | +$290 |

- **No take-profit level beat 50%.** The differences are a handful of trades and
  the sequence is not monotonic (60% is worse than 75%), which is what noise
  looks like. No change suggested.
- **On these 18 the stops cost money.** Six of the 10 stops are in this set. Four
  more stops have no hold value yet (APP, ORCL, AVGO, DAL). APP is the largest
  loss in the book; if it finishes below its short strike on 10-30, "stops cost
  money" reverses. Do not read the table before then.
- **Six of the 10 stops fired at the first two marks of the day**, 09:35 or 09:50
  ET. Those two slots are 7.7% of all mark runs. Some of that is the overnight
  gap, which lands at the open by nature. What separates a gap from a bad quote
  is the stock's move:

| Stop at the open | Stock vs entry | Hold value |
|---|---|---|
| GOOGL, 09-02 | −0.4% | **+$116** (stop booked −$209) |
| DAL, 10-22 | −0.5% | unknown until 11-20 |
| RDDT, 09-28 | −7.2% | +$155 (stop booked −$156) |
| APP, ORCL, AVGO | −12.7%, −4.9%, −5.8% | unknown |

  Two stops fired on a stock that had barely moved. That is the whole evidence
  for a quote-noise problem at the open, and only one of the two has settled.
- A "no stop before 10:00 ET" rule is +$636 on 24 comparable trades, and **all
  of it is GOOGL and RDDT** (+$325 and +$311). Requiring two consecutive marks
  over the stop is +$428 on the same 24, but it rests on six trades that move in
  both directions: it gains $325, $311 and $264 on GOOGL and the two RDDTs, and
  loses $233 on BA and $276 on STX, where the stop was right.

### 4.7 Paper fills are better than live fills would be (grade A for the arithmetic)

Across the 32 positions the mean paper fill was **$209** of credit against a
mean mid of **$216** and a mean natural of **$163**. The median fill sat 88% of
the way from natural to mid, and only 6% of fills beat the mid.

If live fills had landed at the natural, entry credit alone would have been
**$46 lower a trade**, about $1,300 across the 28 closed trades against a
realized result of −$102. The paper P&L is an upper bound on a live result. How
far below is unknown: there is no live fill data.

### 4.8 Where the money went, by sector (grade D, shown so it is not re-derived)

| Sector | Positions | Realized |
|---|---|---|
| Information Technology | 12 (11 closed) | +$434 |
| Communication Services | 7 | **−$899** (APP −$666) |
| Consumer Discretionary | 4 (3 closed) | +$289 |
| Utilities | 2 | +$232 |
| Industrials | 5 (4 closed) | +$14 |
| Consumer Staples | 1 | −$170 |
| Financials | 1 (open) | — |

The screen picks beaten-down names near the 50-day average, which is mostly
technology (12 of 32). Nothing here is large enough to act on. GOOG and GOOGL
were open together once (09-09); that hole is already closed (`universe.py`
treats share classes as one issuer).

---

## 5. Recommendations

Ordered by how much the evidence supports acting. R1 is built and on by default
(`expiry_fallback`); nothing else is applied.

| # | Proposal | Grade | Why | What it costs |
|---|---|---|---|---|
| **R1** | **Make expiry selection liquidity-aware.** Keep `pick_expiration` as the first choice. If the sized chain yields quotes but no spread, try the other expirations inside 28–38, nearest the target first, at most two, before skipping the name. No entry rule changes. **Built 2026-10-23 as `Settings.expiry_fallback`; on by default from the first sitting after it was deployed with that default.** | **B** (mechanism); the *value* of the extra names is C | The 1,866 skip lines that are open interest or quotes (59% of all skips) are what it can reach, and only on sittings whose window holds two Fridays: 32 of 39, holding 83% of those lines. On 10-23 the monthly qualified 20 names against 7. It does **not** reach the 32% that list nothing in the window; that needs a wider `dte_window`, which is a strategy decision | Up to two more chain fetches per name that fails; it is a selection change, so it has its own row in `SELECTION_CHANGES` and restarts the History tab's since-change panel |
| **R2** | **Do nothing on stops yet. Wait for the four unknowns.** APP and ORCL expire 10-30, AVGO 11-13 and DAL 11-20. The existing `shadow_pl` fills them in; no new code. Re-run the replay on all ten stops then. | **C** | The "no stop before 10:00" gain is two trades, and the four unresolved stops include the biggest loss | Nothing |
| **R3** | **Pre-register the judgement on `d488761`.** First look at 10 trades opened on or after 2026-10-19; judge at 20. Watch IV, the stop rate, and credit capture against the record above. Stage a *shadow* filter (IV at or below about 50%, or a capital-aware ranking) that is logged and not applied. | **C** | The ranking now favours the group that did worst in the old record | Nothing until the review date |
| **R4** | **Leave the cap and the credit floor alone for now.** If the owner wants more positions, the cap, the $100 floor and the width trade against each other, and changing any of them while `d488761` is under test confounds it. | **C** | $850 spreads leave room for two or three | A decision for the owner; no seat has a view |
| **R5** | **Report paper P&L next to a natural-fill P&L.** `expectancy` already computes the break-even win rate; add the same record re-priced at the natural. | **A** on the gap, **B** as a fix | $46 a trade is large next to a −$102 total | A small change and tests |
| **R6** | **Do not add volume as a goal.** The skipped spreads are not shown to be better than the taken ones. | **B** | §4.3 | Nothing |

**What would change the council's mind**

- R1 loses its value if the extra names qualify on thin monthlies and then fill
  worse than the existing ones. Check fills against mid on names added by R1.
  It is built and on by default (`expiry_fallback`), and it reaches only the open
  interest and quote-quality skips on days when the window holds two Fridays.
  The names that list nothing inside 28-38 days stay out of reach until
  `dte_window` changes, which is a strategy decision and not a setting.
- R2 turns into a recommendation if at least five of the ten stops, including
  the four now unknown, finish better held *and* the improved ones are
  concentrated in the first two marks of the day.
- R3 turns into a change if, at 20 trades under the rule, the stop rate or the
  loss per stop is clearly worse than the 28-trade record.

---

## 6. The trades

Every position, with the entry's own numbers and what holding it would have
done. Δ is the absolute short delta. Delta and IV were not recorded on the first
seven. Hold values need the expiry to have passed.

| # | Name | Opened | Strikes | Exp | Credit | Collat | Cushion | Δ | IV | Result | Days | Hold |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | MRVL | 09-01 | 200/190 | 10-02 | $416 | $584 | 3.3% | — | — | **+$229** TP | 7 | +$416 |
| 2 | META | 09-01 | 555/545 | 10-02 | $367 | $633 | 3.4% | — | — | **+$226** TP | 2 | +$367 |
| 3 | QCOM | 09-01 | 155/150 | 10-02 | $147 | $353 | 5.8% | — | — | **+$78** TP | 2 | +$147 |
| 4 | GOOGL | 09-01 | 325/320 | 10-02 | $116 | $384 | 3.5% | — | — | **−$209** stop | 1 | **+$116** |
| 5 | VST | 09-02 | 135/125 | 10-02 | $302 | $698 | 4.8% | — | — | **+$155** TP | 1 | +$302 |
| 6 | CSCO | 09-03 | 104/100 | 10-02 | $99 | $301 | 3.8% | — | — | **+$50** TP | 8 | +$99 |
| 7 | RDDT | 09-03 | 150/140 | 10-02 | $223 | $777 | 4.8% | — | — | **−$255** stop | 5 | **+$9** |
| 8 | PANW | 09-08 | 310/300 | 10-09 | $324 | $676 | 5.5% | .31 | 48% | **+$188** TP | 6 | +$324 |
| 9 | CLX | 09-08 | 90/85 | 10-16 | $158 | $342 | 3.6% | .34 | 36% | **−$170** stop | 8 | −$133 |
| 10 | GOOGL | 09-09 | 320/315 | 10-09 | $157 | $343 | 3.3% | .32 | 30% | **+$85** TP | 5 | +$157 |
| 11 | GOOG | 09-09 | 315/310 | 10-09 | $139 | $361 | 4.0% | .29 | 30% | **+$76** TP | 5 | +$139 |
| 12 | BA | 09-10 | 200/195 | 10-09 | $130 | $370 | 3.3% | .32 | 31% | **−$137** stop | 6 | −$370 |
| 13 | GLW | 09-14 | 145/140 | 10-16 | $194 | $306 | 5.0% | .35 | 61% | **+$99** TP | 15 | **−$306** |
| 14 | RDDT | 09-14 | 150/145 | 10-16 | $155 | $345 | 5.4% | .33 | 55% | **−$156** stop | 14 | **+$155** |
| 15 | VST | 09-14 | 135/130 | 10-16 | $126 | $374 | 5.9% | .29 | 45% | **+$77** TP | 21 | +$126 |
| 16 | STX | 09-15 | 750/740 | 10-16 | $420 | $580 | 3.8% | .38 | 65% | **−$304** stop | 1 | −$580 |
| 17 | GEV | 09-17 | 890/880 | 10-16 | $391 | $609 | 5.2% | .31 | 45% | **+$197** TP | 11 | +$391 |
| 18 | AKAM | 09-17 | 105/95 | 10-16 | $330 | $670 | 4.0% | .36 | 58% | **+$171** TP | 4 | **−$670** |
| 19 | AVGO | 09-22 | 350/345 | 10-23 | $181 | $319 | 3.3% | .34 | 36% | **+$113** TP | 14 | pending |
| 20 | UBER | 09-23 | 67/63 | 10-23 | $107 | $293 | 3.3% | .34 | 35% | **+$53** TP | 15 | pending |
| 21 | APP | 09-29 | 295/280 | 10-30 | $539 | $961 | 5.5% | .33 | 59% | **−$666** stop | 3 | pending |
| 22 | ORCL | 09-30 | 133/130 | 10-30 | $137 | $163 | 3.1% | .38 | 56% | **−$127** stop | 19 | pending |
| 23 | AVGO | 10-12 | 345/340 | 11-13 | $151 | $349 | 3.4% | .33 | 34% | **−$201** stop | 3 | pending |
| 24 | HD | 10-13 | 280/270 | 11-13 | $234 | $766 | 4.1% | .29 | 30% | **+$119** TP | 2 | pending |
| 25 | HD | 10-14 | 280/270 | 11-13 | $179 | $821 | 3.3% | .29 | 26% | **+$91** TP | 1 | pending |
| 26 | LEN | 10-16 | 75/70 | 11-20 | $140 | $360 | 4.4% | .34 | 45% | **+$79** TP | 6 | pending |
| 27 | LULU | 10-16 | 95/90 | 11-20 | $138 | $362 | 4.6% | .32 | 40% | open (−$7) | — | — |
| 28 | ADBE | 10-16 | 240/230 | 11-20 | $272 | $728 | 3.6% | .34 | 36% | **+$138** TP | 6 | pending |
| 29 | C | 10-16 | 120/115 | 11-20 | $101 | $399 | 4.5% | .27 | 28% | open (−$79) | — | — |
| 30 | DAL | 10-16 | 75/70 | 11-20 | $99 | $401 | 4.3% | .30 | 33% | **−$99** stop | 6 | pending |
| 31 | UAL | 10-22 | 95/90 | 11-20 | $106 | $394 | 8.2% | .24 | 51% | open (−$75) | — | — |
| 32 | AMD | 10-23 | 415/405 | 11-27 | $114 | $886 | 19.7% | .11 | 63% | open (−$3) | — | — |

The last two rows are the only ones opened under the farther-strike rule: UAL
(10-22) and AMD (10-23, which also has the earnings change). Every earlier
position sat 3.1–5.9% out, and where delta was recorded, at 0.27–0.38.

How to read the Hold column against the Result column. Acting beat holding on
AKAM (+$171 against −$670), GLW (+$99 against −$306), STX (stop −$304 against
−$580) and BA (stop −$137 against −$370). Holding beat acting on GOOGL's stop
(−$209 against +$116), the second RDDT (−$156 against +$155) and the first
RDDT (−$255 against +$9). On every take-profit that held a winner, acting
gave up part of the credit by design: MRVL took $229 of a possible $416.

---

## 7. What the council could not answer

- **Whether skipping earnings names helped or hurt.** 190 skip lines (6%) were
  earnings blocks, and the rule was removed on 10-23. No trade has yet been
  opened that would have been blocked. There is no evidence either way.
- **Whether the farther-strike rule works.** Two trades, both open.
- **What live fills would look like.** No live fills exist.
- **Whether the settlement fix is right.** The real-expiry fix has never run on a
  real expiry. The first test is 11-20.
- **Anything outside this window.** All 32 trades were opened between 09-01
  and 10-23.

---

## 8. How to re-convene

**Standing checkpoints.**

| When | Look at | Decides |
|---|---|---|
| After 10-30 and 11-13 | APP, ORCL and AVGO now have hold values. Re-run the exit replay. | R2 |
| After 11-20 | DAL has a hold value. First real-expiry settlements. Every position has a hold value or is open. | R2; the settlement fix |
| At 10 trades opened on or after 10-19 | Stop rate, credit capture, IV against the record. First look only. | R3 |
| At 20 trades opened on or after 10-19 | The same, judged against §4.1. | R3 |

The pace is uncertain. The five sittings since the rule opened two trades (0.4 a
sitting); with room for two or three $850 positions and a typical hold of about
six days it could be 0.5. At those rates ten trades is around the end of
November and twenty is between the third week of December and the start of
January. `LEARNING.md` §61 put twenty at "around the start of December", which
counted calendar days; this is the later date.

Trades opened from the first sitting under `expiry_fallback` come from two
changes at once: the farther strike and the choice of a second expiry. The
two checkpoints on 10-19 above still count them, but a reading of them has to
say which trades sat on an expiry the fallback chose (the `expiry fallback on:`
lines in `propose.log`) and compare the two groups before crediting either rule.

**Steps.**

1. Pull the ledger and both logs (§3). Do not run `./run.py learn`.
2. Rebuild the sitting-by-sitting table from `propose.log` and the settled
   pool from the tickets, with expiry-day closes from yfinance.
3. Re-run the exit replay on the marks; check it reproduces the ledger within
   a dollar or two before reading any variant.
4. Re-grade every finding in §4 and §5. A finding that was C and has not
   gained a sample stays C.
5. Append the sitting as the next numbered section after this one (§9, then
   §10), with its date and the commit reviewed. Do not rewrite an earlier
   sitting's findings; record that they changed and why.

**Before sitting 2:** promote the scratchpad scripts into `tools/council/` with
tests (the log parsers, the pool builder, the replay), so steps 2 and 3 are one
command.
