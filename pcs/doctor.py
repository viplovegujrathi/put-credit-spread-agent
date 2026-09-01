"""Why has nothing opened?

Every gate between "the market is open" and "a position exists" lives in a
different module, each refusing for its own good reason and each printing to a
log nobody reads on a timer. The dashboard shows the *result* -- a flat book --
and a flat book looks identical whether the agent screened the index and found
nothing, or the master switch is off, or the timer never fired at all.

This walks the whole chain in the order the gates actually bind and names the
first one that stops a fill. Offline by design: it reads the ledger, the saved
proposals, the settings and the clock, and never touches the network, so it can
be run at any hour and answers in under a second. That also means it reports on
the LAST run rather than performing a new one -- which is the question being
asked. "Would it trade if it ran now" is `./run.py propose`.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from dataclasses import dataclass

from .config import LOG_DIR, PROPOSALS_JSON, Settings
from .ledger import Ledger
from .session import SessionState

# A gate either stops a fill outright or it does not. `warn` is for a fact worth
# knowing that is not itself blocking -- a cap that is close, a stale watchlist.
BLOCK, WARN, OK, INFO = "BLOCK", "WARN", "OK", "INFO"


@dataclass
class Check:
    state: str
    label: str
    detail: str
    fix: str = ""


def _svc(unit: str) -> tuple[str, str]:
    """Ask systemd about a unit. Returns ("", "") anywhere systemd is not the
    thing running this -- a laptop must not report a missing timer as a fault."""
    if not shutil.which("systemctl"):
        return "", ""
    try:
        r = subprocess.run(["systemctl", "show", unit, "--no-pager",
                            "--property=ActiveState,Result,ExecMainStatus,"
                            "ActiveEnterTimestamp,NextElapseUSecRealtime"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "", ""
    if r.returncode != 0:
        return "", ""
    kv = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    return kv.get("Result", ""), kv.get("ActiveEnterTimestamp", "")


def _last_propose() -> tuple[list[dict], str]:
    if not PROPOSALS_JSON.exists():
        return [], ""
    try:
        raw = json.loads(PROPOSALS_JSON.read_text())
    except (OSError, ValueError):
        return [], ""
    rows = raw if isinstance(raw, list) else raw.get("proposals", [])
    when = max((r.get("created_at", "") for r in rows), default="")
    return rows, when


def _age(stamp: str) -> str:
    if not stamp:
        return "never"
    try:
        d = dt.datetime.fromisoformat(stamp[:19])
    except ValueError:
        return stamp
    hrs = (dt.datetime.now() - d).total_seconds() / 3600
    if hrs < 1:
        return f"{hrs * 60:.0f}m ago"
    return f"{hrs:.0f}h ago" if hrs < 48 else f"{hrs / 24:.0f}d ago"


def diagnose(led: Ledger, settings: Settings, sess: SessionState,
             journal=None) -> list[Check]:
    """Every gate, in the order it binds. Earlier ones make later ones moot."""
    out: list[Check] = []
    A = out.append

    # -- 1. the switches, which override everything downstream ---------------
    if not settings.paper_trading:
        A(Check(BLOCK, "master switch", "paper_trading is OFF -- the screen, sizing "
                "and tickets all still run, and open_approved refuses every fill.",
                "./run.py config --set paper_trading=on"))
    else:
        A(Check(OK, "master switch", "paper_trading is on"))

    if led.mode != "paper":
        A(Check(BLOCK, "ledger mode", f"the ledger is {led.mode!r}, not paper. "
                "open_approved refuses a non-paper ledger in code; a live order is "
                "placed by a human through the broker.",
                "this is intentional -- there is no setting that lifts it"))
    else:
        A(Check(OK, "ledger mode", "paper"))

    if settings.require_approval():
        why = ("mode is not paper, so approval can never be waived"
               if settings.mode != "paper" else "auto_approve is off")
        A(Check(BLOCK, "approval", f"per-trade human sign-off is required ({why}). "
                "`propose` writes tickets and stops; nothing opens by itself.",
                "./run.py approve <proposal-id> --approver \"your name\""
                if settings.mode == "paper" else ""))
    else:
        A(Check(OK, "approval", f"auto-approve is on (paper only); fills are recorded "
                f"as {settings.auto_approver()!r}"))

    # -- 2. the clock --------------------------------------------------------
    if not sess.is_trading_day:
        A(Check(BLOCK, "trading day", f"{sess.now_et:%A %d %b} is not a US market day"))
    elif not sess.can_open_positions:
        A(Check(BLOCK, "opening range", sess.open_block_reason,
                f"lifts at {sess.settle_until:%H:%M} ET"
                if sess.settle_until else ""))
    elif not sess.is_open:
        A(Check(BLOCK, "market hours", f"the market is {sess.phase}. Auto-open holds "
                "outside regular hours -- a fill against the afternoon's last print "
                "is a price nobody could have traded on.",
                "a human may still approve a ticket after reading it"))
    else:
        A(Check(OK, "market", f"{sess.phase}, quotes grade {sess.quote_quality}"))
    if sess.is_open and sess.quote_quality != "live":
        A(Check(WARN, "quote quality", f"quotes grade {sess.quote_quality} -- sizing "
                f"moves toward the natural credit, so fewer names clear the floor"))

    # -- 3. room in the book -------------------------------------------------
    n, cap = len(led.open_positions), settings.max_open_positions
    if n >= cap:
        A(Check(BLOCK, "position count", f"{n} of {cap} open -- the book is full",
                "./run.py config --set max_open_positions=N, or close something"))
    else:
        A(Check(OK, "position count", f"{n} of {cap} open, room for {cap - n} more"))

    used, tot = led.collateral_held, settings.max_total_collateral
    if used >= tot:
        A(Check(BLOCK, "collateral cap", f"${used:,.0f} of ${tot:,.0f} deployed",
                "./run.py config --set max_total_collateral=N"))
    elif tot - used < 200:
        A(Check(WARN, "collateral cap", f"${tot - used:,.0f} of headroom left -- "
                f"below the collateral of most $5-wide spreads"))
    else:
        A(Check(OK, "collateral cap", f"${used:,.0f} of ${tot:,.0f}, "
                f"${tot - used:,.0f} free"))

    # The balance floor sits under every other cap: an account may never commit
    # more max loss than it has free cash to pay.
    bp = led.buying_power
    if bp < 100:
        A(Check(BLOCK, "available balance", f"${bp:,.2f} free -- under the $100 "
                f"capital at risk of even a $1-wide spread (cash ${led.cash:,.2f} "
                f"less ${led.capital_at_risk:,.0f} already at risk)",
                "close a position"))
    elif bp < 500:
        A(Check(WARN, "available balance", f"${bp:,.2f} free -- enough for a $1 or "
                f"$2.50 width, not a $5"))
    else:
        A(Check(OK, "available balance", f"${bp:,.2f} free"))

    full = [s for s, c in led.sector_counts().items()
            if c >= settings.max_positions_per_sector]
    if full:
        A(Check(WARN, "sector caps", f"at the {settings.max_positions_per_sector}"
                f"-position cap in: {', '.join(full)} -- names in those sectors are "
                f"blocked, others are not"))

    # -- 4. what the agent benched on itself --------------------------------
    if journal is not None:
        from . import learning
        benched = sorted(learning.blocked_symbols(journal))
        if benched:
            A(Check(WARN, "self-repair bench", f"{', '.join(benched)} skipped by "
                    f"`propose` after repeated data failures; expires on its own",
                    "./run.py learn"))

    # -- 5. did the last run actually produce anything? ---------------------
    props, when = _last_propose()
    if not props:
        A(Check(WARN, "last propose run", "no saved proposals at all -- either "
                "`propose` has never completed, or it found nothing that cleared.",
                "./run.py propose"))
    else:
        clear = [p for p in props if p.get("risk_ok")]
        opened = [p for p in props if p.get("status") == "approved"]
        pending = [p for p in props if p.get("status") == "pending"]
        A(Check(INFO, "last propose run",
                f"{_age(when)}: {len(props)} proposal(s), {len(clear)} clear of "
                f"portfolio limits, {len(opened)} opened, {len(pending)} still pending"))
        if pending and clear and not settings.require_approval():
            A(Check(WARN, "pending but auto-approve is on",
                    f"{len(pending)} proposal(s) sat unopened on a run that was allowed "
                    f"to open them -- a gate inside open_approved refused the fill.",
                    "check logs/propose.log for the HELD lines"))
        blocked = [r for p in props for r in p.get("risk_reasons", [])]
        if blocked:
            seen, uniq = set(), []
            for r in blocked:
                head = r.split(":")[0]
                if head not in seen:
                    seen.add(head)
                    uniq.append(r)
            for r in uniq[:4]:
                A(Check(INFO, "portfolio limit hit", r))

    # -- 6. did the timer fire at all? --------------------------------------
    # The question the dashboard cannot answer: a dead scheduler and a quiet
    # market render identically.
    for unit in ("pcs-propose.service", "pcs-mark.service", "pcs-watch.service"):
        result, at = _svc(unit)
        if not result:
            continue
        stamp = at.split(" ", 1)[1] if " " in at else at
        if result == "success" and at:
            A(Check(OK, unit, f"last run {stamp} -- exit ok"))
        elif result == "success":
            A(Check(WARN, unit, "enabled but has never run"))
        else:
            A(Check(BLOCK, unit, f"last run {stamp or 'unknown'} FAILED ({result})",
                    f"journalctl -u {unit} -n 60 --no-pager"))

    log = LOG_DIR / "propose.log"
    if log.exists():
        try:
            tail = [ln for ln in log.read_text(errors="replace").splitlines()
                    if "HELD" in ln or "Traceback" in ln or "Error" in ln]
        except OSError:
            tail = []
        for ln in tail[-3:]:
            A(Check(WARN, "propose.log", ln.strip()[:160]))
    return out


def verdict(checks: list[Check]) -> str:
    blocks = [c for c in checks if c.state == BLOCK]
    if not blocks:
        return ("Nothing is blocking a fill. If the book is still flat, the screen "
                "found no name that cleared the rules -- which is a valid outcome, "
                "not a fault. `./run.py propose` runs it now and shows the working.")
    first = blocks[0]
    return (f"BLOCKED at '{first.label}': {first.detail.splitlines()[0]}"
            + (f"\n  fix: {first.fix}" if first.fix else ""))
