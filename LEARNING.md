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
| `pcs/chains.py` | `PutQuote` / `PutChain`, three providers (yfinance / robinhood / model), `pick_expiration`, `rank_expirations`, `alternate_expirations` | sizing rules |
| `pcs/screener.py` | the 50dma buckets and the earnings tri-state | anything about options |
| `pcs/optimizer.py` | per-trade sizing: the `(short, long)` search, all §1.4 caps | portfolio-level caps |
| `pcs/risk.py` | portfolio caps across a batch, incl. the balance floor | per-trade caps |
| `pcs/session.py` | clock, holidays, quote-quality grade, the opening-range gate | anything that mutates |
| `pcs/exits.py` | §1.7 exit **decisions** (take profit / stop / defend) — pure | ledger mutation |
| `pcs/watchlist.py` | what is tracked and why it has not been taken — **observation only** | anything that opens; it must not import `paper_broker` |
| `pcs/doctor.py` | every gate that stops a fill, in binding order — the "why has nothing opened?" answer | anything that mutates; it is read-only by construction |
| `pcs/learning.py` | the journal: closed-trade outcomes, operational faults, symbol quarantines | anything that opens or closes; it never writes `Settings` |
| `pcs/paper_broker.py` | the three open gates, simulated fills, marking, expiry settlement, exit **execution** | exit policy |
| `pcs/shadow.py` | what each early exit would have booked held to expiration (research columns `shadow_*`) | anything a decision reads; nothing in the exit path may depend on it |
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
   filled collateral breaches the $1,000 per-trade cap or takes the book over
   `max_total_collateral`. `BelowCreditFloor` when the filled credit is under
   $100 a contract (53).
4. **Portfolio caps** — `risk.check`, batch-aware via `pending`. With a
   session, collateral and balance are sized at the expected paper fill
   (`optimizer.fill_collateral`), and so is each `pending` entry (53).
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
- Anything written on the box by its own timers (`data/ledger.json` and its
  `.lock`, `proposals.json`, `watchlist.json`, `journal.json`, `settings.json`,
  `health.json`, `snapshots.json`, `last_screen*.json`) must be in `.gitignore`
  AND in the `rsync --delete` excludes of BOTH `deploy/push.sh` and
  `deploy/bootstrap.sh`. Missing any one means a redeploy silently replaces
  live state with a laptop snapshot. `tests/test_deploy_excludes.py` pins it.
- Every command that writes the ledger loads it inside `ledger.locked()` and
  saves before leaving the block. A Ledger loaded before the lock and saved
  inside it is the lost update of 49. Readers may load unlocked because
  `save` is atomic (`config.atomic_write`).
- The screen and the paper fill read one fill price,
  `optimizer.paper_fill_credit`. A cap checked only at the ticket is a ticket
  refused at the fill (50).
- A mark that fails inside the opening range is never counted toward a
  quarantine (50).
- Any change to how trades are picked adds a row to
  `expectancy.SELECTION_CHANGES` in the same commit; the History tab measures
  the record since the latest row (51). Exit changes need no row.
- A close costs `Settings.spread_fees(contracts)` whichever path takes it --
  the same two-leg fee the optimizer charges to open. See 49.
- Nothing is recorded as a measured vol unless it came off a live chain for
  that leg: `Spread.iv_measured`, `mark_iv`. A placeholder is not a reading.
- The suite runs inside the live install on every deploy, so no test may reach
  a live path. `tests/conftest.py` sets `PCS_RUNTIME`, `PCS_WEB_ROOT`,
  `PCS_STATE_DIR`, `PCS_SESSION_KEY` and `PCS_VIEWERS` before `pcs` is
  imported; a new path goes under `config.RUNTIME`, and
  `tests/test_live_paths.py` fails on any that does not (54).
- The doctor never offers a higher limit as the fix for reaching one (54).
- The short strike sits at least 5% below spot with a delta of 0.30 or less, and
  spreads are ranked by chance of profit before return (`optimizer.rank_key`,
  used for the pick within a name and across names). `max_short_delta` is
  deliberately not an overridable setting (55).

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
- 546 tests, ruff clean.

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

*Only half true until 50.* The probe grew from one name to five, but
`cmd_propose` still returned when all five missed the window -- 2026-10-06
aborted that way with 112 names shortlisted. 50 removed the return.

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

## 37. Two buying powers, and why the strict one is not just caution

A defined-risk spread is margined by a broker at its **max loss**, because the
credit is already in the account. So the statement reads:

```
starting cash + premium = total balance - locked collateral = free cash
```

That is `Ledger.free_cash` (`cash - collateral_held`), and it is the figure that
reconciles against a real brokerage account.

`Ledger.buying_power` is deliberately different -- `cash - capital_at_risk`, the
**gross width** -- and this was very nearly reversed on the grounds that it
disagrees with the broker. It does. It also has to. Open until each gate refuses
($3,000 cash, $400 collateral / $100 credit / $500 width per spread), then
settle everything at max loss:

| gate basis | opens | total width | cash after a total wipeout |
|---|---|---|---|
| broker, `cash - collateral` | 9 | $4,500 | **-$600** |
| strict, `cash - width` | 7 | $3,500 | +$200 |

The broker's basis permits an account to commit more max loss than it holds cash
to settle. A brokerage absorbs that with a margin call; a cash account has no
such line, and a book of beaten-down names across two sectors is precisely the
correlated tail where every position lands at once. `test_balance.py` and
`test_the_balance_can_never_be_driven_negative` exist for this and should not be
"fixed".

Both figures are now on the page, because they answer different questions and
hiding either one is its own failure: showing only the broker's leaves the page
claiming $2,293 is free while the agent silently refuses at $1,146, and showing
only the agent's makes the page irreconcilable with the brokerage statement.

**The process lesson is the sharper one.** The inconsistency that prompted this
was real -- `risk.check` and `open_position` both CHARGE at `collateral + fees`
while measuring capacity at `cash - width` -- and "make the two sides match"
looked like an obvious cleanup. It would have deleted a solvency guarantee. The
existing test file said so in its module docstring. Read the tests that cover a
rule before concluding the rule is an accident.

---

## 38. The first real loss was the plumbing, not the strategy

GOOGL 325/320, sold for $1.1625 on 2026-09-01 at 14:39 UTC. Stopped out the
next morning at 13:50 UTC for a realized **-$209.31** -- 54% of the $383.75 max
loss, on day 1 of 31.

The stock over that span: **336.64 -> 335.26**. Down 0.41%, and still 3.1%
clear of the short strike, which was never touched. `under_strike_at_close` was
`False`. Nothing GOOGL did moved $2 of spread value.

The mark did it. `cost_to_close` blends the mid with a worst case,
`nat = sq.ask - lq.bid`. When the long leg's bid is missing the hedge stops
subtracting, and a five-wide defined-risk spread is valued as a naked short
put. `PutQuote.mid` fails the same way one line earlier: no broker mark and no
two-sided book and it falls through to `last` -- possibly days old -- then to
whichever side is non-zero, returning a guess with the shape of a quote.

Three things had to line up, and all three are worth keeping in mind:

1. **13:50 UTC is 09:50 ET.** The box clock is UTC, and that is twenty minutes
   into the session -- inside the opening range this system already treats as
   untrustworthy for pricing. `opening_settle_minutes` blocks *opening* there.
   `exits.py` deliberately does NOT block exits there, and the reasoning in its
   docstring is sound: a stop that will not fire while a position is moving
   against you is not a stop. That reasoning holds. It just assumes the mark is
   real.
2. **`cost_to_close` returned `None` only for a MISSING STRIKE.** A strike that
   exists with a row of zeros priced happily. So `exits.py`'s stated contract --
   *"a stale mark decides nothing"* -- did not actually hold; a junk mark
   decided plenty.
3. **The stop is tighter than it reads.** `stop_loss_credit_multiple = 2.0` on a
   spread that took in 23% of width fires at **30% of max loss**, well before
   the `stop_loss_pct_of_max_loss = 0.5` backstop can ever bite. That backstop
   is dead code for any thin-credit spread. Neither number is wrong; together
   they do not say what their names suggest.

### The fix, and why it is in this direction

`cost_to_close` now refuses to price unless BOTH legs carry either a broker
mark or a genuinely two-sided book, and clamps the result to the width -- a
vertical can never cost more than its max loss to buy back, which is arithmetic
and true under every quote.

Refusing to mark is the lenient failure. It only ever DELAYS a decision: the
position stays out of `fresh`, `exits.review` skips it, `mark` reports it under
"could not decide", and a human can still close it by hand. Marking badly MAKES
the decision, and here it makes it as a market order. The cost is a false
refusal when a long leg has genuinely gone to zero and the wide mark was right;
that is the cheap direction.

### The part that generalises

The stop was not wrong. It executed correctly on a number that was wrong. Every
guard on the DECISION -- thresholds, approval gates, caps -- is worth exactly
as much as the measurement under it, and this codebase had spent far more care
on the decisions than on the inputs.

It also changes what the record says. One closed trade, 0W/1L, is not evidence
about the strategy -- that trade never tested it. Do not read the win rate as a
signal until enough closes exist that were priced honestly. `learning.py` will
not draw a conclusion under 8 either way, which is the right answer for a
different reason than it thinks.

Pinned by `tests/test_mark_quality.py`, which reconstructs the exact chain and
asserts both halves: that the guard refuses it now, and that the old arithmetic
really did fire the stop -- otherwise the first assertion passes for the wrong
reason and proves nothing.


---

## 39. A cap that printed its own number and behaved as 1

`max_positions_per_ticker` did not work. `PortfolioView` held `symbols: set[str]`
and the gate was:

```python
if spread.symbol in symbols:
    reasons.append(f"already holding a spread on {spread.symbol} "
                   f"(cap {settings.max_positions_per_ticker} per ticker)")
```

Membership answers "any?"; the cap asks "how many?". So the setting behaved as 1
whatever it was set to, and the refusal message read the configured value back
to you while ignoring it -- the most expensive shape a bug can take, because the
output agrees with the setting. Setting it to 5 changed a sentence.

Now `Ledger.ticker_counts()` mirrors `sector_counts()` and the gate compares a
count, in `pv.ticker_counts` and in `pending` both, so a batch cannot build a
ladder past the cap one proposal at a time.

**A ticker has exactly one GICS sector, so `max_positions_per_sector` is the
ceiling the per-ticker cap can ever reach.** At a sector cap of 2, a per-ticker
cap of 5 still tops out at 2 -- raising only the per-ticker number looks like it
permits a deeper ladder and does not. `test_the_sector_cap_is_the_real_ceiling_on_a_ladder`
pins it so nobody re-derives that at the console.

Above a cap of 1 the watchlist's `HOLDING` also stopped meaning what it said.
It was set on `symbol in held`, which was the same statement as "cannot take
more" only while the cap was 1; above that it hid a permitted add behind a
status that reads as nothing-to-do. `HOLDING` now means *at the cap*, and
`Entry.held` carries the count.

---

## 40. A stop without a cooldown is a churn machine

The GOOGL stop-out (§38) had a second half nobody had noticed. The name was
stopped out at 13:50 and **re-proposed as a fresh open in the same run**,
blocked only because the master trading switch happened to be off. Nothing in
the risk manager stood between a stop firing and the same name being reopened.

`max_positions_per_ticker` is not that rule and never was: it caps what the
account HOLDS at one time and says nothing about re-entering after a close.

Why it matters most in exactly the case a stop is least trustworthy: a stop acts
on a mark, and §38 is the record of a mark being wrong. Re-entering immediately
re-establishes the same risk at a worse price, so one bad print gets paid for
twice. Waiting a few days costs at most one entry in a name that is one of
several hundred candidates.

`reentry_cooldown_days = 5` (0 disables). `Ledger.cooling_off()` returns
`{symbol: date it clears}`, and the rule is deliberately about the RESULT, not
the exit kind:

- **Losses only.** A take-profit close leaves nothing to cool off from --
  selling premium again in a name that just paid out is the strategy working.
- **Beyond a dollar.** `LOSS_FLOOR = -0.5`, the same line `learning._result`
  draws between LOSS and SCRATCH. A $0.12 fee-only close is not this loss.
- **Most recent close binds**, or an old loss makes a fresh one look served.
- **Fails open** on an unreadable timestamp. Benching a name permanently
  because of a malformed string is worse than missing one cooldown.

Enforced in **two** places, and the second is the one that matters:

1. `risk.check` -- so the ticket and the watchlist say why, rather than showing
   `READY` on a name the agent would refuse.
2. `paper_broker.open_approved` (`CoolingOff`) -- because this is the only gate
   that sees the present. A proposal carries the verdict it was given when it
   was written, so a ticket produced at 13:00 still reads `risk_ok` after a stop
   fires at 13:50. That is the exact sequence, and `risk.check` alone would not
   have caught it.

**Every gate added to the opening path has to be checked against the exit
path.** A rule that blocked a close would turn a delay into an unbounded loss.
`apply_exits` does not route through `open_approved` at all, so this is
structurally impossible -- and `test_a_cooldown_can_never_hold_a_losing_position_open`
pins it rather than leaving it to be re-reasoned.

---

## 41. The screen's own conditions finally survive the fill

`feature_gaps()` used to name four things the ledger never wrote down. Two of
them -- % off the 52-week high and % from the 50dma -- are *the entire screen*.
The journal could say how every trade ended and nothing about whether the setup
that picked it was worth picking.

They are recorded now, on `Position` and on `Outcome`:
`pct_off_high_at_open`, `pct_from_dma50_at_open`, `short_delta_at_open`,
`iv_at_open`. Delta and IV were already computed during sizing and thrown away
at fill; the two screen numbers live on `Candidate`, which does not reach the
broker, so they ride on `Proposal` -- the only thing that survives between
`propose` and `approve` -- and are passed into `open_approved`.

Three things this got right that are worth not undoing:

**`None`, not `0.0`.** `0.0` is a real reading: a stock exactly at its 52-week
high. A trade opened before the field existed must be distinguishable from one
measured at zero, so `_split` filters `None` before comparing. Without that
filter every pre-change row lands in the low bucket and the agent invents a
pattern out of the date a field was added.
`test_a_missing_feature_is_never_read_as_a_zero` is the guard.

**`abs(None)` raises inside the filter.** `_split` has to *call* the key to test
its result, so the None-safety has to be in the key: hence `_abs()`.

**Delta reports, and suggests nothing.** `_compare` prints its `config --set`
line only when the HIGH side wins, and high on the delta split is the NEAR
strike -- so a win for near strikes would have printed "widen the cushion", the
opposite of what the record just said. The cushion lesson already owns
`min_otm_cushion` and splits the right way round.

What is genuinely still missing is **IV rank**, and the reason is worth keeping:
a rank needs a year of implied-vol history per name and nothing collects one.
Absolute IV cannot tell 40% in a quiet name from 40% in a jumpy one. Realized
vol from the price history we already fetch is a *different* measurement and
labelling it "IV rank" would be the exact dishonesty this file exists to avoid.

`feature_gaps(journal)` now counts rather than describes: how many closed trades
predate each feature, because those rows are dropped from its comparison. The
sample that can speak to the screen is smaller than the sample overall until the
book turns over.


---

## 42. Every one of these was already written down

Five defects landed in one commit and they share a shape worth more than any of
them individually: **the lesson was recorded, the code obeyed it in one place,
and violated it in another.** A lesson in this file is not evidence the code
follows it. Grep for the second call site.

| the rule | honoured at | broken at |
|---|---|---|
| an approver is a person, never a permission | `Settings.auto_approver` | `--approver`, unvalidated |
| a decision is worth what the measurement under it is worth (38) | `cost_to_close`, after cb6e8a1 | expiry settlement, one branch above it |
| a machine check must not depend on prose (24) | the nginx marker, fixed | `"stop_loss" in close_reason` |
| record the stale symbols, do not re-derive them (21) | the alert banner | the position row |
| stale is a property of the reader (22) | `_mark_state` | `_heartbeat` |

### The approver field could carry a standing permission

**Corrected 2026-09-17 against the box.** The string
`approved_by: "viplove (blanket paper approval)"` is in the **local dev copy**
of `data/ledger.json` and has never been on the instance: all 18 live rows read
`agent (auto-approve, paper)`, because `auto_approve` has been on throughout and
`cmd_auto` always used `auto_approver()`. So this was a latent hole in the
`approve` path, not a corrupted live record -- check the box before writing down
what the record says.

The hole was real. That field is the entire audit trail for the human gate, and
`approve --approver` took any string at all. Read back later a standing
permission there asserts a human reviewed *this* trade; what it records is that
a human reviewed the idea of trades, once -- the same laundering this file is
forbidden from doing with consent, performed on the ledger instead, while the
method directly above the branch that accepted it already stated the rule.

`Settings.validate_approver` refuses it and names `auto_approve`, which is what
such a string is reaching for and which already exists and records an honest
marker.

### Settlement is the biggest number a row ever books

`mark_positions` settled expired positions against `spot or pos.mark_spot`. For
a vertical, settlement is max profit or max loss and nothing between, off one
comparison -- and an expired row is never re-priced, so booking it wrong is
permanent. `cb6e8a1` put an honesty guard on the option mark; this branch sits
above the guard and went around it. It now refuses to settle without a spot from
the current run, which only delays: the position stays open, stays out of
`fresh`, and settles on the next run that has one.

### Two stop-loss settings, and which one governs depends on the width

Extending 38.3, with the arithmetic rather than the one example. Stop one fires
at a loss of `credit x (mult - 1)`; the backstop at `pct x (width - credit)`.
They cross at **credit = 33% of width** (mult 2.0, pct 0.50):

* `$5` and `$10` widths clear the `$100` credit floor at 10-20% of width, so the
  credit multiple governs and fires near **30%** of max loss.
* `$1` and `$2.50` widths *cannot* clear `$100` without credit above 33% of
  width, so the backstop governs and fires at **50%**.

So the effective stop is not a constant across the book -- it is 30% or 50% of
max loss depending on which width the optimizer happened to pick, and nothing on
the page or in the ticket says which one is live for a given position. Recorded
as a fact, not acted on: changing either number is a strategy change, and the
closed record cannot support one.

### What the record can and cannot say about profitability

One closed trade, and 38 established it was priced wrong. `learning.py` will not
draw a conclusion under 8 closed trades with 4 per side and a 20-point win-rate
gap, which is correct. The four entry features landed at `13e3a28`, so only
trades opened after it can ever feed those lessons -- the two open positions
read `None` and always will. IV rank remains the one absolutely unrecorded
feature, and a put credit spread is a short-volatility position, so whether it
was sold into rich or cheap IV is the most load-bearing unmeasured fact about
every entry in this account.

### Arming state, unchanged this session

`paper_trading on`, `auto_approve on` (paper only), `auto_exit on`, `mode paper`.
Nothing was armed or disarmed. Standing deviation from the skill baseline, set by
the user before this session: per-trade human approval OFF for paper.

Still declined, still standing: changing the stop-loss parameters on the strength
of one trade. Still open: whether the GOOGL loss should count as a strategy
signal in `journal.outcomes` at all (38) -- flagged, record untouched.


---

## 43. A command with no `ssh` in it is a laptop command

The redeploy in `COMMANDS.md` was a bare fenced block:

```
cd ~/put-credit-spread-agent && git pull && sudo ./deploy/bootstrap.sh
```

Correct on the box, and it lived under a heading that said "Running on the
server" — which is a property of the *document*, not of the text you copied out
of it. Pasted into a laptop shell it failed twice on `cd`, and only because the
box's clone is `put-credit-spread-agent` while the laptop's is
`put-cedit-spread`. Same repo, two spellings, one of them a typo, and that typo
is the entire reason `sudo bootstrap.sh` did not run against a Mac.

Three things were relying on prose:

* **`bootstrap.sh` said "Runs ON the EC2 instance" in a comment** and checked
  only `id -u`. That is 15 again ("a comment is not a fact") and 24 again
  ("a machine check must not depend on prose") — the location requirement was
  stated in English and enforced by nothing. It now refuses on non-Linux. On
  macOS it would have died at `apt-get` anyway, which means the old safety was
  *luck about what is missing*, several steps in, after sudo was given.
* **The box's identity was nowhere in the repo.** `deploy/push.sh` documents
  `$1` as "an ssh host alias from ~/.ssh/config" and no such alias existed; the
  instance had been deployed from a session whose knowledge left with it. Host,
  user and key are now in `COMMANDS.md`.
* **Every box command was shaped so it could be pasted anywhere.** They now
  carry their own `ssh`, so the command is correct or it is nothing.

### Two DuckDNS names, one instance

`put-credit-spread.duckdns.org` and `ditm-robinhood.duckdns.org` are the same
EC2 box (`ip-172-31-8-235`), which also runs the DITM Robinhood agent. The first
SSH prints "the authenticity of host can't be established … this host key is
known by the following other names" — that is the *benign* form of the message
and means the key already matches a host you trust. The alarming form is a
conflicting key for a name you have used before.

`ssh-keygen -lf ~/.ssh/known_hosts` grouped by fingerprint is a machine
inventory, and it settled this in one command: three distinct boxes behind six
names, and the PCS box was neither of the two aliases in `~/.ssh/config`. Worth
reaching for before guessing at keys.

Login is `ubuntu` with `~/.ssh/rho-agent.pem` — the key pair is shared with
`priya-live`, which is a *different* instance. An EC2 key pair serving several
instances is why trying the keys you already have is a reasonable first move.

---

## 44. Thirteen closed trades, and the payoff needs 68% winners

First read of a record big enough to say anything. Box, 2026-09-17: 13 closed,
5 open, **+$11.79** realised on $3,000. `learning.lessons()` clears its floor of
8 and reports nothing actionable on cushion or sector -- and that is not the
interesting part.

**8 wins, 5 losses, 61.5%. Break-even for this payoff is 67.9%.**

| | per trade |
|---|---|
| average win | **+0.55 x credit** (take-profit at 50% of max credit, capturing 55%) |
| average loss | **-1.16 x credit** (stop at 2x credit, i.e. buy back for twice what was sold) |
| break-even win rate | **67.9%** measured, **66.7%** from the settings alone |
| actual | 61.5% |

The two exit settings *set* that number. Booking at 50% of the credit caps a win
near half the credit while the stop lets a loss run to a full credit and past
it, so the configuration needs two winners in three to stand still, by
arithmetic and before any judgment about entries. Dropping the one pre-guard
trade (GOOGL, 38) still leaves break-even at 64.6% against 61.5% delivered.

Nothing in the agent displays this. The dashboard shows win rate and realised
P&L, `readiness` gates on `min_win_rate_for_live = 0.60` -- a threshold **below**
this configuration's own break-even -- and no screen anywhere says what win rate
the current settings require. That is the gap worth closing before any parameter
is touched: an account can pass its go-live gate while running a negative
expectancy, and this one nearly did.

### The stop fires on volatility, not only on direction

Three of the five stops closed with the short strike **never breached**:

| | cushion at close | stop |
|---|---|---|
| GOOGL | +3.1% | 2.8x credit (pre-guard, see 38) |
| RDDT | +0.4% | 2.1x |
| STX | **+5.4%** | backstop, 52% of max loss |
| BA | -0.7% | 2.0x |
| CLX | -6.0% | 2.1x |

Only BA and CLX were actually in trouble. `cb6e8a1` reached the box on
**2026-09-03**, so RDDT, STX, BA and CLX all stopped on marks that passed the
two-sided-book test -- these are not the GOOGL plumbing failure repeating. A
short vertical is short vega: an IV expansion can double the buy-back price with
the underlying untouched, and a credit-multiple stop cannot tell that from a
directional loss. STX is also the live confirmation of 42's crossover -- 42% of
width in credit, so the 50%-of-max-loss backstop governed, exactly as predicted.

### GOOG and GOOGL are one issuer and no cap sees it

Both were open 2026-09-09 to 2026-09-14, $703.74 of collateral on Alphabet.
`max_positions_per_ticker` counts symbols and `max_positions_per_sector` counts
GICS labels (23); two share classes of one company defeat both. They happened to
win. The exposure was single-name and invisible.

### Every deploy was deleting the health record

`/opt/pcs/data/health.json` did not exist on the box. `bootstrap.sh` rsyncs with
`--delete` and its exclude list names ledger, proposals, settings, snapshots,
last_screen, watchlist and journal -- not `health.json`. So the telemetry built
in 21, 29 and 33 was wiped on every redeploy since it shipped, and **the
cold-start fallback from 29 hid it**: absent record, `ledger_evidence()` fills
in, page looks healthy. A graceful degradation that never announces itself is
indistinguishable from the thing working. Added to both exclude lists.

---

## 45. The dashboard described the break-even for weeks instead of computing it

Under the history cards sat a note: *a high win rate at low capture and a few
full-size stops is a losing book that reads as a winning one.* It names the
failure exactly and leaves the reader to do the arithmetic. Nobody does.
`pcs/expectancy.py` does it instead. Two settings fix the number and nothing
else touches it:

    win  = take_profit_pct           x credit
    loss = (stop_loss_multiple - 1)  x credit
    break-even = loss / (loss + win)

66.7% at the shipped defaults. **No screen, no entry filter and no amount of
stock-picking moves it** -- they change only how often it is cleared. Anything
proposed as making the agent "more profitable" that does not move `win`, `loss`
or the win rate is not an improvement, and this is the module to check it
against.

Three design points worth keeping:

- **Credit multiples, not dollars.** The book sizes from $98 to $420 of
  premium; dollars are not comparable across it. Same normalisation as
  `learning.Outcome.capture`, and against `credit_open x 100 x contracts`
  (gross) rather than `credit_dollars` (net of fees), because the take-profit
  rule is written against the gross figure.
- **One-sided books return `None`.** Eight wins and no losses has not measured
  what a loss costs. Reporting a break-even of 0% there would read as *any win
  rate is fine* -- the most dangerous output the module could produce.
- **A measured payoff beats a derived one.** The exits overshoot in both
  directions: take-profit fires on the first mark at or above target so it
  books 55% against a 50% rule, and a stop set at 2.0x fills at 2.1x or 2.8x.
  Only one of those drifts is in the account's favour. Live: 0.548x / 1.158x
  against 0.50x / 1.00x on paper, so the real requirement was 67.9%, not 66.7%.

### The go-live gate could certify a losing configuration

`min_win_rate_for_live` was 0.60 with a break-even of 0.667. A book that met
the bar exactly would lose money at exactly the rate it was certified for.
`readiness.assess` now carries a blocking criterion comparing the two, so the
bar cannot be set underneath its own payoff. This is the same shape as 42: a
rule stated in one place and contradicted by a number set somewhere else.

---

## 46. The per-name cap counted tickers, and a company is not a ticker

GOOG and GOOGL were open together 2026-09-09 to 2026-09-14, $703.74 of a $2,400
budget on Alphabet. Two caps existed to prevent exactly that and neither fired:
`max_positions_per_ticker` counted symbols, `max_positions_per_sector` counted
GICS labels, and none of the 23 labels separates a company from itself.

The cap was not wrong about its intent -- its own docstring reads *"positions
in one NAME, not contracts"*. **The number did not change; the unit did.** Same
shape as 42 and 45: a rule stated correctly in one place and measured wrongly
in another.

`universe.issuer_key(name, symbol)` derives the company from the constituent
table's own `name` column, which carries the class in parentheses
("Alphabet Inc. (Class A)"). Across the 503 cached S&P 500 rows it merges
Alphabet, Fox and News Corp and **nothing else** -- 500 issuers, no false
merge, which is the half that matters, because a false merge refuses a trade
the account was entitled to take. A six-symbol table backs it up for rows whose
name did not survive, and `screener`'s "Unknown" sentinel is explicitly not a
company: two Unknowns must never merge.

Three things worth keeping:

- **One derivation point.** `open_approved` takes the company NAME and derives
  the key itself. An earlier draft took a ready-made key, and the watchlist
  derived its own -- the test caught them disagreeing within the hour. A cap
  keyed one way and counted the other silently stops binding, which is the
  original bug reproduced one level up.
- **Enforced at the fill, not only at proposal time.** Same reason as the
  re-entry cooldown: a ticket carries the verdict it was given when it was
  written, and GOOG can be proposed at 13:00 against an empty book and approved
  at 15:00 after GOOGL filled. New exception `paper_broker.Concentrated`.
- **One counter.** `Ledger.ticker_counts()` is gone rather than kept alongside
  `issuer_counts()`. Two counters is how the next call site gets missed.

`Position.issuer` is empty on all 18 rows in the live ledger; every reader
falls back to `symbol`, which is the correct key for every single-class name,
so the history reads exactly as before and only new fills are grouped.
**Wrong -- corrected in 49:** lookups key by the company NAME, so bare `symbol`
never met them, and a legacy row counted against no company at all.

**What this does NOT do:** the box has `max_positions_per_ticker = 5`, so the
Alphabet pair would still have been allowed. The cap now measures the right
thing; what number it should be is a risk decision and not one to infer from a
13-trade record.

---

## 47. Three of the first five losses stopped on a strike that was never touched

GOOGL 3.1% clear, RDDT 0.4%, STX 5.4%. Only BA (-0.7%) and CLX (-6.0%) were in
trouble. `close_reason` said `stop_loss` for all five, and the dashboard
rendered all five identically -- which is the record failing at its job, since
a stop on an untouched strike and a stop on a thesis that broke are different
trades with different fixes.

The suspect is vega. **A short vertical is short vega:** an IV expansion can
double the buy-back price with the underlying untouched, and a
credit-multiple stop cannot tell that from a directional loss. The record could
not settle it, because the only vol reading it held was `iv_at_open`, from the
day of the fill. `mark_iv` now goes onto the position at every mark, so a row
that closes carries the vol at the mark the exit acted on, and
`Position.iv_change` is the fraction between the two. `None` when either end is
missing, never zero -- "vol did not move" and "nobody wrote it down" are
different facts and only one of them is evidence.

Two things this buys and one it does not:

- The closed table has an **at exit** column: how far the stock was from the
  short strike when the row closed, and what vol had done. Both read
  `mark_spot` / `mark_iv`, which are refreshed on every mark, so on a closed
  row they are the reading the exit acted on -- no new plumbing, and the
  cushion half works retrospectively on all 13 existing rows.
- The history note carries the count: *3 of 5 losing trades closed with the
  short strike never breached*. Counted off losses rather than `close_action`,
  which shipped after those rows.
- It does **not** answer the question yet. `mark_iv` is null on every existing
  row and can only be learned from positions marked after this shipped, same
  as the entry features in `13e3a28`. Until several stops carry both readings
  there is nothing to conclude, and no stop parameter should be changed on the
  strength of a hypothesis the record cannot yet test.

**IV rank remains unrecorded** -- where a name's IV sits in its own year, as
opposed to its absolute level. STX opened at 65% IV and CLX at 36%; without a
rank those two numbers are not comparable, and selling a 65% IV that is cheap
for STX is a different trade from selling it because it is high in absolute
terms.

---

## 48. The first `push.sh` deploy found a bug every hand-run deploy had hidden

`./deploy/push.sh pcs` on 2026-09-20 shipped the code, installed it, started the
timers and ran the suite on the box -- then died in the web section:

    from pcs import authd; print(authd.error_page())
    ModuleNotFoundError: No module named 'pcs'

`python -c` puts the **current directory** on `sys.path`, not the app's. Every
deploy before this one was run by hand as `cd ~/put-credit-spread-agent && sudo
./deploy/bootstrap.sh`, and that directory contains a `pcs/` package -- so the
import had been resolving against the clone by accident for the life of the
script. `push.sh` runs `bash /tmp/pcs-src/deploy/bootstrap.sh` from the login
directory, where there is no `pcs/`, and the accident stopped working. Every
other python call in the script passes `$APP/run.py` as a script path, which
puts the app on `sys.path` by itself; this was the only `-c`.

**Same shape as 43**: a command that worked because of where it happened to be
run, not because it was correct. The fix is `( cd "$APP" && ... )`.

The second defect is the worse one. The line was

    "$APP/.venv/bin/python" -c '...' > "$WEB/50x.html"

and the shell truncates the target **before** running the command, so a failure
destroyed a good file it had no business touching -- `/var/www/pcs/50x.html`
went to 0 bytes. Same shape as the health.json wipe in 44: state destroyed by a
step that was only supposed to write it. It now builds beside the target,
checks the result is non-empty, and moves it into place.

### What the aborted deploy actually left behind

Worth recording because the traceback looks worse than the outcome. Sections
1-5 had already completed, so: `/opt/pcs` carried the new code, all three
timers were live, the site answered 302, and 474 tests passed on the box. Only
the web section was cut short -- an empty 50x page (served only if `pcs-authd`
is down) and a skipped `push.sh` verification step. The nginx vhost was not
rewritten and did not need to be.

**`~/put-credit-spread-agent` on the box stays behind on purpose.** `push.sh`
syncs the laptop's working tree to `/tmp/pcs-src` and installs from there;
the box's own clone is not in that path and is not what runs. Checking
`git log` in it says nothing about what is deployed -- read `/opt/pcs`.

---

## 49. Two timers write one ledger, and the later save won

`propose` fires at 10:15 ET and `mark` at :05, :20, :35 and :50. `propose`
loaded the ledger before sizing -- minutes of option-chain and earnings calls --
and saved after it opened. A stop taken by the 10:20 mark was written to disk,
and then `propose` saved the copy it had loaded at 10:15: the position open
again, the debit back in cash, the `position_closed` event gone. The journal,
written by the mark, had already booked the loss, so the two records disagreed
and nothing raised. `tests/test_ledger_lock.py` reconstructs the sequence; on
the old code it fails with *"propose saved over the stop the mark had taken"*.

**Checked on the box 2026-10-09 (see 50): no sign it ever happened.** The
signature would be a loss in the journal for a position the ledger still shows
open, or closed later at a different debit; all 21 journal outcomes match
their ledger rows.

The fix is `ledger.locked(settings)`: an exclusive `flock` on a sibling
`data/ledger.json.lock`, the ledger loaded inside it, saved before it is
released. `propose` (from writing proposals through auto-open), `approve`,
`mark`, `close` and `reject` all write under it. A writer that cannot get the
lock in 300 s raises `LedgerBusy` rather than going ahead unlocked; the next
timer is fifteen minutes away. `status`, `dashboard`, `watch`, `learn`,
`doctor` and `propose`'s early sizing read stay unlocked -- they never save,
and both `ledger.json` and `proposals.json` are now written by
`config.atomic_write` (sibling temp file, then rename), so a reader sees one
whole version or the other. Before this, a save killed part-way left a
truncated ledger that every later command failed to parse.

The lock file is box state, so it went into `.gitignore` and both rsync
excludes. Writing it down showed `health.json` had been in both excludes since
44 and never in `.gitignore`. The three lists are now a test.

### A placeholder is not a reading

47 made `iv_at_open` and `mark_iv` the fields that would answer "did those
stops fire on vol?". Both had an `... or None` guard against a fabricated value,
and both only ever caught a zero:

- the optimizer used a flat 0.30 when a chain quoted no IV, so the placeholder
  became the spread's IV, its delta, its POP, and then `iv_at_open`;
- a short leg with no IV borrowed the long leg's -- another strike;
- the model provider stamps its 0.30 input on every strike, and every mark
  stored it as `mark_iv`.

The test that claimed to cover this fed in 0.0, a value no provider produces.
Now no IV means no delta and POP `unavailable`; `Spread.iv_measured` is true
only for a live chain quoting the short leg itself, and only that reaches
`iv_at_open`; `mark_iv` is written only off a live chain. Existing rows were
not rewritten. An `iv_at_open` of exactly `0.3` on one of them is most likely
the placeholder, not a measurement.

### The cooldown moved to companies, and the old rows came with it

46 moved the per-name cap to companies and left the re-entry cooldown (40) on
tickers, so a GOOGL stop left GOOG free to open the same morning. The cooldown
is now keyed by `Position.company_key` at the fill, in `risk.check`, and on the
watchlist, which had been passing no issuer at all.

That surfaced the error in 46. Every lookup keys a name by
`issuer_key(name)` -- `appleinc` for AAPL -- while a row with an empty
`issuer` fell back to bare `AAPL`. The two never met, so an open legacy row did
not count against its own company's cap. `Position.company_key` resolves an
empty issuer through `universe.issuer_key_for`, which reads the cached
constituent CSV, so an old row keys exactly as a new fill of the same name.
Same shape as 42: a rule honoured at one call site and missed at the next.

### A close costs the same whoever takes it

Opening charges both legs plus commission. `apply_exits` charged one leg's
$0.06 and no commission, and `close` re-charged whatever the opening had cost.
With commission at zero, every automated exit booked $0.06 per contract too
little; with a commission set, most of a round trip. Both now call
`Settings.spread_fees`, which the optimizer uses too. Closed rows were not
rewritten.

### Half days

NYSE closes at 13:00 ET on 2026-11-27, 2026-12-24 and 2027-11-26 (ICE, *"NYSE
Group Announces 2025, 2026 and 2027 Holiday and Early Closings Calendar"*; the
July holidays in 2026 and 2027 are full closures, not half days). The clock
assumed 16:00 on every weekday, so on a half day the 13:05-15:50 marks would
have graded a book that had stopped trading as `live`, and `apply_exits` --
whose only market gate is `is_open` -- would have fired stops on it.
`session.EARLY_CLOSES` and `close_time()` fix that; the timers still run those
afternoons, mark, and hold any exit until the next session.

The holiday and half-day tables end 2027-12-31. Past that nothing fails -- the
clock is just wrong. `doctor` now warns from 60 days out
(`session.calendar_ends()`).

### Still open

- An expired position settles at the spot of the first run after expiry, not
  at the expiry close. Listed, not fixed.
- `close --debit` still fetches a chain before it looks at the override, so the
  override cannot help when the fetch itself is what failed.
- The ranking question from 44 -- 61.5% winners against a 67.9% break-even,
  with ROC ranking favouring the strike nearest the minimum cushion -- is a
  strategy decision. Not acted on.

### Arming state, unchanged by this change

No setting was read or written on the box. `paper_trading`, `auto_approve`
(paper only), `auto_exit` and `mode` are as 42 records them.

---

## 50. Twenty-one closed trades, and three ways the plumbing shapes them

Read off the box 2026-10-09, read-only (no file written there). The three
plumbing faults below are fixed in the same change, each with a test that
fails on the code before it: `tests/test_learning.py` (opening range),
`tests/test_expiration.py` (probe), `tests/test_balance.py` (cap at fill).

| | |
|---|---|
| closed | 21: 14 take-profit wins, 7 stop-loss losses (66.7%) |
| realised | **-$100.45** (wins +$1,797.56, losses -$1,898.01) |
| average win / loss | +0.546 x credit ($128.40) / -1.148 x credit ($271.14) |
| break-even | **67.8%** -- one average loss costs 2.1 average wins |
| largest loss | APP, 15 wide, -$666.43 = 5.2 average wins |
| since 44 | 8 trades, 6W/2L, about -$112 -- APP alone |

Every win was a take-profit and every loss a stop; nothing has expired or run
to max loss.

### Every quarantine on the box came from the opening range

117 of 118 `mark_failed` faults were recorded at the 09:35 (106) and 09:50 (11)
marks, nearly all "short leg quoted one-sided (bid 0, ask 0, no broker mark)".
The one exception is a CSCO rate limit at 15:05 on 2026-09-09. All five live
quarantines (AVGO, ORCL, UBER, VST from 2026-10-05; APP from 2026-10-07) were
built **entirely** from those two marks -- zero faults from after 10:00 --
and AVGO, UBER and VST had just closed normally at take-profit.

Only held positions are marked, so `learning_fault_threshold = 3` in 7 days
means *any name held across three opening ranges is benched from proposals for
5 days*, under a reason ("the chain will not price this name reliably") that
the later marks contradict. Exits are unaffected -- `blocked_symbols` is read
only by the proposal path -- and the mark itself correctly treats those
positions as not fresh. The fault is the counting: a one-sided book inside the
window `session` already grades as untrustworthy is the session, not the
symbol.

**Fixed:** `self_repair` skips a `mark_failed` fault stamped inside the opening
range (`session.state_for`, so it follows `opening_settle_minutes`). The fault
is still recorded. It is decided from the stamp at count time, not at record
time, because the rows already in the journal would otherwise have re-benched
UBER and ORCL on 2026-10-10 as their quarantines expired. Replayed over the
box's journal from 2026-09-04, the new rule creates no quarantine at all.
The five live ones did not all get to expire under it: see "The deploy that
stopped after the timers" below -- a redeploy of the OLD code re-benched ORCL
and UBER until 2026-10-15 before the new code arrived. `record_fault` stamps
the local clock with no zone, and `_in_opening_range` reads it back as local,
so a journal is only interpreted correctly on the machine that wrote it --
true of the box's.

### The expiration probe can still take a run down

`resolve_batch_expiration` probes the first five shortlisted names and its
docstring says the result is display-only. `cmd_propose` returns 1 when it
comes back `None`, before `size_candidates` -- which resolves per symbol and
needs no batch date -- ever runs. 2026-10-06: CSGP, PODD, ZTS, APTV and HONA
list monthlies only (2026-10-16, then 2026-11-20 at 45 DTE), so 107 other names,
weeklies among them, were never priced. Once in 30 runs.

**Fixed:** a probe that finds nothing prints that and the run carries on; every
name resolves its own expiration in `size_candidates`, as 8 intended. A run in
which no name lists one now ends with each name's own "no listed expiration"
line and a `propose` health record, instead of exit 1 and no record.

### A ticket sized at the cap fills over it

`optimizer.build_spreads` checks `max_collateral_per_trade` at the ticket
credit (session slippage, 0.15 live). `simulated_fill_credit` fills at
`max(paper_slippage_frac, slip + 0.10)` = 0.25 -- by design never better than
the ticket. So any ticket within roughly a tenth of `(mid - nat) x 100` of
$1,000 passes the screen and is refused at the fill. TTWO 2026-10-08: $984.87
sized on $5.15, filled $4.97, $1,003.12, held; it was the only clear proposal
that day.

**Fixed:** the fill formula moved to `optimizer.paper_fill_credit` (with
`PAPER_EXTRA_HAIRCUT`), and `paper_broker.simulated_fill_credit` calls it, so
there is one formula. `build_spreads` rejects a spread whose collateral at that
fill exceeds the cap ("collateral $N at the expected fill > $1,000"); the
ticket's own `collateral`, ROC and ranking are unchanged. It applies in live
mode too, where it is stricter than a limit order strictly needs -- it only
removes tickets within a few dollars of the cap.

### The funnel

Since 2026-09-28, 569 of 937 per-name rejections (61%) were *no listed
expiration in the 28-38 DTE window* -- monthly-only names, reachable on roughly
a third of days. Next: short-leg OI 184, long-leg OI 62, earnings 48, leg
bid/ask 48. Daily yield fell from 10-29 sized names in mid-September to 2-8 in
October. `dte_window` is in `STRATEGY`, skill-fixed, so changing it is a
strategy decision, not a setting.

### Strike distance is the break-even

Short delta at open (n = 15): min .29, median .33, max .38 -- a market-implied
~67% chance of expiring out of the money, the same as the 67.8% break-even.
Cushion at open 3.1-5.9% (median 3.9%), which is 0.19-0.47 (median 0.34) of a
30-day one-sigma move. `min_otm_cushion` is a flat percentage, so on high-IV
names it sits closer in sigma terms; ROC ranking then prefers the strike
nearest that floor (44, 49). Strategy decision, not acted on.

### Width sets the dollar loss

| width | n | W | net | avg collateral |
|---|---|---|---|---|
| 4 | 2 | 2 | +$103.71 | $297 |
| 5 | 10 | 6 | -$145.29 | $350 |
| 10 | 8 | 6 | +$607.56 | $653 |
| 15 | 1 | 0 | -$666.43 | $961 |

The only per-trade dollar limit is the $1,000 collateral cap (33% of the
account). `candidate_widths` in `STRATEGY` is read by nothing: the optimizer
takes every listed width up to `MAX_WIDTH_SEARCH = 15`.

### The vega-stop hypothesis, first two readings

The first two stops to carry `mark_iv` were directional: RDDT IV .55 -> .54,
short strike breached by 1.9%; APP .59 -> .54, breached by 8.2%. Vol fell into
both. No support for 47's hypothesis yet, n = 2 -- not a reason to loosen
anything.

### What `lessons()` reports (tentative, n = 14-21)

Pullback below the 50dma under 5.5%: 88% of 8 won (avg +$82.71); over 5.5%:
33% of 6 (avg -$172.63). 15-25% off the 52w high: 80% of 5; 25%+ off: 56% of 9.
Communication Services 43% of 7; IT 88% of 8. Cushion and IV split nothing.
7 of 21 rows predate the entry features; IV rank is still not collected.
RDDT re-entered 6 days after a loss (cooldown 5) and lost again, n = 1.

### Checked after the 2026-10-07 deploy (8d5f1bb, b560f9e)

- 514 tests pass on the box (Python 3.14.4). pytest `addopts = "-q"`, so
  `pytest -q` prints dots only, no summary line -- count them.
- Lock file created; no errors in 1 propose, 48 mark, 34 watch runs.
- No `iv_at_open == 0.3` rows: the placeholder never reached the record.
- UBER take-profit closing fee 0.12 = `spread_fees(1)`.
- Half-day clock correct on the box; `calendar_ends` 2027-12-31.
- 2026-10-08 propose finished 10:16 ET, before the 10:20 mark: the lock has
  not yet been contended.

Laptop facts: local time is CDT (ET = local + 1h) and `gh` is not installed.

### The deploy that stopped after the timers

2026-10-10, two `push.sh` runs. The first died at `== packages`:
`unattended-upgr` held the dpkg lock. Harmless -- apt runs before the rsync
into `/opt/pcs`, under `set -e`. Retry once the process is gone.

The second printed the timer table and nothing after it: no self-check, no
nginx or login-service steps, no `== verifying on the box`. Cause:
`systemctl list-timers ... | head -4` under `set -euo pipefail`. When `head`
exits before systemctl finishes writing, systemctl dies of SIGPIPE (141),
pipefail makes that the line's status, and `set -e` ends `bootstrap.sh`;
`push.sh`, also `set -e`, stops with it. 1 failure in 30 when the line was
run alone on the box. Code and timers were already in place by then. Every
`| head` in both scripts is now `sed -n 1,Np` (or `awk 'NR == 1'`), which reads
to the end; `tests/test_deploy_scripts.py` keeps it that way. 0 in 30 for
the sed form.

That same run also shipped no new code: `push.sh` syncs the main checkout's
working tree, and the main checkout had not pulled the new commit yet. Read
`/opt/pcs` (grep for the change, count the test dots) to tell what is
deployed -- not the deploy's exit, and not any clone.

**A deploy runs self-repair, with whatever code it ships.** `bootstrap.sh`'s
account-state step runs `run.py status`, `learn` and `dashboard`, and `learn`
calls `self_repair`. The 01:36:51 UTC run on 2026-10-10 shipped b560f9e again,
so the OLD rule ran: AVGO, ORCL, UBER and VST expired, and ORCL and UBER were
benched again until 2026-10-15 on the same opening-range faults -- the re-bench
this section predicted for Monday's first mark, two days early. The new code
does not lift a quarantine (nothing does; they only expire), and a dry run on
the box shows both expiring 10-15 and none renewed. The deploy that shipped
db27f29 came a few minutes later.

Quarantine dates are `dt.date.today()` on the box, which is UTC: a quarantine
"until 2026-10-10" ends at 20:00 ET on 10-09, not at the next session.

### Arming state, unchanged

Settings were read on the box, not written. `paper_trading`, `auto_approve`
(paper only), `auto_exit` and `mode` are as 42 records them.

---

## 51. The record since the code that picks trades last changed

The History tab now leads with **Since trade selection last changed**, above
the whole record. 21 closed trades were picked by code that kept changing
underneath them, and 2026-10-10 alone changed which names can be proposed,
which runs price anything and which tickets clear the cap. Whether the current
code makes money is a question about the trades it picked.

- `expectancy.SELECTION_CHANGES`: `(day, commit, what changed)`, oldest first.
  The latest row is `("2026-10-10", "db27f29", ...)`.
- `opened_since(positions, day)` filters by **open date** (`opened_at[:10] >=
  day`). A position opened before the change and closed after it is left out,
  because the old code picked it. That leaves out ORCL 133/130, opened before
  10-10, so the panel reads "Nothing the current code picked has closed yet"
  until a post-change trade closes.
- The win rate shows next to its **Wilson 95% range**
  (`win_rate_interval`), not on its own. 14 of 21 is 45%-83%, so twenty-one
  trades cannot say whether the true rate sits above or below the 68%
  break-even. 2 of 2 is 34%-100%.
- The break-even comes from the **whole** record (`expectancy.summary`). The
  exits did not change, so every closed trade measures what a win and a loss
  cost. The verdict says "above the break-even even at the low end" or "under
  it even at the high end" only when the whole range clears or misses it.
  Otherwise it says "too few trades to tell yet".

### Where going live stood on 2026-10-10

Facts, not a decision. `readiness.assess` on the box: 5 of 9 checks pass and 3
blocking checks are outstanding:
- the broker's spread permission was never checked by the agent;
- buying power is recorded as $0, and that reading is stale;
- the go-live bar sits at 60%, under the record's own 68% break-even (44).

The record so far: the first position opened 2026-09-01, and 21 closed trades
give 14 wins and 7 losses (66.7%), realised -$100.45. A win averages 0.546x the
credit and a loss 1.148x. Live mode places no order in code: it prints tickets
for a human to place, and `auto_exit` acts in paper mode only.

### Rendering the dashboard locally

The browser pane cannot click or screenshot a `file://` page. To check a page
visually, render it into the scratchpad and serve that folder with `python3 -m
http.server` on 127.0.0.1. Keep any `.claude/launch.json` made for this out of
the commit. On the Mac, `python` is not on PATH, so run `python3`.

### Arming state, unchanged

Settings were read on the box on 2026-10-10 (Friday 22:05 ET; nothing had run
since the deploy), not written. `paper_trading`, `auto_approve` (paper only),
`auto_exit` and `mode` are as 42 records them.

---

## 52. The doctor reported a healthy box as faulty

2026-10-13, a health check of the live box. Every unit was fine -- `health.json`
held 400 runs with none failed, no pcs unit was in `systemctl --failed`, the
journals had no warnings -- yet `./run.py doctor` printed four warnings that
were not faults. Two checks were wrong.

- **"enabled but has never run", for all three services.** `_svc` read
  `ActiveEnterTimestamp`. `pcs-propose`, `pcs-mark` and `pcs-watch` are
  `Type=oneshot`, which go activating -> inactive and never enter `active`, so
  that field is empty however often they have run. It now reads
  `ExecMainStartTimestamp`, which a oneshot keeps. A unit that never fired has
  neither, and still warns.
- **"pending but auto-approve is on".** It fired when any ticket was pending
  and any ticket in the run was clear. On 10-13 HD opened and AVGO stayed
  pending because Information Technology was at its cap of 2, which is the risk
  layer working. It now counts only pending tickets that are themselves clear of
  the limits, the ones a gate inside `open_approved` could have refused.

Not changed: the `propose.log` lines at the bottom are the last three `HELD`,
`Error` or `Traceback` lines in the whole file, whatever their age, so they can
be weeks old. Read the date in the line before acting on one.

To tell a healthy box from a faulty one without the doctor: `systemctl show
<unit> -p Result -p ExecMainStartTimestamp`, the run counts in
`data/health.json`, and `HELD` lines under the latest `=== ... propose ===`
header in `logs/propose.log`.

---

## 53. Two limits were checked on the ticket and broken at the fill

2026-10-16, found by a health check of the box that night. The 14:16
propose run opened five tickets (LEN, LULU, ADBE, C, DAL) with ORCL 133/130 already
open. Both breaches were in the ledger:

- **Portfolio collateral $2,412.37 against the $2,400 cap.** `risk.check`
  summed the batch's TICKETS: $162.50 open + $2,234.14 sized = $2,396.64.
  Every paper fill is worse than its ticket, and the five fills came to
  $2,249.87, $15.73 more.
- **DAL 75/70 filled at $0.9887, $98.87 a contract, under the $100 floor.**
  `build_spreads` holds `credit_dollars` (the ticket credit, $100.32) to the
  floor, never the fill. Its ticket already warned that the natural credit was
  $88.

**Fixed:** `open_approved` re-checks both at the fill, after the per-trade
cap. The portfolio check is `ledger.collateral_held` plus this fill's
collateral, so fills earlier in the same batch count; it raises
`InsufficientFunds`. The floor is the fill credit x 100 per contract, gross of
fees (the same figure the screen uses), against
`settings.strategy().min_credit_per_trade`; it raises the new
`BelowCreditFloor`. On 10-16 both would have held DAL and only DAL. A refusal
leaves the ledger untouched and the ticket pending. `tests/test_fill_caps.py`
pins it.

- `cmd_approve` now catches every `OpenBlocked`. Its list had left out
  `Concentrated`, which came out as a traceback.
- Three tests in `tests/test_ledger_and_broker.py` opened without `sess=` and
  so filled in the wall-clock session: they passed in market hours and failed
  after it (stale, slippage 0.5). They now pass `live_session`.

- `expectancy.SELECTION_CHANGES` has a row for it, `("2026-10-17", "578d3cc",
  ...)`: a refused fill changes which trades open. It is dated the first day
  no old-code fill can land on, so the five 10-16 positions are out of the
  History tab's since-the-change record (51). DAL would not have opened. The
  two since-change tests that pinned the 10-10 row and a 10-12 "after" date
  now read the latest row.

### The screen side, 2026-10-17

The fill-time checks alone left a ticket within a few dollars of either limit
proposed, auto-approved and held at the fill on every run, as TTWO was before
50. The screen now checks both at the expected fill as well:

- `build_spreads` rejects a spread whose credit at the expected fill is under
  the floor ("credit $N at the expected fill < $100"), beside the cap check
  at the same fill.
- `optimizer.fill_collateral(sp, sess, settings, contracts)` is the collateral
  at the paper fill, by the broker's own formula. `risk.check` sizes the
  portfolio cap and the balance with it when it has a session, and
  `build_proposals` uses it for each `pending` entry and the multi-lot
  per-trade check. With no session it is the ticket's figure, so a caller
  with no session gets the old behaviour.
- Replayed on the box's 10-16 tickets and ledger: the expected fills match
  the recorded fills to the cent for LEN, LULU, ADBE and C, and DAL's
  expected fill is its real $98.87, so DAL is not proposed. The batch clears
  four, at $2,011.24 with ORCL. PNC and PEP stay blocked by the portfolio
  cap. Another DAL strike pair might have qualified instead; the chain from
  that moment was not saved, so that is unknown.

It has its own `SELECTION_CHANGES` row, `("2026-10-17", "4f48029", ...)`, after
the fill-time one: it changes which tickets are proposed.

The fill checks stay. They now catch a fill whose session is worse than the
one the ticket was sized in, such as a manual approve once the quotes have gone
stale (0.60 of the gap against 0.25 live). `tests/test_fill_caps.py` covers
both sides.

**Not a fault:** ORCL's mark of $1.85 on the 133/130 with spot $128.50 (under
both strikes, 14 DTE) looks wrong, because the spread would settle at $3.00
today. It is fair at a 65% IV: the long 130 put sits nearer the money and
carries more time value than the short 133, so the pair is worth under its
width until expiry.

### Arming state, unchanged

Read on the box on 2026-10-17 (Saturday 03:20 UTC), not written.
`paper_trading`, `auto_approve` (paper only) and `auto_exit` are as 42 records
them.

---

## 54. The deploy's own tests wrote over the box's proposals

2026-10-17, found by the check after the 13d510f deploy. The box's
`data/proposals.json` held one ticket, `P-TEST` on `TST`, approved, instead of
the ten from 10-16. Nothing on the box makes that ticket; the test suite does.

- `push.sh` and `bootstrap.sh` both run pytest inside `/opt/pcs` as `pcs`, the
  user the timers run as. Every path in `config` was fixed to `ROOT`, the
  checkout, so on the box ROOT *was* the live install. Two tests in
  `test_config_overrides.py` call `run._auto_open`, which saves proposals to
  `proposer.save`'s default path. Every deploy since 09-01 did this.
- Only `proposals.json` was hit. The ledger, the journal (no TST outcome), the
  settings and the served page (no TST) were checked and were intact. The next
  propose run replaces the file; it was left for Monday's 14:15 UTC run, not
  cleared by hand.
- Other live paths were exposed the same way. `/var/www/pcs` and
  `/var/lib/pcs` exist on the box, so `WEB_INDEX` was the served page for any
  `render` a test did not monkeypatch, and `STATE_DIR` held the live
  `overrides.json`. The dashboard tests read the live `watchlist.json` through
  `watchlist.load()`, so they also depended on the box's data.
  `authd.SECRET_FILE` is `/var/lib/pcs/session.key` unless `PCS_SESSION_KEY`
  says otherwise, and a test that wrote it would sign every viewer out.

**Fixed:** `config.RUNTIME` is `PCS_RUNTIME` or ROOT. `DATA_DIR`, `LOG_DIR`
and `DASHBOARD_HTML` hang off it. `SP500_CSV` stays under ROOT because it is a
tracked input. `tests/conftest.py` makes a temporary folder and sets
`PCS_RUNTIME`, a nonexistent `PCS_WEB_ROOT` and `PCS_STATE_DIR` (which is how
`config` reads "nothing serves this"), and `PCS_SESSION_KEY` and
`PCS_VIEWERS`, all before `pcs` is imported, because several modules bind a
path as a parameter default at def time. It also drops `PCS_OVERRIDES`.
Production code reads no new variable unless it is set, so the box runs as
before.

`tests/test_live_paths.py` walks every `pcs` module and `run.py`, collects each
module-level `Path` and each `Path` parameter default, and fails on any outside
RUNTIME other than ROOT and `SP500_CSV`. It also checks that it found
`proposer.save(path)`, so the walk cannot pass by seeing nothing.
`test_deploy_excludes` now measures box state relative to RUNTIME. Locally, the
checkout's `data/proposals.json` stopped changing once this was in.

### The doctor's fix for a full book

The position-count and collateral-cap BLOCKs used to offer `./run.py config
--set max_...=N`. That is advice to loosen the cap that just did its job. On
10-16 the book was over $2,400 only because the fill had not been held to the
cap (53). Both now say the block clears as positions close or expire, and that
the exits keep running. A limit is changed as a decision, not as the fix for
reaching it. `test_a_full_book_is_never_fixed_by_raising_the_limit` pins it.

### Verifying a deploy

`push.sh` leaves no version marker on the box, and its test step prints only
the last three lines. With `addopts = "-q"` plus its own `-q`, a passing run
prints dots and no count, so the deploy output cannot show how many tests ran.
To check what landed, compare `shasum` of each tracked file on the box with
`git ls-files` on main, then run pytest on the box with `-q -o addopts=` to get
the count. The first 13d510f deploy on 10-17 never reached the box: it was
still at 7f180ff, and the last bootstrap was 10-14. The second, from the main
checkout, landed: 73 tracked files matched and 569 tests passed.

Box timers, in UTC (the box's clock) and ET: pcs-mark Mon-Fri 13:35 (09:35),
pcs-propose 14:15 (10:15), pcs-watch hourly. `opened_at` stamps are UTC.

Arming state unchanged: read on the box 2026-10-17, not written.


---

## 55. Ranked by return, the agent always took the strike nearest spot

2026-10-18, from the question "why ORCL 133/130 and not 120/115?" Both facts
below are from the box's ledger (30 positions to 10-17) and from Robinhood's
hourly option bars for 2026-09-30 10:00-11:00 ET (last trades, not bid/ask).

- **The ranking** was credit over collateral, which only grows as the short
  strike nears the money, with a 3% floor on distance. The nearest allowed
  strike therefore won every time: all 30 positions opened 3.1-5.9% below
  spot (median 4.0%) at a short delta of 0.27-0.38. ORCL 133/130 was 3.1% out
  at 0.38, on a $162.50 collateral and an 85% return.
- **120/115 was never eligible.** It paid about $0.83-0.91 on 9-30, under the
  $100 floor. 125/120 paid about $1.23 at 8.9% out and 0.25 delta. 120/110 paid
  about $1.36 but takes about $860 of collateral, and only $452 of the
  $2,400 cap was free that morning.
- **Record to date:** 24 closed, 16 wins averaging +$125 and 8 stops averaging
  -$262, net -$91.80. Nothing in it compares near and far strikes, because
  none was ever taken, so the learner cannot find this by itself.

**Changed (the user chose all three, with 5% / 0.30 / chance of profit):**

- `Strategy.min_otm_cushion` 0.03 -> 0.05, still overridable per account (the
  box's `settings.json` holds null, so the baseline applies after a deploy).
- `Strategy.max_short_delta = 0.30`, new, asserted at import to stay at or under
  0.30. `build_spreads` rejects a short put above it ("short delta 0.38 > the
  0.30 ceiling"), and one with no delta to check ("no delta to check against
  the ceiling"). The second is a behaviour change: a chain that quotes no vol on
  either leg used to size (with no delta recorded, 47) and is now not taken.
- `optimizer.rank_key`: chance of profit (rounded to 0.01), then return, then
  cushion, then width. Used inside a name and in `build_proposals` across
  names. Return still picks the narrowest width at equal odds, since one short
  strike has one chance of profit.
- Tests: the default synthetic chain now pays 4.5 x e^(-otm/6) rather than
  3.0 x, so a $5-wide spread 5% out clears $100; `test_close_fees` opens the
  narrowest spread rather than the top-ranked one, because two lots of a wide
  one breach the per-trade cap; the recorded-vol tests borrow a long-leg vol
  instead of feeding a chain with none.

**Cost, and one thing to watch.** Farther strikes pay less, so to reach $100
they are wider and use more of the $2,400 cap, which means fewer positions and
more names skipped. `build_proposals` takes only the top-ranked spread of each
name, so a name whose best-odds spread is too wide for the room left is
blocked by the portfolio cap rather than offered its next-best spread. On 9-30
that would have blocked ORCL (120/110 at about $860 against $452 free), not
substituted 125/120. Not changed; whether to fall back to the next spread that
fits is a separate decision.

`expectancy.SELECTION_CHANGES` has the row `("2026-10-19", "d488761", ...)`,
dated Monday, the first propose run on the new code, so the five 10-16
positions and ORCL stay out of the History tab's since-the-change record.

Not a guarantee of a better record: it moves each trade to a lower chance of
loss and a smaller credit, and the stops (2x credit, or half the max loss) still
apply. The History tab measures the record since this row (51).

---

## 56. What the eight losses have in common, and what they cannot say

2026-10-15, after the AVGO stop and the HD take-profit. Read from a copy of the
box's ledger (`ssh pcs 'sudo -n -u pcs cat /opt/pcs/data/ledger.json'`), not
from `./run.py learn`: `learn` runs self-repair and writes the journal, so it is
not a read-only look. Nothing was written on the box.

**The record, closed trades only.** 23 closed, 15 wins and 8 losses, realised
-$182.55 (gross wins +$1,916.32, gross losses -$2,098.87). Two positions open:
ORCL 133/130 at 1.19x credit, HD 280/270 at 0.53x. Every win was a take-profit
and every loss a stop; nothing has expired and nothing has run to max loss.

- **Payoff.** An average win is $127.75 (0.544x credit), an average loss
  -$262.36 (1.171x credit): one loss costs 2.1 wins. Break-even is 68.3%, the
  record is 65.2%, Wilson 95% 45-81%. The shortfall is under one trade and the
  range covers both profit and loss.
- **The stops do what the rule says.** The six stops other than GOOGL bought
  back at 2.01x-2.33x credit, a mean overshoot of 0.14x, which is the 15-minute mark
  cadence. A loss runs 1.0x-1.3x credit. GOOGL (2.80x) is the pre-guard
  plumbing failure in 38 and is not a stop-rule result.
- **Few trades carry the loss dollars.** APP alone is 32% (-$666.43, the only
  15-wide trade), and APP + STX + RDDT are 58%. Width 10 nets +$726 and width 15
  -$666, so 10+ wide nets +$60. Width 5: 6 of 11, -$346.
- **Losses arrive together.** STX, BA and CLX all stopped on 2026-09-16, three
  sectors, -$611 = 29% of loss dollars, with 6 positions open. The sector and
  per-name caps cannot see a market-wide move. The index was NOT checked for
  that day, so "market-wide" is an inference. 4 of 8 losses ended within 3 days
  of the fill (GOOGL 1d, STX 1d, APP 3d, AVGO 3d).
- **3 of 8 never touched the short strike** at exit (GOOGL, RDDT, STX), the
  same pattern 47 found. AVGO is the third `mark_iv` reading for 47's vega
  hypothesis and the first with IV UP (+12%), strike breached by 2.5%. The
  other two (RDDT, APP) had vol falling. Three readings, mixed, no conclusion.
- **Re-entries after a loss:** GOOGL reopened 7 days later and won; RDDT
  reopened 6 days later (cooldown 5) and lost again. n = 2.

**Splits on the 16 trades that carry entry features** (the first 7 predate the
fields). None is significant; about ten cuts were looked at, so one or two
would look interesting by chance. Treat as hypotheses to watch, not findings:

| split | in | out | Fisher p |
|---|---|---|---|
| pullback under 5.5% below the 50dma | 7/9, +$461 | 3/7, -$917 | 0.30 |
| IV at open 50%+ | 2/5, -$857 | 8/11, +$400 | 0.30 |
| short delta above .30 | 7/13, -$727 | 3/3, +$271 | 0.25 |
| 30%+ off the 52w high | 4/7 | 6/9 | 1.00 |

Whole record: Communication Services 3 of 7, -$900 (p = 0.18; GOOGL, RDDT x2
and APP are 61% of loss dollars); Information Technology 7 of 9, +$423.

**The 5.5% pullback cut failed its first out-of-sample test.** 50 read it as 7
of 8 against 2 of 6. The two trades since went the other way (AVGO, 3.75%
pullback, lost; HD, 7.55%, won), so it is 7 of 9 against 3 of 7. The 5.5% cut
was chosen after seeing the data and was never a rule. No parameter changes on
this evidence.

**What the record could not answer when this was written.** Wins are cut at
about 50-60% of credit and losses at 2x, and no stopped spread was followed to
expiry, so there was no way to tell whether the stops saved money or locked in
losses that would have recovered. Section 57 answers it from the expiration-day
closes. The only per-trade limit today is the $1,000 collateral cap; a width or
max-loss cap is the next lever if APP-sized losses matter.

### Gotchas from the analysis

- `pct_off_high_at_open` and `pct_from_dma50_at_open` are stored as FRACTIONS
  (0.0755 = 7.55%), and only on trades opened from about 2026-09-08. Comparing
  them against 5.5 instead of .055 silently puts every trade in one bucket.
- `./run.py learn` is not a read-only look at the record. Copy the ledger with
  the command above and analyse the copy.
- Scheduled tasks record the creating session's folder as their cwd, and
  `change_directory` is refused in a worktree session. A task made from a pcs
  worktree runs in pcs. The DITM snapshot refresh has to be created from a plain
  session in `~/ditm-robinhood-agent`; until it is, `ditm-cycle.service` fails
  each weekday on the 2026-09-16 snapshot.

### Arming state, unchanged

Settings were read on the box on 2026-10-13, not written. `paper_trading`,
`auto_approve` (paper only), `auto_exit` and `mode` are as 42 records them. The
ledger is paper; live mode places no order in code.


---

## 57. Early exits against holding to expiration

`pcs/shadow.py`, 2026-10-18. Every exit is a bet that closing early beats
waiting, and the record could not price either bet (56). A closed position whose
expiration has passed now gets the underlying's **expiration-day close**,
settled with `paper_broker.settle_expired` -- the function a real expiry uses --
so "held" is exactly what an expired row would have booked. No option quote and
no running mark: settlement needs the spot and nothing else, which is also why
the 18 trades already closed could be back-filled the same day.

- Fields on `Position`: `shadow_settle_spot`, `shadow_pl`, `shadow_tried_on`.
  None means NOT SETTLED YET, never "held at no loss". Nothing in the exit path,
  the caps or the cash arithmetic reads them. They live on the ledger row on
  purpose: a separate `data/shadow.json` would be deleted by the `--delete`
  rsync unless it were added to the excludes, and the ledger is already
  protected.
- `mark` runs it after the exits for that run, six positions at most per run
  (the first run has everything to back-fill and holds the ledger lock), and a
  failure inside it is printed and swallowed -- `mark` is the job that takes
  stops. A failed fetch is retried once per calendar day, not once per mark.
- `./run.py shadow` prints the comparison; `--resolve` fetches what is due. The
  same table prints under `learn`.
- A close is settled only when its expiration is strictly in the past (the bar
  for that session is not final until it ends), and only on one clean bar for
  that exact date. A bar for another day, a NaN or an empty frame leaves the row
  unsettled.

### The first reading (18 early closes whose expiration had passed)

Run on a copy of the box's ledger from 2026-10-15 with real fetches, nothing
written on the box. The three biggest settle prices (AKAM 87.81, STX 639.01,
GLW 132.00) were checked against the daily history: continuous, no split, the
low of the window in each case.

| exit | n | actual | held to expiry | held minus actual | holding better |
|---|---|---|---|---|---|
| take_profit | 12 | +$1,631 | +$1,492 | -$139 | 10 of 12 |
| stop_loss | 6 | -$1,232 | -$803 | +$429 | 4 of 6 |
| all | 18 | +$400 | +$689 | +$290 | |

- **Take-profits** gave up money in 10 of 12 trades and still came out $139
  ahead, because two of them (GLW +$99, AKAM +$171) were closed before spreads
  that went on to max loss: -$306 and -$670 held.
- **Stops** cost $429 over the six: GOOGL (-$209 against +$116 held), RDDT
  (-$255 against +$9), RDDT again (-$156 against +$155) and CLX (-$170 against
  -$133) would each have been better held. BA (-$137 against -$370) and STX
  (-$304 against -$580) were saved.
- Holding everything would have booked +$290 more across 18 trades, and the
  held book contains four max losses (BA, GLW, STX, AKAM = -$1,926). The exits
  cap the tail at the price of the average. On this sample that price is about
  $16 a trade, well inside the noise of 18 trades; it is not evidence for
  loosening anything. The five early closes not yet past expiration (APP, UBER,
  AVGO twice, HD) are not in it, and APP is the largest loss on record.
- It does not price what a held spread costs: its collateral stays tied up and
  its slot is not free for the next trade, which the agent reused. That favours
  the exits and is not in the table.

### Gotchas

- `Ledger.log(kind, **payload)` already takes `kind`, so an event payload with a
  `kind=` key raises `TypeError: got multiple values`. The shadow event uses
  `exit=`.
- `resolve(fetch=settle_close)` as a default argument binds the function at
  definition time and a test cannot swap it. It takes `None` and looks the
  function up at call time.
- `close_action` is blank on 13 of the first 23 closed trades. `kind()` falls
  back to the prefix of `close_reason`; grouping on `close_action` alone puts
  those 13 in "other" and silently hides the split.
- **Was latent, now fixed (section 58):** `paper_broker.mark_positions` settled a
  real expiry off the spot of the first run AFTER `expiration`, so a Friday
  expiry would have settled on Monday's price. Nothing had ever expired in the
  record (every position closed early), so it never fired.

---

## 58. A real expiry settles on the expiration-day close

`mark_positions` booked an expired spread -- always max profit or max loss, or
the sliver between the strikes -- off the live spot of whichever mark run first
saw `expiration < today`. For the book that opened on 2026-10-16 every position
expires Friday 2026-11-20, so the first such run is Monday 11-23 and the weekend
decides a trade that ended Friday at 16:00. Nothing had ever expired (all 27
closes to date were early exits), so the bug had never booked a wrong number;
it was found while building the shadow-mark (section 57), whose own settlement
already read the right bar.

- `settle_close(symbol, expiration)` moved from `pcs/shadow.py` into
  `pcs/paper_broker.py` (shadow imports it back) so the real path and the
  research path settle off one function: the unadjusted close of that exact
  date, or None.
- `mark_positions(..., settle_price=None)` takes the lookup as a parameter; None
  means `settle_close`, resolved at call time so a test can swap it. Its `spots`
  argument is no longer read for an expired row.
- No close for the date means the row stays open, out of `fresh`, reported as
  "could not settle", and is retried on the next run. A lookup that raises is
  treated the same way, so one symbol's fault cannot stop the other marks or
  the stops. One lookup serves every row on the same symbol and date.
- The close that settled the row is written into `close_reason`
  (`... -- 2026-11-20 close 105.30`), so the number behind a permanent booking
  can be read off the row.
- Tests in `tests/test_record_integrity.py`: a live spot does not stand in for a
  missing close; the Monday spot loses to the Friday close in both directions;
  a close between the strikes books the partial; the lookup is asked for the
  expiration date; one lookup per symbol and date; a raising lookup leaves that
  row open and settles the rest.
- Not changed, and not examined: on the expiration day itself a position is
  still marked off the option chain (a 0-DTE chain, where exits can fire), and
  `expiration < today` is evaluated on the box's clock, which is UTC.

---

## 59. The earnings blockout is off

2026-10-22, on the user's instruction. The rule excluded any name whose next
report fell on or before expiry + 2 days, and counted an unknown date as inside.
It was applied after sizing (`pipeline.apply_earnings`), so it only ever saw
names that already had a qualifying spread.

What it was removing, off the box's `propose.log`:

| run | names with a spread | blocked by earnings | left to a ticket |
|---|---|---|---|
| 2026-10-19 | 21 | 16 | 5 |
| 2026-10-20 | 17 | 13 | 4 |

Across the 35 propose runs from 09-01, about 170 of roughly 380 name-days with a
spread (about 45%). The share is high from mid-October because a 31-32 DTE
expiry on 11-20 spans nearly the whole Q3 reporting season. An unknown date
never occurred. The 10-19 and 10-20 tickets that survived were stopped by
capital (the book was full), not by earnings, so removing the rule widens the
choice of what to buy with the same cash; it does not by itself add trades.

- **`Settings.earnings_blockout`, default False.** The key is absent from the
  box's `data/settings.json`, which `Settings.load` reads over the defaults, so
  the deploy takes effect on the next propose run with no edit on the box.
  `./run.py config --set earnings_blockout=on` brings the old rule back,
  `earnings_buffer_days` and `--allow-unknown-earnings` included.
- Off means: `build_proposals` does not skip the name, and `watchlist.build`
  does not give it the `EARNINGS` signal, so it reads `READY` or `BLOCKED` on
  the portfolio caps like any other. The date is still looked up and the ticket
  still prints it ("reports 2026-11-04, inside the 2026-11-20 expiration window
  - blockout off"), so the risk is shown rather than hidden. The dashboard's
  rules sentence follows the setting.
- **The record cannot say what this does.** All 27 closed trades and every open
  position were taken under the rule, so none has held an earnings report;
  nothing here measures it. What can be said without data: a report inside the
  window is a gap that a 2x-credit stop does not bound (a stop is a decision at
  the next mark, not a fill at the stop price), and an earnings-inflated IV
  fattens the credit, which is also what clears the $100 floor and lifts a name
  in the ranking. Expect these names to be over-represented among new tickets.
  That is an expectation, not an observation.
- Fills after this change are a different population.
  `expectancy.SELECTION_CHANGES` has the row `("2026-10-23", "e5a774e", ...)`,
  dated Friday, the first propose run on the new code, so the History tab's
  since-the-change record starts there and every position opened before it
  stays out of it.
- Tests: `tests/test_earnings_blockout.py`. The tests of the rule itself
  (`test_earnings_and_ingest`, the `EARNINGS` cases in `test_watchlist`) now
  switch it on explicitly.

---

## 60. 10-19 to 10-22: the book was full, and what freed it

Four propose runs, read off the box's `propose.log` and the ledger. Nothing was
broken: the agent proposed every day and the book could not take what it
proposed.

| run (10:15 ET) | passed the screen | had a spread | earnings-blocked | tickets | cleared |
|---|---|---|---|---|---|
| 10-19 | 89 | 21 | 16 | 5 | 0 |
| 10-20 | 75 | 17 | 13 | 4 | 0 |
| 10-21 | 69 | 18 | 13 | 5 | 0 |
| 10-22 | 60 | 15 | 9 | 6 | 1 (UAL) |

- **Where the names go before a ticket (10-19, the one run counted).** Of the 89,
  68 had no qualifying spread: 36 on open interest under 25, 25 on a bid/ask too
  wide, 3 with no bid or offer, 3 under the $100 credit floor, 1 other. Liquidity
  removes far more than earnings does; earnings only sees names that already had
  a spread (section 59).
- **Capital-full, 10-16 to 10-21.** LEN, LULU, ADBE, C and DAL all opened in the
  10-16 10:16 ET run and held $2,249.87 of collateral and $3,000 of width. That
  left $150 of headroom under the $2,400 cap and $530.62 of free balance
  (cash minus width at risk), against tickets that needed $688-$891. On 10-19
  all five tickets were refused by the cap and the balance (AVGO also by its
  re-entry cooldown); 10-20 and 10-21 ended 0 clear, every ticket blocked by
  portfolio limits. Nothing opened or closed on 10-21.
- **10-22.** DAL was stopped at 09:50 ET (-$99.25; buy-back $198, 2.0x its $99
  credit), which put free balance at $832.62. Of the six tickets only UAL 95/90
  fit ($394 of collateral, credit $106, spot $103.54, short delta -0.24, IV
  51%): MRVL $880, GS $847, LULU $879 (also a third Consumer Discretionary),
  HPE $999 and JBHT $809 did not, JBHT after UAL filled left $438.75. ADBE was
  then taken at 12:20 ET at 51% of max credit (+$138.02, six days held).
- **Why size decided it.** A $10-wide spread needs $800-$900 of balance and a
  $5-wide one about $400, so the $2,400 cap holds two of the former or six of the
  latter. The five opened on 10-16 were four $5-wide and
  one $10-wide. Lowering the $1,000 per-trade cap (to about $500 was suggested)
  is not what filled this book, and section 61 withdraws the suggestion.
- **Account.** End of 10-20: net liquidation $2,700.26 (-9.99%), realized
  -$218.91, unrealized -$80.83. At 12:20 ET on 10-22: about $2,843 (-5.2%),
  realized -$180.14 on 27 closed trades (17 winners), 4 open (LEN, LULU, C, UAL)
  holding $1,514 of collateral.

**The constituent list is a tracked input.** `data/sp500.csv` is in git and the
deploy rsync does not exclude it, so `run.py universe --refresh` run on the box
alone is undone by the next deploy; refresh locally, commit, deploy. Refreshed
2026-10-22 (it was 52 days old): 503 names, six in (BE, ILMN, P, SKYD, TWLO,
VYLR), six out (BLDR, CTVA, PSKY, TAP, TTD, WBD), FERG's sub-industry changed,
no sector changed, no open position affected.

---

## 61. Ten losses in 28 trades: what 56 gets wrong now, and what to do about it

2026-10-23. Read from a copy of the box's ledger, not from `./run.py learn`
(which runs self-repair and writes the journal). Nothing was written on the box.
This extends 56; it does not repeat it.

**The record, closed trades only.** 28 closed, 18 wins and 10 losses, realised
-$101.63 (gross wins +$2,223.60, average $123.53; gross losses -$2,325.23,
average -$232.52). Every win is a take-profit and every loss a stop; nothing has
expired. Break-even is 65.3%, the record is 64.3%, Wilson 95% 46-79%. Three are
open (LULU, C, UAL). Against 56 (23 closed, break-even 68.3%, record 65.2%,
-$182.55) the five trades since went 3 wins, 2 losses, +$80.92: HD +$90.75,
LEN +$78.51, ADBE +$138.02, ORCL -$127.11, DAL -$99.25. The book is still at
break-even, a fraction of a trade short, and the interval still spans a
working edge and none.

**Splits.** Delta and IV are recorded on 21 of the 28 closed trades (the first
seven predate the fields); the collateral, width and cushion rows cover all 28.
About fifteen cuts have now been looked at across 56 and this section, so one or
two would look interesting by chance.

| split | in | out |
|---|---|---|
| short delta 0.30 or less (21) | 4/5, +$263 | 9/16, -$638 |
| IV at open 55%+ (21) | 2/5, -$827 | 11/16, +$452 |
| collateral $700+ | 3/5, -$574 | 15/23, +$472 |
| collateral $500-700 | 6/7, +$863 | 12/21, -$965 |
| width 10 | 9/11, +$955 | 9/17, -$1,057 |
| width 5 | 7/13, -$367 | 11/15, +$265 |
| cushion at open 5%+ | 5/7, -$184 | 13/21, +$82 |

Delta, by Fisher on the pooled 21, is p = 0.61 (0.25 on the 16 of 56): it
weakened as trades were added. Medians for the 13 winners and 8 losers that
carry the fields, winners first: distance off the 52-week high 27.2% against
29.9%, distance from the 50dma -4.9% against -5.4%, cushion 4.0% against 3.7%.
None separates them. Return on collateral is 38.9% against 45.6%: the losers
were the richer credits, which is what a higher credit costs.

**What the losses have in common is size and structure, not an entry signal.**

- Five losses of $200 or more (APP, STX, RDDT, GOOGL, AVGO) are $1,636 of the
  $2,325, 70%. APP alone is $666: the only 15-wide trade, 59% IV.
- Seven of the eight losers with a recorded delta were above 0.30 (STX .38,
  ORCL .38, CLX .34, AVGO .33, APP .33, RDDT .33, BA .32). The 4 of 5 at 0.30 or
  less is the pattern `d488761` acts on.
- Four of ten stopped within 3 days of the fill, and no exit rule fixes a gap.
- RDDT was stopped twice, the second entry 6 days after the first stop, one day
  past the 5-day cooldown.

**The farther-strike rule has barely been tested.** `d488761` was committed on
Sunday 10-18 and its first propose run was Monday 10-19. The five positions
opened on 10-16 (LEN, LULU, ADBE, C, DAL) pre-date it and have deltas of
.27-.34. Exactly one position has opened under the rule: UAL 95/90 on 10-22
(delta -.24, 8.2% cushion), still open. A tally of "trades since the rule" that
counts the 10-16 book is wrong; count by `opened_at` against the
`SELECTION_CHANGES` date.

It is also not an independent test of the rule. The rule was chosen after the
losses were read, the same way the 5.5% pullback cut was (56), so the pooled
4/5 against 9/16 is the evidence that suggested it, not a confirmation. The
only out-of-sample trades since 56 are mixed: delta 0.30 or less went 1 win
(HD) and 1 loss (DAL), above 0.30 went 2 wins (LEN, ADBE) and 1 loss (ORCL).
`e5a774e` (the earnings blockout coming off) added a `SELECTION_CHANGES` row
dated 10-23, so the History tab's since-the-change record starts there and
holds none of the farther-strike trades yet.

**A $500 per-trade cap would have made the record worse.** It was floated as a
way to remove APP-sized losses and is withdrawn. Of the 28, the 16 trades with
collateral of $500 or less made -$390 against -$102 for all 28, because the
$500-700 band was 6 wins in 7 and +$863. Width 10 (9 of 11, +$955) did better
than width 5 (7 of 13, -$367), the opposite of what a "narrower is safer"
reading expects, so a cap that favours width 5 selects the weaker group. Only a
cap near $700 would have bitten (five trades, -$574, mostly APP), and that is
one trade chosen in hindsight.

**The exits are not shown to be the problem.** 57's first reading holds the 18
early closes to expiry at +$290 over what was booked, but the held book contains
four max losses totalling -$1,926. That is no case for loosening the stop or
the take-profit.

**Decision: no parameter changes on this evidence.** Keep the farther-strike
rule and add no filter. Judge the rule once about 20 trades have opened under
it; at the pace since the rule (two trades in five sittings) that is between
late December and early January, not the start of December (see 62), and only
if the earnings change does not muddy the sample
(it will, since both are in force from 10-23; the History tab cannot separate
them). Until then a split that looks good on ten losses is a hypothesis to
watch, not a rule.

### Gotchas from the analysis

- `pct_off_high_at_open` and `pct_from_dma50_at_open` are fractions, and
  `iv_at_open` is a fraction too (0.59 = 59%). `short_delta_at_open` is
  negative for a short put; compare its absolute value to .30.
- `short_delta_at_open` and `iv_at_open` are None on the seven earliest trades.
  A split that leaves them out is over 21 trades, not 28.
- Count trades "under a change" by `opened_at >= the SELECTION_CHANGES date`.
  Counting from the commit date or from memory puts the 10-16 book on the wrong
  side of `d488761`.


---

## 62. The trading council's first sitting: the skipped names are mostly an expiry artifact

`TRADING_COUNCIL.md` holds the charter, the evidence grades, every table and the
per-trade list. This section keeps the facts a later session should not have to
rediscover. Sitting 1 was 2026-10-23 at `0e3dc4c`. Paper account, analysis only.

**Why names are skipped.** 3,145 skip lines in 39 live sittings, first matching
reason: open interest under 25 on a leg 46.7%, no listed expiration in the 28-38
day window 32.3%, wide or missing bid/ask 12.7%, earnings blockout (since
removed) 6.0%, everything else 2.3%. 343 names reached a shortlist and only 76
ever produced a ticket. `chains.pick_expiration` picks the expiry nearest 32
days with no regard for liquidity, and sizing never falls back to another one.
Sittings whose target was a third-Friday monthly turned 24.3% of the shortlist
into a spread (206 of 846, 10 sittings); the others 8.6% (217 of 2,532, 28
sittings). Re-sizing the 10-23 shortlist on the 11-20 monthly gave 20 names
against 7 on the 11-27 weekly the agent used; 13 qualified only on the monthly.

**The skipped spreads show no edge.** 109 spreads that were sized and have since
settled (expiries to 10-16), held to expiry at the sizing mid credit: 69.7% won,
mean -$12.6 a trade, -$67.6 at the natural credit. Break-even was 74.8%. The
printed POP averaged 70.3%, so it was calibrated. The 18 the agent opened won
66.7% at -$11.7; the 91 it skipped won 70.3% at -$12.8. More volume is not an
aim; filter quality is.

**Capital is the limit.** 182 of 233 tickets (78%) were blocked by the $2,400
cap or the balance floor. Median open positions at a sitting is 4; the 8-position
cap never binds. On 10-23 AMD left $359 under the cap, so seven tickets could not
open, MRVL among them (it was also $12 short on balance).

**What `d488761` did to the tickets** (before / since, one per name per day):
collateral $640 / $840, share $10-wide 65% / 89%, credit $210 / $130, return on
collateral 35% / 16%, cushion 4.9% / 11.9%, IV 40% / 49%, POP 70% / 83%. POP sat
below break-even before (70% against 75%) and still does (83% against 86%), so
the rule did not create that gap. POP-first ranking tilts toward high-IV names,
which did worst in the old record.

**Stops.** Six of the 10 stops fired at the 09:35 or 09:50 ET mark, 7.7% of mark
runs. Two of them (GOOGL, DAL) fired with the stock within 0.5% of entry; GOOGL
would have finished +$116 against the -$209 booked. On the 18 settled closes,
removing stops would add $428 and no take-profit level beat 50%, but four stops
(APP, ORCL, AVGO, DAL) have no hold value yet and APP is the largest loss, so
this is a hypothesis. They settle 10-30, 10-30, 11-13 and 11-20; re-run the
replay then.

**Fills.** Mean paper fill $209 of credit, mean mid $216, mean natural $163; the
median fill is 88% of the way from natural to mid. At the natural, entry credit
alone is about $46 a trade lower, roughly $1,300 over the 28 closed trades
against -$102 realised. Paper P&L is an upper bound on a live result.

**One trade carries the loss.** APP's stop was -$666, 29% of all loss dollars;
without it the other 27 closed trades are +$565.

**Timing correction to 61.** Twenty trades under `d488761` is between late
December and early January at the pace since the rule, not the start of
December, which counted calendar days. Ten trades is around the end of November.

**State at the 15:50 ET mark on 2026-10-23.** 28 closed (18 wins, 10 losses),
realised -$101.63; open LULU, C, UAL and AMD at -$7, -$79, -$75 and -$3; net
liquidation $2,734.53. AMD (415/405, opened 10-23) is the first position opened
under both the farther-strike and the earnings changes.

**Decision: no parameter changes.** The council proposed a liquidity-aware
fallback for expiry selection and a natural-fill column in the expectancy
report; neither was built at the sitting. The first is built since, off by
default (63); the second is not.

**Corrected by the second sitting (64):** the "mid" credit above is the ticket's
sizing credit, the 74.8% break-even is the max-loss form, the "$1,300" is the
all-32 mean applied to 28 trades, and at the true mid the pool is break-even.

### Gotchas from the analysis

- The logs are `/opt/pcs/logs/propose.log` and `mark.log`; the ledger and
  proposals are in `/opt/pcs/data/`. The OPEN table in `mark.log` drops a
  position on the run that closes it, so append the ledger's close debit as the
  last point of a replay. With that the replay of the real rule gave -$100.71
  against the ledger's -$101.63.
- A proposal's `pop` is a column, not a pandas method: `df["pop"]`, never
  `df.pop`.
- Skip-reason text can carry several reasons at once; classify by a fixed
  priority order and say so, or the shares will not add to 100%.


## 63. The expiry fallback: built, on by default, narrower than the council first said

`Settings.expiry_fallback` is council recommendation R1. It was built on
2026-10-23 with default `False` (`e9830a9`) and the default was then changed to
`True`. The box's code and setting are not recorded here: `git log` and
`run.py config` on the box show them.

**What it does.** `size_candidates` sizes a name on the one expiry
`pick_expiration` chose. With the setting on, a name whose chain loaded but
produced no spread is tried on the other expirations inside the same 28-38 day
window, best first (`chains.rank_expirations`, Friday before non-Friday), at
most `chains.FALLBACK_TRIES` = 2, stopping at the first that yields a spread.
`pick_expiration` is now the head of `rank_expirations`, so the first choice is
unchanged. `PutChain.listed` carries every expiry yfinance returned, so no extra
call is needed to learn the alternatives. No sizing rule changes.

**What it never does.** It does not look outside the window. It does not run
when the first chain failed to load (a data fault), when the caller named an
expiration with `--expiration`, or on Robinhood or modeled chains, which have
empty `listed`. A name that still fails says `(also tried X, Y)` on its skip
line. `run.py propose` prints `expiry fallback on: N name(s) sized on another
expiration in the window` and one indented line per name, only when the setting
is on.

**Reach is narrower than the council's table implied.** It can only help the
open-interest and quote-quality skips, 1,866 of 3,145 skip lines (59%). Of
those, 1,551 (83%) fall in the 32 of 39 sittings whose window held two Fridays,
which is 4 of 5 weekdays. The 32% of skips that list nothing inside 28-38 days
are out of reach: they need a wider `dte_window`, and that is a skill-fixed
strategy value, not a setting.

**Cost.** Up to two more chain fetches per name that fails, not parallelized.
`pcs-watch` (hourly, round the clock, a 30-minute timeout, about 55 seconds a
run on the box) and `propose` both call `size_candidates`, so both pay it.
Measured once, from a laptop on Friday 2026-10-23 after the close, on the watch
shortlist of 82 names: fallback off 12 s and 82 fetches, 12 names with a spread;
on 16 s and 116 fetches, 23 names with a spread, 11 of them moved from the
11-27 weekly to the 11-20 monthly, and 23 names still skipped after trying both
alternates. The earlier guess of 80-160 extra seconds was about twenty times
too high. One run on one evening, not a distribution: look at `pcs-watch`
wall time (`journalctl -u pcs-watch`) after the first deploy with it on.

**Changing the default is a selection change.** It changes which trades are
taken, so the change has a `SELECTION_CHANGES` row (dated the first sitting
under it, as `e5a774e`'s was) and the latest-row assertion in
`tests/test_since_change.py` follows it. The row is added in a follow-up commit
that names the change's commit, since a commit cannot name itself. Setting it
with `run.py config --set expiry_fallback=on|off` on the box is the same change
in effect, and the History tab would not know about it. Keys absent from the
box's `settings.json` take the code default, so a default flip reaches the box
without editing that file; `expiry_fallback` was absent from it on 2026-10-24.

**What the new row does to the record.** The History tab's since-change panel
counts positions opened on or after the latest row's day, so a new row restarts
it from zero. The farther-strike sample (`d488761`, the 10- and 20-trade
checkpoints in `TRADING_COUNCIL.md` 8) is counted from 2026-10-19 by hand, not
from that panel, and from the first sitting under the fallback its trades come
from two changes at once. A trade that opened on a monthly the fallback
reached is identifiable by its expiration and by the `expiry fallback on:` line
in `propose.log`.

Checks to run once it is on: fills on the names it adds against their mid, and
whether the names it adds stop out more or less than the rest. Neither is
known; the value of the extra names is a hypothesis.


## 64. The second sitting audited the agent itself: nothing it printed was invented, the council's own "mid" was wrong

`TRADING_COUNCIL.md` 9 has the tables and the grades. This keeps the facts. Sitting
2 was 2026-10-25 at `5f20f9b`, a read-only audit of the box's ledger and logs
against Yahoo bars and plain arithmetic. Paper account.

**The agent has no language model.** Everything it prints is a template with
numbers filled in by code, so the failure modes are a wrong number or a wrong
source, never an invented fact. Checked and reproduced: cash two ways
($3,357.15), geometry of all 32 positions, `realized_pl` of all 28 closed
(-$101.63), every number in all 28 close-reason sentences, the recorded % off
high and % from 50dma and short delta, the 18 expiry-day closes and hold values,
and the arithmetic and prose numbers of all 241 tickets.

**Ticket field to remember.** The printed `credit $N (package BID/ASK, natural
$N)` is the **sizing credit**, not the mid. The true mid is
`(package bid + ask) / 2`, the ticket JSON's `credit_mid`. A parser that reads
`credit` as the mid understates it by about $9 a trade (mean $215.5 against
$224.9). Sitting 1's parser did, which is why its pool read negative.

**Corrected pool figures** (109 settled spreads, held to expiry): at the true mid,
win 70.6%, mean -$0.8, break-even 70.7%, 95% interval -$80 to +$87 resampling 13
sittings; at the sizing credit 69.7%, -$12.6, break-even 71.4%; at the natural
68.8%, -$67.6, break-even 78.0%. 74.8% was the max-loss form of break-even
(mean of (width - credit) / width), not the realized-payoff one. The $1,300
fill gap is $1,400 over the 28 closed. "No edge shown" and "more volume is not an
aim" stand; "negative at mid" does not.

**POP.** Calibrated in aggregate (printed 70.3%, realized 70.6%), not by bin:
62.9 -> 62.5% won, 68.6 -> 80.4%, 74.2 -> 63.2%, 83.5 -> 50.0% (n = 4). Grade C.

**Earnings dates from `yfinance.calendar` are projections and are printed as
fact.** Of 9 claims that could be resolved, 3 were off by more than 3 days (UNH
14, META 7, TXN 6). UNH tickets on 09-11, 09-14 and 09-15 said `clear -- next
report 2026-10-27`; the report was 10-13, inside the 10-16 expiry. UNH was never
opened. `earnings_dates` was exact every time. None of the 32 positions was held
through a report. Proposal: label calendar dates as estimates (R6 in the council
doc); not built.

**Counts to carry.** The 39 "live sittings" are 38 trading days plus the Labor Day
run on 09-07, which printed "Market closed (holiday)" and opened and closed
nothing. 241 tickets in 35 sittings across 76 names. Marks ran 26 a day on 38
days. Skips on open interest reproduce on live chains: KKR, CRH, DECK and IP had
no put with open interest of 25 or more 3-20% below spot.

**Gotchas.** A test built from "today + N days" depends on the weekday through
the Friday preference; sweep it over consecutive days before trusting it. The
ledger is read with `ssh pcs 'sudo -n -u pcs cat /opt/pcs/data/ledger.json'`, not
with `./run.py learn`.

**Not checked.** The quotes the agent saw at each fill (no historical option
chains), Yahoo's own prices and IV, and the dashboard's rendering.
