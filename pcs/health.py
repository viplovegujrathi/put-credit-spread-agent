"""What the agent actually did, and when -- the half the ledger cannot hold.

A ledger records things that happened. Three of the states that cost the most
money are things that did NOT happen:

  * a timer that never fired,
  * a mark that failed to re-price,
  * an exit that was due and was held.

All three render identically to a quiet market: a flat book and yesterday's
numbers. `cmd_mark` already knows about all three and prints them to stdout,
which on a timer is a file nobody reads.

So this module persists them. Every run appends one `Run` to data/health.json
with what it priced and what it decided but could not act on, and `alerts()`
turns that record plus the ledger into the short list of things an operator
would want pushed at them rather than displayed.

Deliberately dependency-free and never fatal: a failure to write the health
file must not fail the mark that was otherwise fine. Health telemetry that can
break trading is worse than no telemetry.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field

from .config import DATA_DIR

HEALTH_JSON = DATA_DIR / "health.json"

# The mark timer fires every 15 minutes during regular hours (see
# deploy/pcs-mark.timer). Two missed intervals is the point at which "the timer
# is slow" stops being the likely explanation.
MARK_INTERVAL_MIN = 15
# The watchlist is meant to refresh several times a day (the timer asks for
# hourly). Four a day is one every six hours, so eight hours without a fresh
# file means the cadence has already fallen below the floor -- late enough not
# to fire on one missed run, early enough to still be the same trading day.
WATCH_STALE_AFTER_H = 8
MARK_MISSING_AFTER_MIN = MARK_INTERVAL_MIN * 2

# How many runs to keep. Enough to see a week of marks; small enough that the
# file stays a few hundred KB and the dashboard can read it on every render.
KEEP = 400

CRITICAL, WARNING, INFO = "critical", "warning", "info"
_SEV_RANK = {CRITICAL: 0, WARNING: 1, INFO: 2}


@dataclass
class Run:
    """One execution of one command. `ok` is about the run, not the outcome:
    a mark that priced nothing because the market is shut is still ok=True."""
    kind: str                 # propose | mark | watch
    at: str
    ok: bool = True
    detail: str = ""
    # marks
    positions: int = 0
    marked: int = 0           # re-priced this run
    stale: int = 0            # open, did not re-price
    # exits -- the three outcomes cmd_mark distinguishes and the page did not
    exits_due: int = 0
    exits_taken: int = 0
    exits_held: int = 0       # due, market shut
    exits_skipped: int = 0    # due, but the mark behind them was stale
    held_detail: list[str] = field(default_factory=list)
    # Recorded rather than re-derived: a position that failed to re-price today
    # may still carry a perfectly good mark from yesterday, so `marked_at` does
    # not identify it. Only the run that attempted the mark knows.
    stale_symbols: list[str] = field(default_factory=list)

    @property
    def when(self) -> dt.datetime | None:
        try:
            return dt.datetime.fromisoformat(self.at)
        except ValueError:
            return None

    @property
    def unactioned(self) -> int:
        return self.exits_held + self.exits_skipped


@dataclass
class Health:
    runs: list[Run] = field(default_factory=list)

    def last(self, kind: str) -> Run | None:
        for r in reversed(self.runs):
            if r.kind == kind:
                return r
        return None

    def runs_today(self, kind: str, today: dt.date | None = None) -> int:
        today = today or dt.date.today()
        return sum(1 for r in self.runs
                   if r.kind == kind and (r.when.date() if r.when else None) == today)

    def recent(self, kind: str, n: int = 10) -> list[Run]:
        return [r for r in self.runs if r.kind == kind][-n:]


def load(path=None) -> Health:
    path = path or HEALTH_JSON
    if not path.exists():
        return Health()
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        # A corrupt health file must not stop a mark. Start a fresh record.
        return Health()
    runs = []
    for r in raw.get("runs", []):
        try:
            runs.append(Run(**r))
        except TypeError:
            # A record written by an older or newer build. Skip the row rather
            # than lose the whole file to one unknown field.
            continue
    return Health(runs=runs)


def save(health: Health, path=None) -> None:
    path = path or HEALTH_JSON
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(
            {"runs": [asdict(r) for r in health.runs[-KEEP:]]}, indent=2))
        tmp.replace(path)
    except OSError:
        pass          # never fatal -- see the module docstring


def record(kind: str, path=None, **stats) -> Run:
    """Append one run. Returns it, so a caller can log what was recorded."""
    health = load(path)
    run = Run(kind=kind, at=dt.datetime.now().isoformat(timespec="seconds"), **stats)
    health.runs.append(run)
    save(health, path)
    return run


# --- alerts ----------------------------------------------------------------
# Push, not pull. The whole premise of an unattended agent is that nobody is
# looking at the page, so the things that matter have to come find you. This
# function only DETECTS; delivery is the caller's problem, and the dashboard
# renders whatever comes back as a banner.

def prior_evidence(led, kind: str, watch_at: str = "") -> str:
    """The newest timestamp proving a loop ran, from somewhere other than the
    health record itself -- or "".

    The health record starts empty the day this module is deployed, so on a box
    that has been trading for weeks `last("mark")` is None and the page would
    say "never run" about a loop that has been running all along -- a critical
    alert that is simply false, on the first view, which is exactly how a
    reader learns to skip the panel. The ledger is the older witness: a
    position cannot exist unless `propose` ran, and cannot carry `marked_at`
    unless `mark` did.
    """
    if kind == "mark":
        return max((p.marked_at for p in led.open_positions if p.marked_at), default="")
    if kind == "propose":
        return max((p.opened_at for p in led.positions if p.opened_at), default="")
    if kind == "watch":
        # watchlist.json stamps itself, so the file IS the receipt. Passed in
        # rather than read here: this module knows about the ledger and the
        # clock, and dragging the screener import chain into telemetry to
        # answer one question is the wrong trade.
        return watch_at
    return ""


@dataclass
class Alert:
    kind: str
    severity: str
    title: str
    detail: str
    symbol: str = ""

    @property
    def rank(self) -> int:
        return _SEV_RANK.get(self.severity, 9)


def _age_min(stamp: str, now: dt.datetime | None = None) -> float | None:
    try:
        return ((now or dt.datetime.now())
                - dt.datetime.fromisoformat(stamp[:19])).total_seconds() / 60
    except (ValueError, TypeError):
        return None


def alerts(led, settings, health: Health | None = None, sess=None,
           now: dt.datetime | None = None, watch_at: str = "") -> list[Alert]:
    """The five things the operator seat asked to be told, not shown.

    Ordered by severity, then by how much money the state is costing while it
    goes unnoticed.
    """
    now = now or dt.datetime.now()
    health = health if health is not None else load()
    out: list[Alert] = []
    last_mark = health.last("mark")

    # 3. An exit was due and was NOT taken. First, because it is the state that
    #    costs the most and is currently the quietest: the position is still
    #    open, still moving, and the page shows the same amber pill it shows
    #    for an exit that was handled.
    if last_mark and last_mark.unactioned:
        why = []
        if last_mark.exits_held:
            why.append(f"{last_mark.exits_held} held (market shut)")
        if last_mark.exits_skipped:
            why.append(f"{last_mark.exits_skipped} skipped (stale mark)")
        out.append(Alert(
            "exit_unactioned", CRITICAL,
            f"{last_mark.unactioned} exit(s) were due and NOT taken",
            "; ".join(why) + ". The position is still open and still moving. "
            + ("; ".join(last_mark.held_detail) if last_mark.held_detail else "")))

    # 2. Short strike breached, at ANY dte -- not only at defend_dte, where
    #    exits.decide() starts acting. Between "comfortable" and "the agent
    #    will act in four days" there is a week of drawdown.
    for p in led.open_positions:
        if p.mark_spot and p.mark_spot <= p.short_strike:
            out.append(Alert(
                "strike_breached", CRITICAL,
                f"{p.symbol} is through its short strike",
                f"spot {p.mark_spot:,.2f} vs {p.short_strike:g} short, {p.dte} DTE, "
                f"unrealised {p.open_pl:+,.0f}", p.symbol))

    # 4. The mark loop is not running, or ran and could not price something.
    if led.open_positions:
        trading_now = bool(sess and sess.is_open)
        if last_mark is None:
            # No RECORD is not the same as never ran. Fall back to the ledger
            # before crying wolf, and only then treat the marks as absent.
            seen = prior_evidence(led, "mark")
            if seen:
                last_mark = Run("mark", seen, detail="reconstructed from the ledger")
            else:
                out.append(Alert(
                    "mark_never_ran", CRITICAL, "the mark loop has never run",
                    "every number on the page is the price at fill. Run ./run.py mark"))
        if last_mark is not None:
            age = _age_min(last_mark.at, now)
            if age is not None and trading_now and age > MARK_MISSING_AFTER_MIN:
                out.append(Alert(
                    "mark_stalled", CRITICAL,
                    f"no mark for {age:.0f} minutes during market hours",
                    f"the timer fires every {MARK_INTERVAL_MIN} minutes. Every P&L "
                    f"figure on the page is that old. "
                    f"systemctl status pcs-mark.timer"))
            if last_mark.stale:
                names = ", ".join(last_mark.stale_symbols) or "see logs"
                out.append(Alert(
                    "mark_failed", WARNING,
                    f"{last_mark.stale} position(s) failed to re-price",
                    f"their P&L is frozen at the last good mark and no exit can be "
                    f"decided on them: {names}"))

    # 6. The watchlist is the one thing on this page with no ledger behind it:
    #    if the refresh stops, every name keeps its last quote and the tab goes
    #    on looking populated and current. The file stamps itself, so age is
    #    the honest measure -- not whether the timer fired, and not whether the
    #    job exited zero.
    last_watch = health.last("watch")
    seen_at = watch_at or prior_evidence(led, "watch", watch_at)
    age_h = None
    if seen_at:
        mins = _age_min(seen_at, now)
        age_h = mins / 60 if mins is not None else None
    if age_h is not None and age_h > WATCH_STALE_AFTER_H:
        out.append(Alert(
            "watchlist_stale", WARNING,
            f"the watchlist has not refreshed for {age_h:.0f} hours",
            f"every name still shows its last quote, so the tab looks current "
            f"and is not. The timer asks for hourly and the floor is one every "
            f"{WATCH_STALE_AFTER_H}h. systemctl status pcs-watch.timer"))
    elif not seen_at and last_watch is None:
        out.append(Alert(
            "watch_never_ran", WARNING, "the watchlist has never been built",
            "nothing is being screened between proposals. Run ./run.py watch"))
    if last_watch is not None and not last_watch.ok:
        out.append(Alert(
            "watch_failed", WARNING, "the last watchlist refresh failed",
            f"{last_watch.detail or 'see logs'} "
            f"-- journalctl -u pcs-watch -n 50"))

    # 5. Book-wide drawdown, before any single stop fires. Each position can sit
    #    just under its own stop while the book as a whole is deeply underwater.
    risk = led.capital_at_risk
    unreal = led.unrealized_pl
    if risk > 0 and unreal < 0 and -unreal >= 0.5 * led.collateral_held:
        out.append(Alert(
            "book_drawdown", CRITICAL,
            f"the book is down ${-unreal:,.0f}",
            f"{-unreal / led.collateral_held:.0%} of the ${led.collateral_held:,.0f} "
            f"at risk, with no individual stop fired yet"))

    # 1. Anything the agent closed on its own. Informational by the time you
    #    read it -- the trade is done -- but it is the one event that must never
    #    be discovered by noticing a position is missing.
    for e in reversed(led.events):
        if e.get("kind") != "position_closed":
            continue
        age = _age_min(e.get("at", ""), now)
        if age is None or age > 24 * 60:
            break
        out.append(Alert(
            "agent_closed", INFO,
            f"the agent closed {e.get('symbol', '?')} "
            f"({str(e.get('reason', '')).replace('_', ' ')})",
            f"realised ${e.get('realized_pl', 0):+,.0f}", str(e.get("symbol", ""))))

    return sorted(out, key=lambda a: a.rank)
