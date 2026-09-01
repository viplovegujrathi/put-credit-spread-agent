#!/usr/bin/env python3
"""S&P 500 beaten-down put credit spread agent -- command line.

    ./run.py screen                 run the screen, show every bucket
    ./run.py propose                screen -> size -> risk -> proposal tickets
    ./run.py approve P260831-01 --approver "Viplove"    <- the human gate
    ./run.py reject  P260831-01 --reason "too close to earnings"
    ./run.py mark                   refresh marks, surface management actions
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
    exits,
    marketdata,
    paper_broker,
    pipeline,
    proposer,
    screener,
    session,
    universe,
)
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

    print(BAR)
    print(f"PROPOSE  {res.session.now_et:%Y-%m-%d %H:%M %Z}")
    print(f"account: {settings.account_label} [{led.mode}]   cash {_money(led.cash)}   "
          f"buying power {_money(led.buying_power)}   open {len(led.open_positions)}")
    print(BAR)
    for w in res.warnings:
        print(f"  ! {w}")
    print(f"\nshortlist: {len(cands)} name(s) passed the screen"
          f"{' (incl. NEAR_BELOW_TIGHT)' if args.include_tight else ''}")
    if not cands:
        print("  nothing passed the screen today - no proposals.")
        return 0
    print("  " + ", ".join(c.symbol for c in cands))

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
        print(proposer.ticket(p))
    if skipped:
        print(f"\nSKIPPED ({len(skipped)}):")
        for s in skipped:
            print(f"  - {s}")

    path = proposer.save(props)
    print(f"\nsaved: {path}")
    clear = [p.id for p in props if p.risk_ok]
    if clear:
        print(f"\nNothing has been placed. To open one in the paper account:\n"
              f"  ./run.py approve {clear[0]} --approver \"your name\"")
        if not res.session.can_open_positions:
            print(f"  (held until {res.session.settle_until:%H:%M} ET - "
                  f"{res.session.open_block_reason.split(' - ')[0]})")
    dashboard.render(led, props, settings, res.session)
    return 0


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

    led = ledger_mod.Ledger.load(settings)
    sess = session.state_for(settings)
    sp = Spread(**p.spread)
    print(proposer.ticket(p))
    print(f"\napprover: {args.approver}")
    try:
        pos = paper_broker.open_approved(led, sp, p.sector, p.contracts, settings,
                                         proposal_id=p.id, approved_by=args.approver,
                                         sess=sess)
    except paper_broker.MarketNotReady as exc:
        print(f"\nHELD - {exc}")
        print(f"  {p.id} stays pending. Re-run this same command after "
              f"{sess.settle_until:%H:%M} ET.")
        return 1
    except paper_broker.InsufficientFunds as exc:
        print(f"\nHELD - {exc}")
        print(f"  {p.id} stays pending; the ledger is unchanged.")
        return 1
    p.status, p.approved_by = "approved", args.approver
    p.approved_at = dt.datetime.now().isoformat(timespec="seconds")
    p.position_id = pos.id
    proposer.save(props)
    led.save()
    haircut = (f"sized at ${sp.credit:.2f}; paper fills take a deliberate haircut"
               if pos.credit_open < sp.credit else
               f"capped at the ${sp.credit:.2f} the ticket was sized on")
    print(f"\nPAPER FILL  position {pos.id}")
    print(f"  filled at ${pos.credit_open:.2f} credit ({haircut})")
    print(f"  net credit {_money(pos.credit_dollars)}   collateral {_money(pos.collateral)}")
    print(f"  cash {_money(led.cash)}   buying power {_money(led.buying_power)}")
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


def cmd_mark(args, settings: Settings) -> int:
    led = ledger_mod.Ledger.load(settings)
    if not led.open_positions:
        print("no open positions to mark.")
        return 0
    sess = session.state_for(settings)
    print(f"{BAR}\nMARK  {sess.now_et:%Y-%m-%d %H:%M %Z}   {sess.banner}\n{BAR}")
    syms = sorted({p.symbol for p in led.open_positions})
    spots = marketdata.live_quote(syms) if sess.is_open else {}
    for s, snap in marketdata.fetch_snapshots(syms, batch_size=len(syms)).items():
        spots.setdefault(s, snap.spot)
    notes, fresh = paper_broker.mark_positions(led, settings, spots)

    decisions = exits.review(led, settings, fresh)
    auto = settings.auto_exit and led.mode == "paper" and not args.no_auto_exit
    actionable = [(p, d) for p, d in decisions if d.act]

    if auto and actionable:
        acted = paper_broker.apply_exits(led, settings, fresh)
        led.save()
        print(f"\nEXITS TAKEN ({len(acted)}) -- decided and executed by the agent")
        for pos, d in acted:
            print(f"  {d.headline}  {pos.symbol} {pos.short_strike:g}/{pos.long_strike:g} "
                  f"[{pos.id}]  realized {_money(pos.realized_pl)}")
            print(f"      {d.reason}")
    else:
        led.save()
        if actionable:
            why = ("live mode -- a close is an order, so this is a ticket for you to place"
                   if led.mode != "paper" else "auto-exit is off")
            print(f"\nEXITS DUE ({len(actionable)}) -- {why}:")
            for pos, d in actionable:
                print(f"  {d.headline}  {pos.symbol} [{pos.id}]: {d.reason}")
                print(exits.ticket(pos, d))

    _print_positions(led)
    stale = [p for p in led.open_positions if p.id not in fresh]
    if stale:
        print(f"\n  ! {len(stale)} position(s) did not re-price; no exit was decided on "
              f"a stale mark: {', '.join(p.symbol for p in stale)}")
    watch = [(p, d) for p, d in decisions if not d.act and d.reason]
    if watch:
        print("\nWATCHING (no action due):")
        for pos, d in watch:
            print(f"  {pos.symbol} {pos.short_strike:g}/{pos.long_strike:g} [{pos.id}]: {d.reason}")
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
    p.set_defaults(fn=cmd_propose)

    p = sub.add_parser("approve", help="human gate: open a proposal in the paper account")
    p.add_argument("proposal_id")
    p.add_argument("--approver", required=True, help="who is approving this trade")
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

    p = sub.add_parser("status", help="account and positions")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("close", help="close a paper position")
    p.add_argument("position_id")
    p.add_argument("--reason", default="manual close")
    p.add_argument("--debit", type=float, help="override the closing debit, per share")
    p.set_defaults(fn=cmd_close)

    p = sub.add_parser("dashboard", help="rebuild dashboard.html")
    p.set_defaults(fn=cmd_dashboard)

    p = sub.add_parser("universe", help="inspect or refresh the S&P 500 list")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(fn=cmd_universe)

    p = sub.add_parser("chain-requests", help="list chains to pull via the Robinhood MCP")
    p.add_argument("--expiration")
    p.add_argument("--include-tight", action="store_true")
    p.set_defaults(fn=cmd_chain_requests)

    args = ap.parse_args(argv)
    settings = Settings.load()
    if args.source:
        settings.chain_source = args.source
    return args.fn(args, settings)


if __name__ == "__main__":
    sys.exit(main())
