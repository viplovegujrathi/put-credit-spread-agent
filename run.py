#!/usr/bin/env python3
"""S&P 500 beaten-down put credit spread agent -- command line.

    ./run.py screen                 run the screen, show every bucket
    ./run.py propose                screen -> size -> risk -> proposal tickets
    ./run.py approve P260831-01 --approver "Viplove"    <- the human gate
    ./run.py reject  P260831-01 --reason "too close to earnings"
    ./run.py mark                   refresh marks, surface management actions
    ./run.py learn                  what the closed record supports, and self-repair
    ./run.py doctor                 why has nothing opened? every gate, in order
    ./run.py status                 account + open positions
    ./run.py close  <position-id>   close a paper position at the current mark
    ./run.py dashboard              rebuild dashboard.html
    ./run.py universe --refresh     refresh the S&P 500 constituent list
    ./run.py chain-requests         list the chains to pull via the Robinhood MCP

Nothing in this CLI places a real order. `approve` fills into the paper ledger
only; in live mode it prints the ticket and refuses.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from pcs import (
    chains,
    dashboard,
    doctor,
    exits,
    health,
    learning,
    marketdata,
    paper_broker,
    pipeline,
    proposer,
    screener,
    session,
    universe,
    watchlist,
)
from pcs import config as config_mod
from pcs import ledger as ledger_mod
from pcs.config import DATA_DIR, PROPOSALS_JSON, STRATEGY, Settings
from pcs.optimizer import Spread

BAR = "=" * 78


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _progress(i: int, n: int, got: int) -> None:
    print(f"\r  fetching price history  batch {i}/{n}  ({got} tickers)", end="", flush=True)
    if i == n:
        print()


# ---------------------------------------------------------------------------
def cmd_screen(args, settings: Settings) -> int:
    res = pipeline.run_screen(settings, symbols=args.symbols, progress=_progress,
                              cache_max_age_min=args.max_cache_age)
    print(BAR)
    print(f"SCREEN  {res.session.now_et:%Y-%m-%d %H:%M %Z}   {res.session.banner}")
    print(f"universe: {len(res.universe.symbols)} names, list built {res.universe.as_of}")
    print(BAR)
    for w in res.warnings:
        print(f"  ! {w}")

    print("\nbuckets:")
    for k, v in res.counts.items():
        print(f"  {k:<22} {v:>4}")

    def table(title: str, rows: list, note: str) -> None:
        print(f"\n{title}  ({len(rows)})\n  {note}")
        if not rows:
            print("  (none)")
            return
        print(f"  {'ticker':<7}{'sector':<26}{'price':>9}{'50dma':>9}"
              f"{'from50':>9}{'off high':>10}")
        for c in rows:
            print(f"  {c.symbol:<7}{c.sector[:25]:<26}{c.spot:>9,.2f}{c.dma50:>9,.2f}"
                  f"{c.pct_from_dma50:>+9.1%}{c.pct_off_high:>10.1%}")

    table("PRIMARY -- tradeable under this mandate", res.bucket(screener.PRIMARY),
          "3-8% below the 50dma and >=15% off the 52-week high")
    table("NEAR BUT TIGHT -- separate bucket, not blended in",
          res.bucket(screener.NEAR_TIGHT),
          "within 3% below the 50dma: near the average but barely pulled back")
    if args.verbose:
        table("EXCLUDED: stretched (8-12% below)", res.bucket(screener.STRETCHED),
              "past the intended band; excluded by default")
        table("EXCLUDED: broken down (>12% below)", res.bucket(screener.BROKEN)[:15],
              "not a 'near the average' stock - see the 50-day-low variant instead")
        table("DIFFERENT THESIS: above the 50dma", res.bucket(screener.ABOVE)[:15],
              "recovery/continuation, not a beaten-down bounce - flagged, not traded")
    else:
        print(f"\n  (excluded: {len(res.bucket(screener.STRETCHED))} stretched, "
              f"{len(res.bucket(screener.BROKEN))} broken down, "
              f"{len(res.bucket(screener.ABOVE))} above the 50dma -- `--verbose` to list)")

    # A partial run must never overwrite the full-universe screen that
    # `chain-requests` and `propose --cached` read from.
    path = DATA_DIR / ("last_screen_partial.json" if args.symbols else "last_screen.json")
    path.write_text(json.dumps({
        "at": dt.datetime.now().isoformat(timespec="seconds"),
        "session": res.session.phase, "counts": res.counts,
        "candidates": [c.as_dict() for c in res.candidates if c.bucket != screener.NO_DATA],
    }, indent=2))
    print(f"\nsaved: {path}")
    return 0


def cmd_propose(args, settings: Settings) -> int:
    res = pipeline.run_screen(settings, symbols=args.symbols, progress=_progress,
                              cache_max_age_min=args.max_cache_age)
    led = ledger_mod.Ledger.load(settings)
    cands = pipeline.shortlist(res, include_tight=args.include_tight)

    # Names the agent benched itself after repeated data failures. Applied here
    # rather than inside the pipeline: the pipeline states the strategy, and a
    # quarantine is an operational fact about a data feed, not a rule.
    benched = learning.blocked_symbols(learning.load())
    dropped = [c.symbol for c in cands if c.symbol in benched]
    if dropped:
        cands = [c for c in cands if c.symbol not in benched]

    print(BAR)
    print(f"PROPOSE  {res.session.now_et:%Y-%m-%d %H:%M %Z}")
    print(f"account: {settings.account_label} [{led.mode}]   cash {_money(led.cash)}   "
          f"available balance {_money(led.buying_power)}   open {len(led.open_positions)}")
    print(BAR)
    for w in res.warnings:
        print(f"  ! {w}")
    print(f"\nshortlist: {len(cands)} name(s) passed the screen"
          f"{' (incl. NEAR_BELOW_TIGHT)' if args.include_tight else ''}")
    if not cands:
        print("  nothing passed the screen today - no proposals.")
        if dropped:
            print(f"  ({len(dropped)} name(s) benched by self-repair: "
                  f"{', '.join(dropped)})")
        return 0
    print("  " + ", ".join(c.symbol for c in cands))
    if dropped:
        print(f"  benched by self-repair (not proposed): {', '.join(dropped)}"
              f"  --  ./run.py learn")

    exp_probe = args.expiration or pipeline.resolve_batch_expiration(cands, settings)
    if exp_probe:
        print(f"\ntarget expiration: {exp_probe} "
              f"({(dt.date.fromisoformat(exp_probe) - dt.date.today()).days} DTE, "
              f"{dt.date.fromisoformat(exp_probe):%A}) -- names without a listing in the "
              f"{STRATEGY.dte_window[0]}-{STRATEGY.dte_window[1]} DTE window resolve their own "
              f"or are skipped")
    else:
        print(f"\n  ! no shortlisted name lists an expiration in the "
              f"{STRATEGY.dte_window[0]}-{STRATEGY.dte_window[1]} DTE window.")
        return 1

    print("  pulling option chains and sizing spreads...")
    sized = pipeline.size_candidates(cands, res, settings, expiration=args.expiration)
    n_live = sum(1 for sc in sized if sc.spreads)
    print(f"  {n_live}/{len(sized)} name(s) produced a qualifying spread")
    print("  checking the earnings calendar for those...")
    pipeline.apply_earnings(sized, res, settings)
    props, skipped = pipeline.build_proposals(
        sized, res, led, settings, contracts=args.contracts,
        allow_unknown_earnings=args.allow_unknown_earnings)

    print(f"\n{BAR}\nPROPOSALS ({sum(1 for p in props if p.risk_ok)} clear, "
          f"{sum(1 for p in props if not p.risk_ok)} blocked by portfolio limits)\n{BAR}")
    for p in props:
        print()
        print(proposer.ticket(p, settings))
    if skipped:
        print(f"\nSKIPPED ({len(skipped)}):")
        for s in skipped:
            print(f"  - {s}")

    path = proposer.save(props)
    print(f"\nsaved: {path}")
    clear = [p.id for p in props if p.risk_ok]

    if clear and not settings.require_approval() and not args.no_auto_open:
        _auto_open(props, led, settings, res.session)
    elif clear:
        print(f"\nNothing has been placed. To open one in the paper account:\n"
              f"  ./run.py approve {clear[0]} --approver \"your name\"")
        if not res.session.can_open_positions:
            print(f"  (held until {res.session.settle_until:%H:%M} ET - "
                  f"{res.session.open_block_reason.split(' - ')[0]})")
    health.record("propose", detail=f"{len(props)} proposal(s), "
                  f"{sum(1 for x in props if x.risk_ok)} clear")
    dashboard.render(led, props, settings, res.session)
    return 0


def _record_fill(p, pos, approver: str) -> None:
    """The bookkeeping that follows any fill, human-approved or not."""
    p.status, p.approved_by = "approved", approver
    p.approved_at = dt.datetime.now().isoformat(timespec="seconds")
    p.position_id = pos.id


def _fill_line(p, pos, led) -> str:
    return (f"  {p.symbol} {pos.short_strike:g}/{pos.long_strike:g}p {pos.expiration}  "
            f"filled ${pos.credit_open:.2f}  credit {_money(pos.credit_dollars)}  "
            f"collateral {_money(pos.collateral)}  -> {pos.id}")


def _auto_open(props, led, settings: Settings, sess, journal=None) -> int:
    """Open every clear proposal, in rank order, with no human in the loop.

    Only reachable when `require_approval()` is False, which is paper-only. The
    gates inside `open_approved` are unchanged and still do the real work -- the
    balance floor especially, since opening down the list eats the balance the
    later proposals were sized against. A refusal is per-proposal: it is
    reported and the batch continues, because the next one may well be smaller.
    """
    print(f"\n{BAR}\nAUTO-OPEN -- human approval is OFF for this paper account\n{BAR}")

    # The opening-range gate lets a fill through outside regular hours, and for a
    # human that is right: they read the stale-quote banner and decide. With
    # nobody in the loop there is no one to read it, so the same standard
    # `apply_exits` holds itself to applies here -- an auto-open against the
    # afternoon's last print is a fill nobody could have got.
    if not sess.is_open:
        print(f"  HELD -- the market is {sess.phase}. Tickets are written and stay "
              f"pending.\n  Auto-open needs a live market; nobody is here to judge a "
              f"stale quote.\n  Approve one by hand if you have read the ticket: "
              f"./run.py approve <id> --approver \"your name\"")
        return 0

    # Loaded here, not at the top of `propose`: the screen in between takes
    # minutes, and the mark timer writes this same file every fifteen. Holding
    # a copy across that gap means saving it clobbers whatever mark decided.
    journal = learning.load() if journal is None else journal
    approver = settings.auto_approver()
    opened, held = [], []
    for p in props:
        if p.status != "pending" or not p.risk_ok:
            continue
        try:
            pos = paper_broker.open_approved(
                led, Spread(**p.spread), p.sector, p.contracts, settings,
                proposal_id=p.id, approved_by=approver, sess=sess)
        except paper_broker.OpenBlocked as exc:
            held.append((p, str(exc)))
            learning.record_fault(journal, learning.OPEN_BLOCKED, p.symbol, str(exc))
            continue
        _record_fill(p, pos, approver)
        opened.append((p, pos))
    if opened or held:
        led.save()
        proposer.save(props)
    if held:
        learning.save(journal)
    print(f"  approver recorded as: {approver}")
    for p, pos in opened:
        print(_fill_line(p, pos, led))
    for p, reason in held:
        print(f"  HELD {p.symbol} [{p.id}] - {reason}")
    if not opened and not held:
        print("  nothing clear to open.")
    else:
        print(f"\n  cash {_money(led.cash)}   available balance {_money(led.buying_power)}"
              f"   open {len(led.open_positions)}")
    return len(opened)


def cmd_approve(args, settings: Settings) -> int:
    props = proposer.load()
    p = next((x for x in props if x.id == args.proposal_id), None)
    if p is None:
        print(f"no proposal {args.proposal_id} in {PROPOSALS_JSON}")
        return 1
    if p.status != "pending":
        print(f"proposal {p.id} is already {p.status}")
        return 1
    if not p.risk_ok:
        print(f"proposal {p.id} is blocked by portfolio limits:")
        for r in p.risk_reasons:
            print(f"  - {r}")
        if not args.override:
            print("  refusing. re-run with --override only if you accept the breach.")
            return 1
        print("  ! overridden by the approver")

    approver = args.approver
    if not approver:
        if settings.require_approval():
            print("--approver is required: every trade needs explicit per-trade "
                  "human approval.")
            return 1
        approver = settings.auto_approver()

    led = ledger_mod.Ledger.load(settings)
    sess = session.state_for(settings)
    sp = Spread(**p.spread)
    print(proposer.ticket(p, settings))
    print(f"\napprover: {approver}")
    try:
        pos = paper_broker.open_approved(led, sp, p.sector, p.contracts, settings,
                                         proposal_id=p.id, approved_by=approver,
                                         sess=sess)
    except paper_broker.MarketNotReady as exc:
        print(f"\nHELD - {exc}")
        print(f"  {p.id} stays pending. Re-run this same command after "
              f"{sess.settle_until:%H:%M} ET.")
        return 1
    except (paper_broker.InsufficientFunds, paper_broker.TradingDisabled) as exc:
        print(f"\nHELD - {exc}")
        print(f"  {p.id} stays pending; the ledger is unchanged.")
        return 1
    _record_fill(p, pos, approver)
    proposer.save(props)
    led.save()
    haircut = (f"sized at ${sp.credit:.2f}; paper fills take a deliberate haircut"
               if pos.credit_open < sp.credit else
               f"capped at the ${sp.credit:.2f} the ticket was sized on")
    print(f"\nPAPER FILL  position {pos.id}")
    print(f"  filled at ${pos.credit_open:.2f} credit ({haircut})")
    print(f"  net credit {_money(pos.credit_dollars)}   collateral {_money(pos.collateral)}")
    print(f"  cash {_money(led.cash)}   available balance {_money(led.buying_power)}")
    dashboard.render(led, props, settings, session.state_for(settings))
    return 0


def cmd_reject(args, settings: Settings) -> int:
    props = proposer.load()
    p = next((x for x in props if x.id == args.proposal_id), None)
    if p is None:
        print(f"no proposal {args.proposal_id}")
        return 1
    p.status = "rejected"
    p.risk_warnings.append(f"rejected by human: {args.reason}")
    proposer.save(props)
    print(f"proposal {p.id} ({p.symbol}) rejected: {args.reason}")
    return 0


def _journal_pass(journal, led, settings: Settings) -> list[str]:
    """Ingest any newly closed trades, run self-repair, persist.

    Kept in one function so `mark` and `learn` cannot drift apart, and called
    on every path -- an account with nothing open still has quarantines that
    need to expire on schedule.
    """
    learning.sync(journal, led)
    repairs = learning.self_repair(journal, settings)
    learning.save(journal)
    return repairs


def cmd_mark(args, settings: Settings) -> int:
    led = ledger_mod.Ledger.load(settings)
    if not led.open_positions:
        print("no open positions to mark.")
        for r in _journal_pass(learning.load(), led, settings):
            print(f"  SELF-REPAIR  {r}")
        return 0
    sess = session.state_for(settings)
    print(f"{BAR}\nMARK  {sess.now_et:%Y-%m-%d %H:%M %Z}   {sess.banner}\n{BAR}")
    syms = sorted({p.symbol for p in led.open_positions})
    spots = marketdata.live_quote(syms) if sess.is_open else {}
    for s, snap in marketdata.fetch_snapshots(syms, batch_size=len(syms)).items():
        spots.setdefault(s, snap.spot)
    notes, fresh = paper_broker.mark_positions(led, settings, spots)

    # A position that would not re-price is a data fault against that symbol,
    # not a trading decision. Recorded here because this is the only place that
    # knows a mark was attempted and failed.
    journal = learning.load()
    for pos, note in notes:
        if note.startswith("could not mark"):
            learning.record_fault(journal, learning.MARK_FAILED, pos.symbol, note)

    decisions = exits.review(led, settings, fresh)
    auto = settings.auto_exit and led.mode == "paper" and not args.no_auto_exit
    actionable = [(p, d) for p, d in decisions if d.act]

    # An exit that could not even be CONSIDERED because the mark behind it is
    # stale is a third outcome, and the one with no trace anywhere: `review()`
    # skips those positions entirely, so they leave no line to read. Decide on
    # the old mark purely to report that something may be due and we cannot
    # tell. Nothing is acted on here.
    unpriced = [(p, exits.decide(p, settings)) for p in led.open_positions
                if p.id not in fresh]
    skipped = [(p, d) for p, d in unpriced if d.act]
    taken_n = held_n = 0

    if auto and actionable and not sess.is_open:
        held_n = len(actionable)
        led.save()
        print(f"\nEXITS DUE ({len(actionable)}) -- HELD, the market is {sess.phase}. "
              f"A close taken now would be filled at a price nobody could trade on.")
        for pos, d in actionable:
            print(f"  {d.headline}  {pos.symbol} [{pos.id}]: {d.reason}")
    elif auto and actionable:
        acted = paper_broker.apply_exits(led, settings, fresh, sess)
        taken_n = len(acted)
        led.save()
        print(f"\nEXITS TAKEN ({len(acted)}) -- decided and executed by the agent")
        for pos, d in acted:
            print(f"  {d.headline}  {pos.symbol} {pos.short_strike:g}/{pos.long_strike:g} "
                  f"[{pos.id}]  realized {_money(pos.realized_pl)}")
            print(f"      {d.reason}")
    else:
        led.save()
        if actionable:
            held_n = len(actionable)
            why = ("live mode -- a close is an order, so this is a ticket for you to place"
                   if led.mode != "paper" else "auto-exit is off")
            print(f"\nEXITS DUE ({len(actionable)}) -- {why}:")
            for pos, d in actionable:
                print(f"  {d.headline}  {pos.symbol} [{pos.id}]: {d.reason}")
                print(exits.ticket(pos, d))

    # Persisted BEFORE the dashboard renders, so the page reads this run rather
    # than the previous one. This is the record that lets the page tell "nothing
    # happened" apart from "nothing ran" -- see pcs/health.py.
    health.record(
        "mark", positions=len(led.open_positions) + taken_n, marked=len(fresh),
        stale=len(unpriced), stale_symbols=sorted({p.symbol for p, _ in unpriced}),
        exits_due=len(actionable) + len(skipped),
        exits_taken=taken_n, exits_held=held_n, exits_skipped=len(skipped),
        held_detail=[f"{p.symbol} {d.headline}: {d.reason[:120]}"
                     for p, d in (actionable if held_n else []) + skipped])

    _print_positions(led)
    stale = [p for p in led.open_positions if p.id not in fresh]
    if stale:
        print(f"\n  ! {len(stale)} position(s) did not re-price; no exit was decided on "
              f"a stale mark: {', '.join(p.symbol for p in stale)}")
    if skipped:
        print(f"  ! {len(skipped)} of those would have exited on their last known "
              f"mark: {', '.join(p.symbol for p, _ in skipped)}")
    watch = [(p, d) for p, d in decisions if not d.act and d.reason]
    if watch:
        print("\nWATCHING (no action due):")
        for pos, d in watch:
            print(f"  {pos.symbol} {pos.short_strike:g}/{pos.long_strike:g} [{pos.id}]: {d.reason}")
    # Closes taken above are now in the ledger, so ingest after the exits run
    # rather than before -- otherwise every trade lands in the journal a run late.
    for r in _journal_pass(journal, led, settings):
        print(f"\n  SELF-REPAIR  {r}")

    dashboard.render(led, proposer.load(), settings, sess)
    return 0


def _print_positions(led) -> None:
    print(f"\naccount  cash {_money(led.cash)}   collateral held {_money(led.collateral_held)}"
          f"   capital at risk {_money(led.capital_at_risk)}")
    print(f"         available balance {_money(led.buying_power)}")
    print(f"         net liq {_money(led.net_liq)}   "
          f"total return {led.total_return:+.2%}   "
          f"realized {_money(led.realized_pl)}   unrealized {_money(led.unrealized_pl)}")
    if led.open_positions:
        print(f"\nOPEN ({len(led.open_positions)})")
        print(f"  {'id':<10}{'ticker':<7}{'spread':<14}{'exp':<12}{'DTE':>4}"
              f"{'credit':>9}{'now':>8}{'P&L':>9}{'% max':>8}")
        for p in led.open_positions:
            print(f"  {p.id:<10}{p.symbol:<7}"
                  f"{f'{p.short_strike:g}/{p.long_strike:g}p':<14}{p.expiration:<12}"
                  f"{p.dte:>4}{p.credit_dollars:>9,.0f}"
                  f"{p.mark_cost_to_close * 100 * p.contracts:>8,.0f}"
                  f"{p.open_pl:>+9,.0f}{p.pct_of_max_credit:>8.0%}")
    if led.closed_positions:
        print(f"\nCLOSED ({len(led.closed_positions)})")
        for p in led.closed_positions:
            print(f"  {p.id:<10}{p.symbol:<7}"
                  f"{f'{p.short_strike:g}/{p.long_strike:g}p':<14}"
                  f"{p.realized_pl:>+9,.0f}   {p.close_reason}")


def cmd_learn(args, settings: Settings) -> int:
    """What the closed record supports -- and the repairs the agent made itself.

    Read-only with respect to trading. It ingests closed trades, expires and
    creates quarantines, and prints suggestions. It never applies one: a
    suggestion drawn from a dozen fills is a hypothesis, and the account is the
    only thing that pays if it is wrong.
    """
    led = ledger_mod.Ledger.load(settings)
    journal = learning.load()
    before = len(journal.outcomes)
    repairs = _journal_pass(journal, led, settings)
    s = learning.summary(journal, settings)

    print(f"{BAR}\nLEARN  {dt.datetime.now():%Y-%m-%d %H:%M}\n{BAR}")
    print(f"closed trades on record: {s['closed']}  ({s['wins']}W / {s['losses']}L)"
          f"   newly ingested: {len(journal.outcomes) - before}")
    print(f"operational faults logged: {s['faults']}   self-repairs to date: "
          f"{s['repairs']}")
    if repairs:
        print("\nSELF-REPAIR THIS RUN:")
        for r in repairs:
            print(f"  {r}")
    if s["quarantined"]:
        print("\nBENCHED (kept out of proposals until the date shown):")
        for q in journal.quarantines:
            print(f"  {q.symbol:6} until {q.until}  -- {q.reason}")
    else:
        print("\nnothing benched: every symbol is in the universe.")

    print(f"\n{BAR}\nWHAT THE RECORD SUPPORTS\n{BAR}")
    for lesson in learning.lessons(journal, settings):
        print(f"\n[{lesson.confidence.upper()}] {lesson.title}  (n={lesson.sample})")
        print(f"  {lesson.finding}")
        if lesson.suggestion:
            print(f"  suggested, NOT applied:  {lesson.suggestion}")

    print("\nNot learnable -- the ledger never recorded these at open:")
    for g in learning.feature_gaps():
        print(f"  - {g}")
    print("\nNothing above has been applied. Every number stays where it is until "
          "you change it.")
    dashboard.render(led, proposer.load(), settings, session.state_for(settings))
    return 0


_MARK = {doctor.BLOCK: "\u2717", doctor.WARN: "!", doctor.OK: "\u2713",
         doctor.INFO: "\u00b7"}


def cmd_doctor(args, settings: Settings) -> int:
    """Walk every gate between "the market is open" and "a position exists".

    Offline: it reports on the last run rather than performing a new one, which
    is the question being asked. A flat book looks the same whether the screen
    found nothing, the master switch is off, or the timer never fired -- this
    says which.
    """
    led = ledger_mod.Ledger.load(settings)
    sess = session.state_for(settings)
    checks = doctor.diagnose(led, settings, sess, journal=learning.load())

    print(f"{BAR}\nDOCTOR  {sess.now_et:%Y-%m-%d %H:%M %Z}   {settings.account_label}\n{BAR}")
    for c in checks:
        print(f"  {_MARK.get(c.state, ' ')} {c.label:24} {c.detail}")
        if c.fix:
            print(f"      -> {c.fix}")
    print(f"\n{BAR}")
    print(doctor.verdict(checks))
    print(BAR)
    return 1 if any(c.state == doctor.BLOCK for c in checks) else 0


def cmd_status(args, settings: Settings) -> int:
    led = ledger_mod.Ledger.load(settings)
    sess = session.state_for(settings)
    print(f"{BAR}\n{settings.account_label} [{led.mode}]   opened {led.created_at[:10]}")
    print(f"{sess.banner}\n{BAR}")
    _print_positions(led)
    return 0


def cmd_close(args, settings: Settings) -> int:
    led = ledger_mod.Ledger.load(settings)
    pos = led.by_id(args.position_id)
    if pos is None or pos.status != "open":
        print(f"no open position {args.position_id}")
        return 1
    ch = chains.get_chain(pos.symbol, settings.chain_source, expiration=pos.expiration)
    debit = args.debit if args.debit is not None else paper_broker.cost_to_close(ch, pos, settings)
    if debit is None:
        print("could not price the close; pass --debit to override.")
        return 1
    fees = round(pos.fees_paid, 2)
    led.close_position(pos, debit, args.reason, fees=fees)
    led.save()
    print(f"closed {pos.id} {pos.symbol} {pos.short_strike:g}/{pos.long_strike:g}p "
          f"for a ${debit * 100 * pos.contracts:,.0f} debit -> realized "
          f"{_money(pos.realized_pl)} ({pos.close_reason})")
    _print_positions(led)
    dashboard.render(led, proposer.load(), settings, session.state_for(settings))
    return 0


def cmd_dashboard(args, settings: Settings) -> int:
    led = ledger_mod.Ledger.load(settings)
    path = dashboard.render(led, proposer.load(), settings, session.state_for(settings))
    print(f"wrote {path}")
    return 0


def cmd_watch(args, settings: Settings) -> int:
    """Refresh the watchlist. Observation only -- this never opens anything.

    That is why it can run around the clock while `propose` cannot: pricing a
    name at 02:00 tells you where it stands, filling one there does not.
    """
    # A failed screen used to leave NO trace: health.record was the last line
    # of this function, so a run that raised looked exactly like a run that
    # never fired. That is the difference between "the watchlist is stale
    # because the network was down" and "the timer is dead", and it is the only
    # thing that makes a refresh cadence verifiable rather than assumed.
    try:
        res = pipeline.run_screen(settings, symbols=args.symbols, progress=_progress,
                                  cache_max_age_min=args.max_cache_age)
        led = ledger_mod.Ledger.load(settings)
        cands = pipeline.shortlist(res, include_tight=True)
        sized = pipeline.size_candidates(cands, res, settings)
        pipeline.apply_earnings(sized, res, settings)
        wl = watchlist.build(res, sized, led, settings, contracts=args.contracts)
    except Exception as exc:                       # noqa: BLE001 -- then re-raised
        health.record("watch", ok=False, detail=f"{type(exc).__name__}: {exc}"[:200])
        raise

    print(BAR)
    print(f"WATCHLIST  {res.session.now_et:%Y-%m-%d %H:%M %Z}   {len(wl.entries)} name(s)")
    print(f"{res.session.banner}")
    if not wl.tradeable:
        print("  ! prices below are a "
              f"{wl.quote_quality.replace('_', ' ')}, not a live market -- they say "
              "where a name stands, not what it would fill at")
    print(BAR)

    rows = [["ticker", "signal", "spread", "exp", "premium", "collat", "rate",
             "cushion", "off-high"]]
    for e in wl.entries:
        rows.append([
            e.symbol, e.signal,
            f"{e.short_strike:g}/{e.long_strike:g}p" if e.has_spread else "-",
            e.expiration[5:] if e.expiration else "-",
            f"${e.credit_dollars:,.0f}" if e.has_spread else "-",
            f"${e.collateral:,.0f}" if e.has_spread else "-",
            f"{e.roc:.1%}" if e.has_spread else "-",
            f"{e.cushion:.1%}" if e.has_spread else "-",
            f"{e.pct_off_high:.0%}",
        ])
    w = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for i, r in enumerate(rows):
        print("  " + "  ".join(c.ljust(w[j]) for j, c in enumerate(r)))
        if i == 0:
            print("  " + "  ".join("-" * x for x in w))

    for sig in (watchlist.READY, watchlist.BLOCKED, watchlist.NO_FIT):
        n = wl.count(sig)
        if n:
            print(f"\n{sig} ({n}):")
            for e in [x for x in wl.entries if x.signal == sig][:6]:
                print(f"  {e.symbol:6} {e.reason}")
                for b in e.blockers:
                    print(f"         - {b}")

    path = watchlist.save(wl)
    print(f"\nsaved: {path}")
    health.record("watch", detail=f"{len(wl.entries)} name(s) watched")
    dashboard.render(led, proposer.load(), settings, res.session)
    return 0


# The knobs exposed on the CLI. Deliberately a curated list, not every field:
# the fill model and the liquidity gates are how the paper account stays honest,
# and they should be edited in the file with a reason, not flipped in passing.
_CONFIG_GROUPS = (
    ("Trading", (
        ("paper_trading", "master switch -- off means nothing opens at all"),
        # Read carefully before rewording: this said "off = the agent opens clear
        # proposals itself", which is backwards, on the one setting that decides
        # whether trades happen without a human looking at them.
        ("auto_approve", "ON = the agent opens clear proposals itself, no human "
                         "sign-off (paper only)"),
        ("auto_exit", "act on take-profit / stop decisions automatically"),
    )),
    ("Per-trade rules  (blank = use the skill's number)", (
        ("max_collateral_per_trade", f"skill: {_money(STRATEGY.max_collateral_per_trade)}"),
        ("max_loss_per_trade", "same thing said the other way; the tighter one binds"),
        ("min_credit_per_trade", f"skill: {_money(STRATEGY.min_credit_per_trade)}"),
        ("max_credit_per_trade", "skill: no cap"),
        ("min_otm_cushion", f"skill: {STRATEGY.min_otm_cushion:.0%} below spot"),
        ("take_profit_pct", f"skill: {STRATEGY.take_profit_pct:.0%} of max credit"),
    )),
    ("Stops", (
        ("stop_loss_credit_multiple", "close when buyback costs this many x the credit"),
        ("stop_loss_pct_of_max_loss", "... or when down this fraction of max loss"),
    )),
    ("Portfolio caps", (
        ("max_total_collateral", "across the whole book"),
        ("max_open_positions", ""),
        ("max_positions_per_sector", ""),
        ("max_positions_per_ticker", ""),
    )),
    ("Self-learning  (suggestions only; the agent applies none of them)", (
        ("self_repair", "let the agent bench symbols whose chains keep failing"),
        ("learning_min_sample", "closed trades before any lesson is drawn at all"),
        ("learning_min_group", "trades needed on each side of a comparison"),
        ("learning_min_effect", "win-rate gap that counts as a real difference"),
        ("learning_fault_threshold", "data failures before a symbol is benched"),
        ("learning_quarantine_days", "how long a bench lasts before it expires"),
    )),
    ("Account and ops", (
        ("mode", "paper | live -- live is refused in code, see paper_broker"),
        ("account_label", ""),
        ("starting_cash", "only read when the ledger is first created"),
        ("chain_source", "yfinance | robinhood | model"),
        ("opening_settle_minutes", "no opening inside this many minutes of the bell"),
    )),
)
_CONFIG_KEYS = {k for _, rows in _CONFIG_GROUPS for k, _ in rows}


def _coerce(key: str, raw: str):
    """Turn a CLI string into the field's type, using the declared annotation."""
    ann = str(Settings.__dataclass_fields__[key].type)
    if raw.strip().lower() in ("none", "null", "") and "None" in ann:
        return None
    if "bool" in ann:
        v = raw.strip().lower()
        if v in ("true", "on", "yes", "1"):
            return True
        if v in ("false", "off", "no", "0"):
            return False
        raise ValueError(f"{key} is a switch: use on/off (got {raw!r})")
    if "int" in ann:
        return int(raw)
    if "float" in ann:
        return float(raw)
    return raw


def cmd_config(args, settings: Settings) -> int:
    if args.set:
        for pair in args.set:
            if "=" not in pair:
                print(f"expected key=value, got {pair!r}")
                return 1
            key, raw = pair.split("=", 1)
            key = key.strip()
            if key not in _CONFIG_KEYS:
                print(f"{key!r} is not settable here. `./run.py config` lists what is; "
                      f"anything else is edited in data/settings.json with a reason.")
                return 1
            try:
                val = _coerce(key, raw)
            except ValueError as exc:
                print(f"{exc}")
                return 1
            before = getattr(settings, key)
            setattr(settings, key, val)
            print(f"  {key}: {_cfg_val(before)} -> {_cfg_val(val)}")
            # The dashboard writes an override that is applied AFTER
            # settings.json. Leaving it in place would make this command look
            # like it had been ignored.
            if config_mod.clear_override(key):
                print("    (cleared the value set from the dashboard)")
        path = settings.save()
        print(f"\nsaved: {path}")

    print(f"\n{BAR}\nCONFIG  {settings.account_label} [{settings.mode}]\n{BAR}")
    for title, rows in _CONFIG_GROUPS:
        print(f"\n{title}")
        for key, note in rows:
            print(f"  {key:<28} {_cfg_val(getattr(settings, key)):<12} {note}")

    dev = settings.deviations()
    print(f"\n{BAR}")
    if dev:
        print("MOVED AWAY FROM THE SKILL BASELINE:")
        for d in dev:
            print(f"  - {d}")
        print("\nThese are carried on every ticket and shown on the dashboard, so a "
              "\nproposal is never read as if it met the standard rule.")
    else:
        print("Running the skill's numbers exactly -- no deviations.")
    if not settings.require_approval():
        print(f"\nPer-trade human approval is OFF. `./run.py propose` will open every "
              f"\nclear proposal itself, recording the approver as "
              f"\n{settings.auto_approver()!r}. This is paper-only: a live ledger "
              f"\nalways requires a human, and no setting here can change that.")
    print("\n  ./run.py config --set max_collateral_per_trade=750 --set auto_approve=off")
    return 0


def _cfg_val(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "on" if v else "OFF"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def cmd_universe(args, settings: Settings) -> int:
    if args.refresh:
        u = universe.refresh_from_wikipedia()
        print(f"refreshed {len(u.frame)} constituents from {u.source} ({u.as_of})")
    else:
        u = universe.load()
        print(f"{len(u.frame)} constituents, list built {u.as_of} "
              f"({u.staleness_days()} days ago, source: {u.source})")
    print(u.frame.sector.value_counts().to_string())
    return 0


def cmd_chain_requests(args, settings: Settings) -> int:
    """Emit the chains an agent should pull through the Robinhood MCP server."""
    path = DATA_DIR / "last_screen.json"
    if not path.exists():
        print("run `./run.py screen` first.")
        return 1
    blob = json.loads(path.read_text())
    buckets = {screener.PRIMARY} | ({screener.NEAR_TIGHT} if args.include_tight else set())
    rows = [c for c in blob["candidates"] if c["bucket"] in buckets]
    exp = args.expiration or pipeline.resolve_expiration(rows[0]["symbol"], settings) if rows else None
    out = [{"symbol": c["symbol"], "expiration": exp, "spot": c["spot"],
            "strike_low": round(c["spot"] * (1 - STRATEGY.max_otm_cushion), 2),
            "strike_high": round(c["spot"] * (1 - STRATEGY.min_otm_cushion), 2)}
           for c in rows]
    dest = DATA_DIR / "chain_requests.json"
    dest.write_text(json.dumps({"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
                                "expiration": exp, "requests": out}, indent=2))
    print(json.dumps(out, indent=2))
    print(f"\nsaved: {dest}\nFeed each into the Robinhood MCP chain pull, then "
          f"`python3 tools/rh_ingest.py` to write the snapshots.")
    return 0


def cmd_viewer(args, settings: Settings) -> int:
    """Dashboard logins. Add, rotate, list, revoke.

    Every login sees the SAME page. There is no admin tier and no per-user
    view, so anything reachable from the page is reachable by everyone added
    here.

    What they WILL see: the account label, cash, every position, every closed
    trade and the full event log. Decide that is fine to share before sharing.

    What they can CHANGE: only config.DASHBOARD_SETTABLE -- today that is
    max_open_positions, inside its bounds. paper_trading, auto_approve, mode
    and starting_cash are deliberately off that list, so nothing reachable from
    a browser can arm trading or waive the per-trade human approval gate. A
    viewer still cannot approve a trade or reach the agent.
    """
    from pathlib import Path as _Path

    from pcs import authd, viewers
    path = authd.VIEWERS_FILE
    try:
        if args.action == "list":
            rows = viewers.load(path)
            if not rows:
                print(f"no logins in {path}. Add one with: ./run.py viewer add <name>")
                return 1
            print(f"logins that can reach the dashboard ({path}):")
            for v in rows:
                print(f"  {v.name}")
            return 0

        if not args.name:
            print(f"usage: ./run.py viewer {args.action} <username>")
            return 1

        if args.action == "add":
            password = args.password or viewers.generate_password()
            existed = viewers.add(path, args.name, password)
            try:
                domain = _Path("/etc/pcs/domain").read_text().strip()
            except OSError:
                domain = ""
            print(f"\n{'password rotated for' if existed else 'added'}: {args.name}")
            print(f"  url:      https://{domain or '<your-domain>'}/")
            print(f"  user:     {args.name}")
            print(f"  password: {password}")
            print("\nShown once. It is stored salted and hashed and cannot be read")
            print("back -- re-run this command to rotate it if it is lost.")
            print("Send it over something private. A password in a chat log is a")
            print("password in everyone's chat log.")
            return 0

        viewers.remove(path, args.name)
        print(f"revoked: {args.name}")
        print("Sessions are signed and stateless, so one already open stays valid")
        print("until it expires. To cut every session now, delete the signing key")
        print("and restart: sudo rm /etc/pcs/session.key && "
              "sudo systemctl restart pcs-authd")
        return 0
    except viewers.ViewerError as exc:
        print(f"error: {exc}")
        return 1
    except PermissionError:
        print(f"error: cannot write {path} -- run this with sudo.")
        return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["yfinance", "robinhood", "model"],
                    help="option chain source (default: settings)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("screen", help="run the screen")
    p.add_argument("--symbols", nargs="*", help="limit to these tickers")
    p.add_argument("--verbose", action="store_true", help="list the excluded buckets too")
    p.add_argument("--max-cache-age", type=float, default=0.0, metavar="MIN",
                   help="reuse cached price snapshots up to this many minutes old")
    p.set_defaults(fn=cmd_screen)

    p = sub.add_parser("propose", help="screen -> size -> risk -> tickets")
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--expiration", help="force an expiration (YYYY-MM-DD)")
    p.add_argument("--contracts", type=int, default=1)
    p.add_argument("--include-tight", action="store_true",
                   help="also size the NEAR_BELOW_TIGHT bucket")
    p.add_argument("--allow-unknown-earnings", action="store_true",
                   help="propose names whose next earnings date could not be confirmed")
    p.add_argument("--max-cache-age", type=float, default=0.0, metavar="MIN",
                   help="reuse cached price snapshots up to this many minutes old")
    p.add_argument("--no-auto-open", action="store_true",
                   help="write tickets only, even if auto_approve is on")
    p.set_defaults(fn=cmd_propose)

    p = sub.add_parser("approve", help="human gate: open a proposal in the paper account")
    p.add_argument("proposal_id")
    p.add_argument("--approver", help="who is approving this trade "
                   "(required unless auto_approve is on for this paper account)")
    p.add_argument("--override", action="store_true", help="approve despite a portfolio limit")
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("reject", help="mark a proposal rejected")
    p.add_argument("proposal_id")
    p.add_argument("--reason", default="not taken")
    p.set_defaults(fn=cmd_reject)

    p = sub.add_parser("mark", help="refresh marks, then take any exit that is due")
    p.add_argument("--no-auto-exit", action="store_true",
                   help="decide but do not execute: print the exits that are due "
                        "and leave every position open")
    p.set_defaults(fn=cmd_mark)

    p = sub.add_parser("learn", help="what the closed record supports; run self-repair")
    p.set_defaults(fn=cmd_learn)

    p = sub.add_parser("doctor", help="why has nothing opened? every gate, in order")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("status", help="account and positions")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("close", help="close a paper position")
    p.add_argument("position_id")
    p.add_argument("--reason", default="manual close")
    p.add_argument("--debit", type=float, help="override the closing debit, per share")
    p.set_defaults(fn=cmd_close)

    p = sub.add_parser("dashboard", help="rebuild dashboard.html")
    p.set_defaults(fn=cmd_dashboard)

    p = sub.add_parser("watch", help="refresh the watchlist (never opens anything)")
    p.add_argument("--symbols", nargs="*", help="limit to these tickers")
    p.add_argument("--contracts", type=int, default=1)
    p.add_argument("--max-cache-age", type=float, default=45.0, metavar="MIN",
                   help="reuse cached price snapshots up to this old (default 45)")
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("config", help="view or change the configurable rules")
    p.add_argument("--set", action="append", metavar="KEY=VALUE",
                   help="set a value and persist it (repeatable)")
    p.set_defaults(fn=cmd_config)

    p = sub.add_parser("universe", help="inspect or refresh the S&P 500 list")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(fn=cmd_universe)

    p = sub.add_parser("chain-requests", help="list chains to pull via the Robinhood MCP")
    p.add_argument("--expiration")
    p.add_argument("--include-tight", action="store_true")
    p.set_defaults(fn=cmd_chain_requests)

    p = sub.add_parser("viewer", help="dashboard logins: add, list, revoke")
    p.add_argument("action", choices=["add", "list", "remove"])
    p.add_argument("name", nargs="?", help="username (not needed for `list`)")
    p.add_argument("--password", help="set one instead of generating a password")
    p.set_defaults(fn=cmd_viewer)

    args = ap.parse_args(argv)
    settings = Settings.load()
    if args.source:
        settings.chain_source = args.source
    return args.fn(args, settings)


if __name__ == "__main__":
    sys.exit(main())
