# What building this agent actually taught us

Notes taken while turning the strategy document into running code, from
2026-08-31 onward. Every claim here was observed on live data, not assumed.

**Read this file instead of re-reading the tree.** The orientation section below
is kept current on purpose so a new session can act without opening every
module. Update it in the same commit as the code it describes.

---

## 0. Orientation — the whole system on one page

### Module map

| file | what it owns | do not put here |
|---|---|---|
| `pcs/config.py` | `STRATEGY` (frozen, skill-fixed, asserted at import) and `Settings` (user-settable, persisted to `data/settings.json`) | anything derived at runtime |
| `pcs/universe.py` | S&P 500 constituents + GICS sectors, Wikipedia with a CSV fallback | price data |
| `pcs/marketdata.py` | bulk price history, 50dma / 52w-high, snapshot cache | option chains |
| `pcs/chains.py` | `PutQuote` / `PutChain`, three providers (yfinance / robinhood / model), `pick_expiration` | sizing rules |
| `pcs/screener.py` | the 50dma buckets and the earnings tri-state | anything about options |
| `pcs/optimizer.py` | per-trade sizing: the `(short, long)` search, all §1.4 caps | portfolio-level caps |
| `pcs/risk.py` | portfolio caps across a batch, incl. the balance floor | per-trade caps |
| `pcs/session.py` | clock, holidays, quote-quality grade, the opening-range gate | anything that mutates |
| `pcs/exits.py` | §1.7 exit **decisions** (take profit / stop / defend) — pure | ledger mutation |
| `pcs/watchlist.py` | what is tracked and why it has not been taken — **observation only** | anything that opens; it must not import `paper_broker` |
| `pcs/paper_broker.py` | the three open gates, simulated fills, marking, exit **execution** | exit policy |
| `pcs/ledger.py` | cash, positions, append-only events, all account arithmetic | any policy |
| `pcs/pipeline.py` | wiring: screen → shortlist → size → earnings → propose | new rules |
| `pcs/proposer.py` / `pcs/dashboard.py` | proposal records + tickets; the HTML view | logic of any kind |

### The gates, and where each one lives

Everything that can refuse is enforced in code, not in instructions:

0. **Master switch** — `open_approved` raises `TradingDisabled` when
   `Settings.paper_trading` is off. Entries only; exits keep running.
1. **Approval to open** — `paper_broker.open_approved` raises
   `ApprovalRequired` with no approver, and refuses outright on a live ledger.
   Whether a *human* must be that approver is `Settings.require_approval()`,
   which returns True unconditionally when `mode != "paper"` — auto-approve is
   a paper-only convenience and no setting can lift the live lock. When a human
   does approve, it is per trade and given in chat: never stored, never implied
   by a previous approval, and never recorded in this file. Auto-approved fills
   are recorded as `agent (auto-approve, paper)`, never as a person.
2. **Opening range** — `open_approved` raises `MarketNotReady` for the first
   `Settings.opening_settle_minutes` (30) after the bell. Paper included.
3. **Available balance** — `open_approved` raises `InsufficientFunds` when the
   *filled* collateral plus fees exceeds `Ledger.buying_power`, and again if the
   filled collateral breaches the $1,000 per-trade cap.
4. **Portfolio caps** — `risk.check`, batch-aware via `pending`.
5. **Earnings** — `pipeline.apply_earnings`; an unknown date blocks.

Exits are deliberately **not** gated: closing only ever reduces risk. See §13.

### Invariants worth not breaking

- `Spread.collateral` is **per contract**; every portfolio cap is a total.
  Multiply by `contracts` or a 2-lot gets checked as a 1-lot.
- `buying_power = cash − capital_at_risk`, never `cash − collateral_held`. See §12.
- A paper fill is never better than the credit the ticket was sized on.
- Exit decisions are only ever taken off a mark that re-priced this run.
- `max_credit_per_trade` is `None` in `STRATEGY` on purpose. An *account* may
  set a ceiling; the mandate must not have one.
- Read the resolved rules through `Settings.strategy()`, never `STRATEGY`
  directly, anywhere a user override should apply. `STRATEGY` is the baseline
  `deviations()` compares against.
- Every refusal to open subclasses `paper_broker.OpenBlocked`, so a batch can
  catch one exception and still print the specific reason.

### Commands

```
./run.py screen                 # full index, ~4m40s, writes data/last_screen.json
./run.py propose                # size + rank + portfolio-check -> data/proposals.json
./run.py approve <id> --approver <name>    # the only path that opens a position
./run.py mark                   # re-price, then TAKE any exit that is due
./run.py mark --no-auto-exit    # decide but do not execute
./run.py propose --no-auto-open # tickets only, even with auto_approve on
./run.py config                 # every knob + what has been changed from the mandate
./run.py config --set auto_approve=off --set max_collateral_per_trade=750
./run.py watch                  # refresh the watchlist -- opens nothing, runs 24/7
./run.py status | dashboard
```

**`COMMANDS.md` is the single operational reference** — every command, every
flag, and the from-scratch EC2 setup. Update it in the same commit as any
command change. The README links to it and does not repeat it; `docs/DEPLOY.md`
was deleted because it had drifted into saying things that were no longer true.

### Current state (2026-08-31)

- Paper only. `Settings.mode == "paper"`, $3,000 starting cash, real ledger flat.
- The agentic Robinhood account reached level 3 on 2026-08-31, so spreads are
  now *permitted* there. It holds $14.72 of buying power, so none are
  *affordable*. See §10. Nothing has been placed.
- Placing a live order remains a human action. `open_approved` and `apply_exits`
  both refuse a live ledger in code; that is unchanged by the level upgrade.
- **Per-trade human approval is OFF** (`auto_approve = true`, paper only), so
  `./run.py propose` opens the clear proposals itself. `paper_trading` is on.
  Both are in `data/settings.json`; `./run.py config` prints the truth.
- 123 tests, ruff clean.

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

## 10. Permission and capital are two different blockers

The agentic Robinhood account is `421118043` ("Agentic", limited margin) — the
only one of five in this login that the agent can reach.

**Options level: resolved.** It was `option_level_2` (long options and
cash-secured puts, no spreads). Re-checked 2026-08-31: it is now
**`option_level_3`**, which is what a credit spread requires. Verify with
`get_accounts` rather than trusting this line — levels change.

**Capital: still binding.** Same date, `get_portfolio` on that account:

| | |
|---|---|
| total value | $1,208.08 |
| equity (held in stock) | $1,193.36 |
| cash / buying power | **$14.72** |

The paper agent is configured for $3,000 and its proposals size to $624–$991 of
collateral. Against the real account every one of them fails the balance rule in
§12 — not because the code is wrong but because the money is in stock, not cash.
Going live would need `Settings.starting_cash` reset to the real buying power,
at which point the $1,000 per-trade cap and $100 minimum credit leave almost no
qualifying spread. This is a sizing problem, not a permissions one.

The general lesson: "can this account trade spreads" and "can this account
afford one" fail in completely different places, and clearing the first says
nothing about the second.

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

## 12. `cash - collateral` is not the money you have

Buying power was defined as `cash - collateral_held`. Both terms are correct on
their own and the combination is wrong: `cash` already includes the credit
received, and `collateral` is `(width - credit) x 100`, i.e. measured *net of
that same credit*. The premium gets counted twice, so the reported free balance
overstates the real one by exactly the credit taken in.

On one $5-wide spread sold for $1.10 it reported $2,720 free when $2,610 was
unencumbered. Exaggerated to make the shape obvious — thirty spreads at a $4.00
credit on a $5 width — it reported **$12,000 of buying power on an account with
exactly $0 left**.

The fix is to net off the gross amount the book can be called on to pay:

```
capital_at_risk = width x 100 x contracts        # not width net of credit
buying_power    = cash - capital_at_risk
                = starting cash + realised P/L - collateral - fees   # falls out
```

The second identity is the sanity check: available balance is what you started
with, less what you have committed. Opening costs `collateral + fees` of it.

Two things this also exposed:

- The check ran at proposal time only. Between proposing and approving, other
  positions can open — so the balance gate now lives in `open_approved`, and
  fires on the **filled** collateral. A worse fill means less credit, which
  means *more* collateral: an $624 ticket in a live run filled at $636.
- `risk.check` compared a per-contract `Spread.collateral` against portfolio
  totals, so a 2-lot was checked as a 1-lot. `contracts` is now a parameter.

## 13. A stop loss that waits for a human is not a stop loss

The skill's "never auto-place a trade" rule is about *opening* risk. Applying it
to exits produces something that cannot do its job: a profit target only works
if it is taken mechanically, and a stop has to be able to fire while nobody is
watching. So the agent decides and acts on exits itself — but inside a box that
is drawn in code, not in prose:

- **closing only.** `apply_exits` can only buy back a short the account already
  carries. It cannot open exposure, and the entry approval gate is untouched.
- **paper only.** It raises `ApprovalRequired` against a live ledger and renders
  `exits.ticket()` for a human instead.
- **fresh marks only.** The caller passes the set of ids that actually
  re-priced. A mark that failed to update decides nothing — this is why
  `mark_positions` now returns `(notes, fresh)`.
- **not blocked by the opening range.** That gate exists to stop the account
  taking on new risk at the day's worst prices. Refusing to let a stop fire for
  the same reason would hold a losing position open exactly while it moves.

Two stop conditions are needed, not one. The classic rule — exit when the
buyback costs `2x` the credit taken in — is unreachable when the credit is large
relative to the width (a $4.00 credit on a $5 width can never cost $8 to close),
and those are precisely the positions where collateral is thin. So a second
stop fires at 50% of the defined max loss. Risk branches are checked before the
profit branch so no later edit can let a profit rule mask a stop.

Policy lives in `pcs/exits.py` and is pure; execution lives in
`paper_broker.apply_exits`. Keeping them apart is what makes the triggers
testable without a ledger.

---

## 14. A configurable rule has to announce that it was configured

The strategy's numbers are now overridable per account (`max_collateral_per_trade`,
`min_credit_per_trade`, `max_credit_per_trade`, `min_otm_cushion`,
`take_profit_pct`, and `max_loss_per_trade` as an alias for the collateral cap).
Making them settable was easy. Making them *safe* to settle turned on three
things:

**`None` means "use the mandate", not "zero".** An untouched install resolves to
`STRATEGY` itself — `settings.strategy() is STRATEGY` — so behaviour is
identical to before the knobs existed and there is no migration.

**Consumers must not be able to tell.** `Settings.strategy()` returns a real
frozen `Strategy` built with `dataclasses.replace`, so the optimizer, the
exits module and the broker keep reading the same attributes. The only code that
knows an override happened is `deviations()`. That kept the change from
sprawling into every call site as `settings.x or STRATEGY.x`.

**The display is the risk.** A dashboard that renders the loosened number as if
it were the mandate is how a limit quietly stops being a limit. So the Rules tab
reads from `settings.strategy()` *and* carries a banner naming every deviation,
the proposal ticket prints `sized under NON-STANDARD rules`, and `config` ends
with the same list. Three surfaces, one source: `Settings.deviations()`.

The same reasoning applies to `auto_approve`. Turning per-trade sign-off off is
a legitimate paper-account convenience, but the two things that must not follow
from it are (a) reaching a live order and (b) an audit trail that reads as if a
human looked at the trade. So `require_approval()` ignores the setting entirely
when `mode != "paper"`, `open_approved` independently refuses a non-paper
ledger, and the recorded approver is the literal string
`agent (auto-approve, paper)`.

A related correction: `deploy/pcs-refresh.sh` carried the comment *"Never
approves anything. Opening a position stays a human action."* That became false
the moment auto-approve shipped. A stale safety comment is worse than no
comment, because it is what someone reads instead of checking.

---

## 15. Deploying it taught us more than writing it

Six real bugs surfaced by running the deploy, none of which produced an error.
That is the pattern worth carrying: `nginx -t` passed, certbot succeeded, the
installer printed `== done`, the site returned HTTP 200 — while serving the
wrong page from the wrong file on a box whose timers would never have fired
during market hours.

**A comment is not a fact.** The timer files said *"Instance timezone is
America/New_York, so this follows DST on its own."* Nothing ever set it. EC2
defaults to UTC, so `10:15` meant 06:15 ET. The comment described an assumption
in the voice of an established fact, which is the most expensive kind.

**Put the schedule's timezone in the schedule.** `timedatectl set-timezone`
works but makes market hours a property of *the machine*; anything else later
scheduled on that box silently moves four hours. systemd v252+ takes the zone
inside the spec — `OnCalendar=Mon..Fri 10:15 America/New_York` — which keeps it
a property of this agent. Test the capability (`systemd-analyze calendar` with a
zone) rather than parsing a version.

**systemd `/N` repetition applies within one field.** `09:35..15:55/15:00` is
not a valid way to say "every 15 minutes across the session"; the minute field
would have to carry the hour. Two rules instead.

**`certbot --nginx` edits your config and picks the block itself.** The real
vhost cannot be enabled before the certificate exists (it references cert paths),
so unlinking it left the default site as the only candidate — certbot put the
domain and the TLS config *there*. `certbot certonly --webroot` obtains the
certificate without touching config at all; a throwaway HTTP-only vhost answers
the challenge.

**Duplicate `server_name` on one port is not an nginx error.** The first block
loaded wins and `sites-enabled` sorts alphabetically, so `default` beat `pcs`.
Nothing fails; the wrong page is simply served. Now checked for explicitly.

**A blanket port-80 redirect kills renewal.** Redirecting everything to HTTPS
works for 90 days and then quietly stops `certbot renew` from proving the domain.
`/.well-known/acme-challenge/` has to come before the redirect.

**`ProtectSystem=strict` and library caches.** yfinance resolves its cache
through platformdirs to `$HOME/.cache` — inside the read-only region. Nothing
caught it because the install only ran `status` and `dashboard`, neither of which
touches yfinance; the first thing to exercise it would have been the first timer
firing. Fixed with `CacheDirectory=` rather than widening `ReadWritePaths` over
the install tree.

**Verify from outside the box.** Every one of these was found by `curl` from the
laptop asking what a stranger actually receives — not by reading logs on the
instance, which reported success throughout. Run a scheduled job by hand *out of
hours* before trusting a timer: it exercises network, sandbox and permissions
for real, and holds at the fill because the market is shut.

---

## What the tests actually protect

123 tests, and they are deliberately aimed at the rules that would fail
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
- buying power never double-counts the credit, and an open can never drive the
  available balance negative
- a refused open leaves the ledger byte-identical -- no cash moved, no event
- exit triggers fire on the exact number, and one cent short of it they do not
- auto-exit refuses a live ledger, and never acts on a stale mark
- **no setting can lift the approval requirement off a non-paper ledger** —
  parametrized over `live`/`LIVE`/`real`/`""`, because the check is
  `mode == "paper"`, not `mode != "live"`
- an override reaches the resolved strategy *and* shows up in `deviations()`,
  asserted per field
- overriding one account never mutates the shared frozen baseline
- `max_loss_per_trade` and `max_collateral_per_trade` are the same cap, and the
  tighter of the two binds in both directions
- the master switch stops entries without stopping exits
- the watchlist cannot open a position — asserted by reading the module source
  for `paper_broker`, because "it currently doesn't" is not a guarantee
- a stale-session watchlist reports `tradeable == False`; an out-of-hours price
  is never presented as a premium you could get
- a name blocked by a portfolio cap stays on the watchlist **with its price**,
  rather than vanishing the way it does from the proposal list
