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
| `pcs/doctor.py` | every gate that stops a fill, in binding order — the "why has nothing opened?" answer | anything that mutates; it is read-only by construction |
| `pcs/learning.py` | the journal: closed-trade outcomes, operational faults, symbol quarantines | anything that opens or closes; it never writes `Settings` |
| `pcs/paper_broker.py` | the three open gates, simulated fills, marking, exit **execution** | exit policy |
| `pcs/ledger.py` | cash, positions, append-only events, all account arithmetic | any policy |
| `pcs/pipeline.py` | wiring: screen → shortlist → size → earnings → propose | new rules |
| `pcs/proposer.py` / `pcs/dashboard.py` | proposal records + tickets; the HTML view | logic of any kind |
| `pcs/brand.py` | the mark: logo, favicon, the one chart path | anything with dependencies |
| `pcs/viewers.py` | dashboard logins: PBKDF2 hash, verify, add, revoke | anything about sessions |
| `pcs/authd.py` | the login page, the session cookie, nginx's `auth_request` | anything about trading |
| `pcs/health.py` | what each run DID: run records, mark staleness, the five alert conditions | trading decisions; it observes and never mutates |

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
- `pcs/learning.py` may bench a symbol and nothing else. It never writes
  `Settings`, never touches `STRATEGY`, and never opens or closes a position.
  A quarantine is one-directional — it can only remove a candidate — and it
  expires on its own. See §16.
- Logins are `/etc/pcs/viewers` (PBKDF2, `pcs/viewers.py`), not `.htpasswd`.
- `/etc/pcs` is root-owned, 0750, and **read-only** to `pcs-authd`. Anything that
  service writes lives in `/var/lib/pcs` (`StateDirectory=`): the session key and
  `overrides.json`. Both outside `$APP`, which is rsynced with `--delete`.
  `htpasswd` is gone on purpose: its `-c` flag TRUNCATES the file it is given,
  so one stray `-c` deleted every login including your own. `viewers.add()`
  cannot express that operation at all.
- `/etc/pcs/viewers` and `/etc/pcs/session.key` live outside `$APP` because
  `$APP` is rsynced with `--delete`. A wiped key logs everyone out; a wiped
  viewers file locks everyone out.
- Anything written on the box by its own timers (`data/ledger.json`,
  `data/watchlist.json`, `data/journal.json`, `data/settings.json`) must be in
  BOTH `.gitignore` and the `rsync --delete` excludes in `deploy/bootstrap.sh`.
  Missing either one means a redeploy silently replaces live state with a
  laptop snapshot.

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
./run.py learn                  # what the closed record supports + self-repair pass
./run.py doctor                 # why has nothing opened? every gate, in order
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
- Self-learning is on (`self_repair = true`). The journal is empty: 0 closed
  trades against a floor of 8, so it reports "insufficient" and nothing else.
- The dashboard defaults to a **light** palette with a header toggle for dark,
  persisted per browser in `localStorage` under `pcs-theme`. It does not follow
  `prefers-color-scheme` — see §17.
- 372 tests, ruff clean.

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

**The take-profit trigger is the bottom of its band, not the middle.** The rule
fires on the first mark at or above `take_profit_pct`; the band's upper edge is
the line a position should never still be held past. That upper edge is not
reachable by a `Settings` override -- only `take_profit_pct` is in
`_OVERRIDABLE` -- so widening the band (50-65% -> 50-75% on 2026-09-01) had to
change `STRATEGY` itself, and with it `docs/STRATEGY.md` and the vendored
`SKILL.md`. `_validate()` now asserts the trigger sits inside the band, because
the band is rendered on the dashboard and on every ticket as the rule in force:
a trigger outside it makes the page state something the engine does not do.
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

---

## 16. What a trading agent may learn by itself, and what it may only suggest

`pcs/learning.py` splits the agent's own record into two kinds of thing and
treats them completely differently. The split is the design, not an
implementation detail.

**Trades produce suggestions.** Grouping 12 closed spreads by cushion at entry
and moving `min_otm_cushion` toward whichever bucket won is not learning; it is
fitting a rule to noise, and the account is what pays when the fit is wrong.
So: no lesson under `learning_min_sample` (8) closed trades, no comparison
unless both sides hold `learning_min_group` (4), and no finding unless the
win-rate gap clears `learning_min_effect` (20 points). Every lesson prints its
`config --set` line and stops there.

`insufficient` is a first-class result. Returning an empty list would read on
the dashboard as "nothing is wrong"; "we do not know yet" is a different
statement and the true one for almost all of this agent's life.

**Faults are repaired.** A symbol whose chain will not price is a bug, not a
trading opinion, and benching it is safe for one specific reason: **there is no
input to `self_repair` that makes the agent trade something it otherwise would
not.** It can only shorten the candidate list. That asymmetry is what makes
running it unattended defensible, and `test_self_repair_never_opens_or_closes_anything`
plus `test_self_repair_never_touches_settings` are what keep it true.

Two details that mattered:

- A quarantine has to **expire on its own**, and expiry has to run even when
  the book is empty. `cmd_mark` returns early with no open positions, so the
  journal pass had to be lifted out into `_journal_pass()` and called on both
  paths — otherwise an account that closed everything would keep its benched
  names benched forever.
- `open_blocked` faults are recorded and deliberately **not** counted toward a
  bench. A refused fill is nearly always the portfolio caps working correctly;
  benching the ticker for it would punish the symbol for the account being full.

**What cannot be learned from is stated, not omitted.** `Position` never records
% off the 52-week high, % from the 50dma, short delta, or IV at open, so those
are unlearnable. `feature_gaps()` lists them on the dashboard, because "no
signal from IV" and "IV was never written down" look identical on a page and
mean opposite things.

**The rendered page and the served page are not the same file.** `render()`
writes `dashboard.html` beside the code; nginx serves `/var/www/pcs/index.html`.
For a while only `bootstrap.sh` copied one to the other, so the served page
froze at install time -- `propose` opened four positions and the dashboard kept
showing an empty book, with the header timestamp moving only on redeploys.
`render()` now publishes to `config.WEB_INDEX` atomically on every call and
warns on stderr if it cannot. Any new writer of the page must publish too.

**HTTP Basic auth cannot be made attractive.** The credential dialog is browser
chrome: no CSS reaches it, no markup replaces it, and nginx cannot suppress the
`WWW-Authenticate` header that summons it. A styled login page therefore is not
a CSS job -- it requires a form, which requires something server-side that can
verify a password and issue a session. That is `pcs/authd.py`: `auth_request`
to a loopback service, PBKDF2 credentials in `/etc/pcs/viewers`, and an
HMAC-signed stateless cookie. Consequences worth remembering: a single session
cannot be revoked early (rotate `/etc/pcs/session.key` to cut all of them), and
both that key and the viewers file must live outside `$APP` because `$APP` is
rsynced with `--delete`.

## 17. A dark dashboard is a preference, not a default

The first dashboard was dark because it was built at night. It is read on a
phone in daylight at least as often, so the palette is now light by default
with a toggle in the header.

It deliberately does **not** follow `prefers-color-scheme`. A phone on the
system-wide dark schedule would flip the one view of a live account to a
palette its owner did not choose, and the toggle already covers the case. The
saved choice is applied by an inline script in `<head>` — after `<body>`
renders it is too late and you get a flash of the wrong theme.

The colour tokens are defined twice: the full palette on bare `:root`, and the
dark overrides under `:root[data-theme="dark"]`. No colour has its only
definition inside the dark block, so a token that is missing from the override
degrades to its light value rather than to nothing.

One real bug the rewrite fixed: `.tag` was defined twice — once for the brand
tagline, once for the pill chips in tables — and the pill rules won, wrapping
the tagline in a rounded box. Renamed to `.brandtag`.


---

## 21. A ledger cannot record what did not happen

The dashboard read the ledger, and a ledger only holds events. The three states
that cost the most money are non-events:

* a timer that never fired,
* a mark that failed to re-price,
* an exit that was due and was held.

All three render as a flat book with yesterday's numbers — identical to a quiet
market. `cmd_mark` distinguished all three and printed them to stdout, which on
a systemd timer is a file nobody opens.

`pcs/health.py` persists one `Run` per command to `data/health.json` (what it
priced, what it decided, what it could not act on) and the page reads it. Three
rules that fell out of building it:

* **Telemetry must never be able to break trading.** Every read and write in
  that module swallows its errors. A corrupt health file starts a fresh record
  rather than raising inside a mark.
* **A row with an unknown field is skipped, not fatal.** A file written by a
  newer build must not take the whole record down on a rollback.
* **Record the stale symbols, do not re-derive them.** A position that failed to
  price today can still carry a perfectly good mark from yesterday, so
  `marked_at` does not identify it. Only the run that attempted the mark knows.

## 22. "Stale" is a property of the reader, not the file

A mark taken 40 minutes ago is stale during regular hours and completely normal
overnight — the timer only fires 09:35–16:00. `_mark_state()` therefore takes
the session, and the same timestamp renders `fresh` after the close and `stale`
at 11:00. An alerting rule that ignores this fires every single night and trains
the reader to scroll past the panel that will one day matter.

## 23. The sector cap counts labels, not correlation

MRVL + QCOM (Information Technology) and META + GOOGL (Communication Services)
is two per sector and passes `max_positions_per_sector`. It is also four
large-cap tech names that move together on one bad index day: $3,000 of capital
at risk on a $3,000 account, $1,953 of max loss. The cap was satisfied the whole
way in. The page now states the worst case as a percentage and says plainly that
the cap counts GICS labels — this is COUNCIL.md operator finding 5, and it
happened live on 2026-09-01 before it was fixed.

## 24. A machine check must not depend on prose

`bootstrap.sh` refused to overwrite `/etc/nginx/sites-available/pcs` unless it
found the phrase `put credit spread agent` in it — a phrase from the config's
header comment. Rewriting that header for the login work took the phrase with
it, so every box that already had a config refused every subsequent redeploy,
and died *before* installing the login service it was there to install.

Two fixes, both general:

* The marker is now `# managed-by: pcs-bootstrap` on line 1 and nothing else is
  allowed to be load-bearing. A legacy config is recognised by `root /var/www/pcs;`,
  which no unrelated vhost would contain.
* The check moved to immediately before the write. A guard that aborts 40 lines
  early leaves the box half-provisioned, which is worse than either outcome it
  was choosing between. It also keeps one `.prev` copy.


## 25. A service cannot create a file in a directory it has no write bit on

`pcs-authd` runs as `pcs`; `/etc/pcs` was `0750 root:pcs`. Group `pcs` gets
`r-x` — no `w` — so `os.open("/etc/pcs/session.key", O_CREAT)` fails and the
service crash-loops before it ever binds. `ReadWritePaths=/etc/pcs` in the unit
did not help: systemd's sandbox controls whether a mount is read-only, and
ordinary file permissions still apply on top of it.

The split that came out of it is the right one regardless of the bug:

* **`/etc/pcs`** — credentials, root-owned, read-only to the service. It can
  check a password and cannot rewrite the file it checks against. A directory
  the service could write is one where it could unlink `viewers` and drop in
  its own.
* **`/var/lib/pcs`** — the service's own state, via `StateDirectory=pcs`, which
  creates it with the unit's user and adds it to `ReadWritePaths` automatically.

Atomic writes need this too: `tmp.replace(path)` creates a sibling, which is a
write to the *directory*, so a file-scoped `ReadWritePaths` would not have been
enough either.

## 26. One value, one home

The dashboard writes `max_open_positions` and so does `run.py config --set`.
Applying the override after `settings.json` makes the page authoritative, which
silently breaks the CLI: `--set` writes, the override keeps winning, and the
command looks ignored. `cmd_config` therefore **clears the override** for every
key it writes. Last human action wins, whichever way it was made — which is the
only rule a person can hold in their head.

The allowlist is enforced on read as well as on write. Hand-editing
`overrides.json` to add `paper_trading` does nothing: `load_overrides()` filters
to `DASHBOARD_SETTABLE` before anything is applied. A validator on one side of a
file is not a validator.

---

## 27. A page that shows the same money on two bases reads as broken arithmetic

The dashboard showed `collateral at risk $1,953` and `available balance
$1,046.40` side by side, off `cash $4,046.40`. Both were right. They are not
the same base: collateral is the width **net** of the credit (`collateral_held`,
what the book can actually lose), while the buying-power hold is the **gross**
width (`capital_at_risk`, the conservative number the risk gate uses). A reader
who subtracts the visible risk number from the visible cash number gets
$2,093.40 and the card says $1,046.40, so the page looks like it cannot add up.

The fix was not to pick one. Both are load-bearing -- loosening `buying_power`
to `cash - collateral` would quietly relax the balance floor in `risk.py`, which
is a risk change wearing a formatting change's clothes. The fix was to make
every card state its own basis, and to order them so they read as one
arithmetic: start + premium = cash, cash carries collateral, what is left is
free.

**`premium_collected` was the missing card.** It is the single number that
explains why cash sits above starting cash, and it was the only one not shown.

## 28. One class, one meaning -- `.sub` cost the watchlist its layout

`.sub` is the **page subtitle**: `color:dim; font-size:13px; margin-bottom:16px`,
applied to a `<div>` under the masthead. The watchlist table reused the same
class on `<span>` elements for the second line inside a cell. A span is inline,
so nothing wrapped and the bottom margin did nothing:

    METACommunication Services    $322nat $270    47.5%on $678    3.2%66% est. win

Every pair in the table ran together. The class name was descriptive enough to
look correct at the call site and carried no `display` rule, so the bug was
invisible in the source and obvious on screen. Cell sub-lines are now `.csub`,
which owns `display:block`.

## 29. An empty record is not evidence that nothing ran

`pcs/health.py` shipped after the agent had been trading for weeks.
`health.json` starts empty, so `last("mark")` was `None`, so the page opened
with a CRITICAL **"the mark loop has never run"** -- above a table of four
positions each stamped `marked 4h ago`. The alert was false, it was first, and
it was red, which is precisely how a reader learns to skip the panel that will
matter later.

The health record is not the only witness. The **ledger is older than the
record**: a position cannot exist unless `propose` ran, and cannot carry
`marked_at` unless `mark` did. `health.ledger_evidence()` reads that, and the
alert now fires only when both are silent. Falling back does not go quiet -- an
old `marked_at` still trips `mark_stalled`.

Generalisation: any monitor added to a system already in flight will have a
cold-start window where absence of record looks like absence of event. Reconcile
against the state the system was already keeping before claiming a fault.

## 30. `credit_dollars` is banked; `credit_open` is quoted

`paper_broker` stores `credit_dollars = gross - fees`, so cash and `net_liq`
carry the fee. `Position.open_pl` recomputed from `credit_open * 100 *
contracts` and did not, which put two totals on one page that disagreed by
exactly the fill fees: unrealised said `+$53.63`, net liq minus starting cash
said `+$53.15`. Small, and it scales with contract count.

`open_pl` and `pct_of_max_credit` now both work off `credit_dollars`, which also
makes unrealised consistent with `realized_pl` (already net of both open and
close fees) and means the take-profit rule fires on money the account keeps.

## 31. Sort on the key, not on the rendered cell

The dashboard is a static file behind nginx, so sorting is client-side or it
does not exist. The temptation is to sort on the text already in the cell --
but that cell holds `$849`, `17.8%`, `2026-10-02` and `—`, which is four
different parses and at least one of them silently wrong (`parseFloat` reads
every ISO date in the column as `2026`, so they all tie).

The value that produced the text is in hand at render time. `_table()` takes a
grid of keys parallel to `rows` and emits `data-s`; the column's type comes from
the first key that exists, so the caller declares it by passing a number or a
string rather than by passing a flag as well.

`None` means *this row has nothing to rank on here* -- a name with no sizeable
spread has no premium. Those rows sink in **both** directions, so reversing the
sort never buries the rows the reader was looking at.

## 32. A header you cannot see is not a control

The table collapses to a stack of cards under 700px and `thead` becomes
`display:none`. Click-to-sort headers alone would therefore have been a feature
that silently does not exist on a phone -- which is where a dashboard behind a
login actually gets read. The select above the table drives the same code and
survives the breakpoint.

Same class of bug as the nginx marker: the thing that worked was checked at the
width it was built at.

## 33. Record the failure, or a dead job is indistinguishable from a quiet one

`health.record("watch", ...)` was the **last** line of `cmd_watch`. A run that
raised -- a network blip, a rate limit on a 500-name screen -- wrote nothing at
all, so a job failing every hour looked exactly like a timer that had never
fired. Both render as silence.

The screen is now wrapped and records `ok=False` with the exception before
re-raising. Same rule as §29 from the other side: §29 was *absence of record is
not absence of event*; this is *presence of the event has to survive the event
going wrong*.

## 34. Measure the artifact, not the schedule

"Refresh the watchlist several times a day" cannot be enforced on the timer.
`OnCalendar=hourly` can fire twenty-four times and leave the file untouched
twenty-four times, and the unit still reports success on a `oneshot` that
exited zero after writing nothing new.

The watchlist stamps itself, so **age of the file** is the only honest measure
of the cadence -- and it is also the only thing on the page with no ledger
behind it. If the refresh stops, every name keeps its last quote and the tab
goes on looking populated and current. `WATCH_STALE_AFTER_H = 8` sits under the
promised four-a-day (one every six hours) so the alert fires once the cadence
has actually fallen below the floor, not on one missed run.

The banner now prints the age beside the timestamp. "Refreshed 2026-08-31
23:12:10" makes the reader do the subtraction, and that subtraction is the
entire question the timestamp was there to answer.


## 35. Two premium figures, 48 cents apart, and which one each identity uses

`collateral` is set at fill as `width x 100 - fill x 100`, i.e. width less the
**quoted** credit. `credit_dollars` is banked **net of the fill fee**. So the
open book carries two premium totals that differ by exactly the fees:

| property | basis | the identity it belongs to |
|---|---|---|
| `Ledger.premium_collected` | net of fees | `starting_cash + premium = cash` |
| `Ledger.gross_premium` | as quoted | `capital_at_risk - gross = collateral_held` |

Using the wrong one is invisible in code and glaring on a page. The dashboard
said "pay out $3,000, keep the $1,146.52 of premium, so the real loss is
$1,853.00" -- three correct numbers in a sentence that is wrong by $0.48,
because collateral nets the gross credit and `premium_collected` does not.
A near-miss like that is worse than an obviously wrong number: it reads as
sloppiness across the whole page.

`gross_premium` exists so the risk identity has a term that closes exactly. The
fee difference is then stated once, in the one sentence where both figures
appear, instead of being left for the reader to discover.

## 36. A dollar amount with a label is not an explanation

Three rounds of "the numbers are messy" were answered with better labels, more
subtitles, and a reconciling card ladder. The arithmetic was right after the
first round. It stayed unreadable, for two reasons that no amount of labelling
fixes:

- **The bridge number was never on the page.** `net_liq` computed the cost to
  close the whole book inline and threw it away, so $1,046 of premium sat next
  to $53 of profit with nothing in between. `Ledger.cost_to_close` is now a
  property, used by `net_liq` and shown as its own card.
- **Two unrelated cards collided on the same value.** On a $3,000 account with
  $3,000 of width open, premium taken in and cash-less-the-hold are both
  $1,046.40 -- a coincidence that reads exactly like a copy-paste bug. Each
  card's subtitle now prints its own subtraction, so the pair reads as two
  derivations rather than one number repeated.

The fix that actually landed was prose: three sentences above the cards saying
the arithmetic out loud, in the order it happens, so every figure below has
already been met in a sentence. Card labels are the plain-English name now
("account value", "worst case"), with the broker's term as the subtitle rather
than the headline.

Every subtraction the page states is pinned by
`test_every_subtraction_on_the_page_actually_comes_out`.
