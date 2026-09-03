"""Step 11 -- the journal: what went wrong, and what the record actually supports.

Two different things live here, and keeping them apart is the whole design.

OUTCOMES are trades. Every closed position is flattened into the features it
had at open and the result it produced, then grouped into buckets. A bucket
only becomes a *lesson* once enough trades sit in it to mean anything; below
that floor the honest answer is "not yet", and this module says exactly that
rather than moving a rule on the strength of four fills. A lesson is a
SUGGESTION -- it prints the `config --set` line and stops there. Nothing in
this file writes settings, and nothing in it touches STRATEGY, which the skill
fixes.

FAULTS are the agent's own operational failures: a chain that would not price,
a fill refused by a gate, a provider that errored. These it does repair by
itself, because the repair is bounded and one-directional -- it can bench a
symbol, never un-bench one into a trade. A quarantine keeps a name out of
proposals for a few days and then expires on its own, so a bad afternoon at
the data provider cannot silently shrink the universe forever.

The line between the two is the point. A losing trade is not a bug to be
patched by loosening the rule that lost it -- that is how a backtest gets
fitted to its own noise, and with 8 closed trades the noise is all there is. A
chain that fails to price on four consecutive runs genuinely is a bug, and
benching that name costs nothing but one candidate.

    what it may do by itself     what it may only suggest
    -------------------------    ---------------------------------
    bench a failing symbol       change any number in Settings
    expire its own quarantine    change anything in STRATEGY
    write to data/journal.json   open, close, or size any position
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from dataclasses import asdict, dataclass, field

from .config import DATA_DIR, Settings
from .ledger import EXPIRED, Ledger, Position

JOURNAL_JSON = DATA_DIR / "journal.json"

# Faults are kept as a ring: the repair logic only ever looks at a recent
# window, and an unbounded list on a box that runs every 15 minutes is a file
# that grows forever for no one's benefit.
MAX_FAULTS = 400

WIN, LOSS, SCRATCH = "WIN", "LOSS", "SCRATCH"

# Confidence tiers. `insufficient` is a first-class answer, not a failure --
# most of this agent's life is spent there and the dashboard should say so.
INSUFFICIENT, TENTATIVE, SUPPORTED = "insufficient", "tentative", "supported"

# Fault kinds. Only `mark_failed` and `chain_error` count toward a quarantine:
# they mean the data is broken for that name. `open_blocked` is usually the
# account being full, which is the risk caps working, not a fault of the
# symbol -- it is recorded for the operator and deliberately not acted on.
MARK_FAILED, CHAIN_ERROR, OPEN_BLOCKED = "mark_failed", "chain_error", "open_blocked"
QUARANTINE_KINDS = (MARK_FAILED, CHAIN_ERROR)


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------
@dataclass
class Outcome:
    """One closed position, flattened to what can be learned from.

    Only fields the ledger genuinely recorded at open appear here. The four
    entry features at the bottom are `None` on every trade opened before the
    ledger began recording them, so each split drops the rows that never had
    the number rather than reading a missing measurement as a zero -- and
    `feature_gaps()` says how many rows that is.
    """
    id: str
    symbol: str
    sector: str
    opened_at: str
    closed_at: str
    days_held: int
    dte_at_open: int
    width: float
    contracts: int
    credit_open: float          # per share
    collateral: float
    realized_pl: float
    max_credit: float           # credit x 100 x contracts -- the most it could make
    capture: float              # realized / max_credit; 1.0 = kept the whole credit
    otm_cushion: float          # (opened_spot - short_strike) / opened_spot
    credit_per_width: float     # how rich the premium was, per dollar of width
    quote_quality: str          # session grade at fill: live | closing_snapshot | stale
    basis: str                  # live chain or modeled
    exit_kind: str              # take_profit | stop_loss | defend | expired | manual
    result: str                 # WIN | LOSS | SCRATCH
    under_strike_at_close: bool
    # The screen conditions and the strike's own numbers, as at entry. Default
    # None so a journal.json written before these existed still loads, and so a
    # trade that never recorded them is excluded from their splits instead of
    # counted at zero.
    pct_off_high: float | None = None
    pct_from_dma50: float | None = None
    short_delta: float | None = None
    iv_at_open: float | None = None


@dataclass
class Fault:
    at: str
    kind: str
    symbol: str
    detail: str


@dataclass
class Quarantine:
    """A symbol benched from proposals until `until` (exclusive)."""
    symbol: str
    until: str          # ISO date
    reason: str
    faults: int
    since: str


@dataclass
class Lesson:
    key: str            # stable across runs, so the dashboard does not churn
    title: str
    dimension: str
    finding: str
    sample: int
    confidence: str
    suggestion: str = ""   # a `config --set` line, or "" when nothing to change


@dataclass
class Journal:
    updated_at: str = ""
    outcomes: list[Outcome] = field(default_factory=list)
    faults: list[Fault] = field(default_factory=list)
    quarantines: list[Quarantine] = field(default_factory=list)
    repairs: list[dict] = field(default_factory=list)   # append-only audit trail

    @property
    def seen(self) -> set[str]:
        return {o.id for o in self.outcomes}


# --------------------------------------------------------------------------
# ingest
# --------------------------------------------------------------------------
def _exit_kind(pos: Position) -> str:
    """Map a free-text close reason back to the decision that produced it.

    `apply_exits` writes "take_profit: ..." and friends, `settle_expired`
    writes prose, and a human close writes whatever they typed -- so anything
    unrecognised is `manual`, never guessed into one of the real buckets.
    """
    if pos.status == EXPIRED:
        return "expired"
    head = (pos.close_reason or "").split(":", 1)[0].strip().lower()
    return head if head in ("take_profit", "stop_loss", "defend", "roll") else "manual"


def _result(pl: float) -> str:
    if pl > 0.5:
        return WIN
    if pl < -0.5:
        return LOSS
    return SCRATCH      # inside a dollar either way is fees, not an edge


def _days(a: str, b: str) -> int:
    try:
        return max((dt.date.fromisoformat(b[:10]) - dt.date.fromisoformat(a[:10])).days, 0)
    except ValueError:
        return 0


def to_outcome(pos: Position) -> Outcome:
    max_credit = round(pos.credit_open * 100 * pos.contracts, 2)
    cushion = ((pos.opened_spot - pos.short_strike) / pos.opened_spot
               if pos.opened_spot else 0.0)
    return Outcome(
        id=pos.id, symbol=pos.symbol, sector=pos.sector,
        opened_at=pos.opened_at, closed_at=pos.closed_at,
        days_held=_days(pos.opened_at, pos.closed_at),
        dte_at_open=_days(pos.opened_at, pos.expiration),
        width=pos.width, contracts=pos.contracts, credit_open=pos.credit_open,
        collateral=pos.collateral, realized_pl=pos.realized_pl, max_credit=max_credit,
        capture=round(pos.realized_pl / max_credit, 4) if max_credit else 0.0,
        otm_cushion=round(cushion, 4),
        credit_per_width=round(pos.credit_open / pos.width, 4) if pos.width else 0.0,
        quote_quality=pos.quote_quality or "unknown", basis=pos.basis or "unknown",
        exit_kind=_exit_kind(pos), result=_result(pos.realized_pl),
        under_strike_at_close=bool(pos.mark_spot and pos.mark_spot <= pos.short_strike),
        pct_off_high=pos.pct_off_high_at_open,
        pct_from_dma50=pos.pct_from_dma50_at_open,
        short_delta=pos.short_delta_at_open,
        iv_at_open=pos.iv_at_open,
    )


def sync(journal: Journal, led: Ledger) -> int:
    """Ingest every closed position not already recorded. Idempotent by id.

    Reading the ledger rather than being called at close-time is deliberate:
    the journal cannot miss a trade because some path forgot to notify it, and
    re-running it costs nothing.
    """
    seen = journal.seen
    added = 0
    for pos in led.closed_positions:
        if pos.id in seen:
            continue
        journal.outcomes.append(to_outcome(pos))
        added += 1
    if added:
        journal.outcomes.sort(key=lambda o: o.closed_at)
        journal.updated_at = dt.datetime.now().isoformat(timespec="seconds")
    return added


def record_fault(journal: Journal, kind: str, symbol: str, detail: str = "") -> Fault:
    f = Fault(at=dt.datetime.now().isoformat(timespec="seconds"), kind=kind,
              symbol=symbol.upper(), detail=detail[:200])
    journal.faults.append(f)
    del journal.faults[:-MAX_FAULTS]
    journal.updated_at = f.at
    return f


# --------------------------------------------------------------------------
# self-repair -- the only part that acts on its own
# --------------------------------------------------------------------------
def blocked_symbols(journal: Journal, today: dt.date | None = None) -> set[str]:
    """Symbols currently benched. Consumed by the proposal path."""
    today = today or dt.date.today()
    return {q.symbol for q in journal.quarantines
            if dt.date.fromisoformat(q.until) > today}


def self_repair(journal: Journal, settings: Settings,
                today: dt.date | None = None) -> list[str]:
    """Expire finished quarantines, then bench symbols that keep failing.

    Bounded on purpose. The only state this writes is the quarantine list, and
    a quarantine can only ever remove a name from consideration -- there is no
    input to this function that makes the agent trade something it otherwise
    would not, which is what makes running it unattended defensible.

    Faults are counted inside a recent window rather than for all time, so a
    provider outage three weeks ago does not keep a name benched today.
    """
    today = today or dt.date.today()
    actions: list[str] = []

    live = []
    for q in journal.quarantines:
        if dt.date.fromisoformat(q.until) > today:
            live.append(q)
        else:
            actions.append(f"{q.symbol}: quarantine expired, back in the universe")
    journal.quarantines = live

    if not settings.self_repair:
        return actions

    window = today - dt.timedelta(days=settings.learning_fault_window_days)
    benched = {q.symbol for q in journal.quarantines}
    counts: Counter[str] = Counter()
    for f in journal.faults:
        if f.kind not in QUARANTINE_KINDS or f.symbol in benched or not f.symbol:
            continue
        try:
            if dt.date.fromisoformat(f.at[:10]) >= window:
                counts[f.symbol] += 1
        except ValueError:
            continue

    for sym, n in sorted(counts.items()):
        if n < settings.learning_fault_threshold:
            continue
        until = today + dt.timedelta(days=settings.learning_quarantine_days)
        journal.quarantines.append(Quarantine(
            symbol=sym, until=until.isoformat(), faults=n, since=today.isoformat(),
            reason=(f"{n} data failures in {settings.learning_fault_window_days}d -- "
                    f"the chain will not price this name reliably")))
        actions.append(f"{sym}: benched until {until} after {n} data failures")

    if actions:
        stamp = dt.datetime.now().isoformat(timespec="seconds")
        journal.repairs.extend({"at": stamp, "action": a} for a in actions)
        del journal.repairs[:-MAX_FAULTS]
        journal.updated_at = stamp
    return actions


# --------------------------------------------------------------------------
# lessons -- suggestions only
# --------------------------------------------------------------------------
def _decided(rows: list[Outcome]) -> list[Outcome]:
    """Trades that actually said something. A $0.20 fee-only close is neither a
    win nor a loss, and a group made entirely of them is not a sample -- it has
    to be excluded from the SIZE test too, not only from the rate."""
    return [o for o in rows if o.result != SCRATCH]


def _rate(rows: list[Outcome]) -> float:
    """Win rate over decided trades."""
    decided = _decided(rows)
    return (sum(o.result == WIN for o in decided) / len(decided)) if decided else 0.0


def _avg(rows: list[Outcome]) -> float:
    return round(sum(o.realized_pl for o in rows) / len(rows), 2) if rows else 0.0


def _abs(v: float | None) -> float | None:
    """|v|, preserving None. `abs(None)` raises, and it would raise inside
    `_split`'s own None filter -- which has to call the key to test it."""
    return None if v is None else abs(v)


def _split(rows: list[Outcome], key, threshold: float
           ) -> tuple[list[Outcome], list[Outcome]]:
    """Two-way split, dropping rows where the feature was never recorded.

    The `None` filter is the whole reason this is not a one-liner at the call
    site. Four of the features arrived after the first trades closed, and a
    missing delta read as 0.0 would land every one of those rows in the
    low bucket -- inventing a pattern out of the date the field was added.
    """
    have = [o for o in rows if key(o) is not None]
    return ([o for o in have if key(o) < threshold],
            [o for o in have if key(o) >= threshold])


def _compare(key: str, title: str, dimension: str, low: list[Outcome],
             high: list[Outcome], low_label: str, high_label: str,
             settings: Settings, suggestion: str = "") -> Lesson | None:
    """Turn a two-way split into a lesson, or into nothing.

    Returns None when either side is too thin to compare, rather than
    reporting a 100% win rate off two trades. That is the guard that keeps
    this from being a random-number generator with a confident voice.
    """
    low, high = _decided(low), _decided(high)
    n = len(low) + len(high)
    if len(low) < settings.learning_min_group or len(high) < settings.learning_min_group:
        return None
    lo, hi = _rate(low), _rate(high)
    gap = hi - lo
    if abs(gap) < settings.learning_min_effect:
        return Lesson(key=key, title=title, dimension=dimension, sample=n,
                      confidence=SUPPORTED if n >= settings.learning_strong_sample
                      else TENTATIVE,
                      finding=(f"No difference worth acting on: {low_label} won "
                               f"{lo:.0%} of {len(low)}, {high_label} won {hi:.0%} of "
                               f"{len(high)}. Leave the rule where it is."))
    better, worse = (high_label, low_label) if gap > 0 else (low_label, high_label)
    bn, wn = (high, low) if gap > 0 else (low, high)
    return Lesson(
        key=key, title=title, dimension=dimension, sample=n,
        confidence=SUPPORTED if n >= settings.learning_strong_sample else TENTATIVE,
        finding=(f"{better} won {_rate(bn):.0%} of {len(bn)} and averaged "
                 f"${_avg(bn):,.2f}; {worse} won {_rate(wn):.0%} of {len(wn)} and "
                 f"averaged ${_avg(wn):,.2f}."),
        suggestion=suggestion if gap > 0 else "")


def _by_group(rows: list[Outcome], key, label: str, settings: Settings,
              lesson_key: str, title: str, dimension: str
              ) -> tuple[str, Lesson] | None:
    """Name the worst-performing group in a categorical split, if one stands out.

    Returns `(worst_key, lesson)` rather than the lesson alone: callers need to
    branch on WHICH group did worst, and reading that back out of the rendered
    sentence is a bug waiting for someone to reword the sentence.
    """
    groups: dict[str, list[Outcome]] = {}
    for o in _decided(rows):
        groups.setdefault(key(o), []).append(o)
    big = {k: v for k, v in groups.items() if len(v) >= settings.learning_min_group}
    if len(big) < 2:
        return None
    ranked = sorted(big.items(), key=lambda kv: _rate(kv[1]))
    (wk, wv), (bk, bv) = ranked[0], ranked[-1]
    if _rate(bv) - _rate(wv) < settings.learning_min_effect:
        return None
    return wk, Lesson(
        key=lesson_key, title=title, dimension=dimension, sample=len(rows),
        confidence=SUPPORTED if len(rows) >= settings.learning_strong_sample
        else TENTATIVE,
        finding=(f"Worst {label}: {wk} won {_rate(wv):.0%} of {len(wv)} "
                 f"(avg ${_avg(wv):,.2f}). Best: {bk} won {_rate(bv):.0%} of "
                 f"{len(bv)} (avg ${_avg(bv):,.2f})."))


def lessons(journal: Journal, settings: Settings) -> list[Lesson]:
    """Every pattern the closed record actually supports.

    Always returns at least one entry. With too few trades that entry says so
    -- an empty list would read as "nothing wrong", and "we do not know yet"
    is a different and more useful statement.
    """
    rows = journal.outcomes
    n = len(rows)
    if n < settings.learning_min_sample:
        return [Lesson(
            key="sample", title="Not enough closed trades to learn from",
            dimension="sample", sample=n, confidence=INSUFFICIENT,
            finding=(f"{n} closed trade{'s' if n != 1 else ''} on record; this agent "
                     f"will not draw a conclusion under {settings.learning_min_sample}. "
                     f"A win rate off a handful of fills is noise, and tuning a rule to "
                     f"noise is how a strategy gets worse while looking better."))]

    eff = settings.strategy()
    out: list[Lesson] = []

    # How the book actually ends. The most useful single number here, and the
    # one no split is needed for.
    kinds = Counter(o.exit_kind for o in rows)
    tp = [o for o in rows if o.exit_kind == "take_profit"]
    cap = (sum(o.capture for o in tp) / len(tp)) if tp else 0.0
    out.append(Lesson(
        key="exits", title="How positions are ending", dimension="exits", sample=n,
        confidence=SUPPORTED if n >= settings.learning_strong_sample else TENTATIVE,
        finding=("; ".join(f"{k} x{v}" for k, v in kinds.most_common())
                 + (f". Take-profit closes captured {cap:.0%} of max credit on average "
                    f"against a {eff.take_profit_pct:.0%} target." if tp else ""))))

    lo, hi = _split(rows, lambda o: o.otm_cushion, eff.min_otm_cushion + 0.015)
    lesson = _compare(
        "cushion", "Cushion at entry", "cushion", lo, hi,
        f"tight cushion (<{eff.min_otm_cushion + 0.015:.1%} OTM)",
        f"wide cushion (>={eff.min_otm_cushion + 0.015:.1%} OTM)", settings,
        suggestion=(f"./run.py config --set min_otm_cushion="
                    f"{min(eff.min_otm_cushion + 0.01, 0.10):.2f}"))
    if lesson:
        out.append(lesson)

    lo, hi = _split(rows, lambda o: o.credit_per_width, 0.20)
    lesson = _compare(
        "richness", "Premium richness at entry", "credit", lo, hi,
        "thin premium (<20c per $1 of width)", "rich premium (>=20c per $1 of width)",
        settings,
        suggestion=(f"./run.py config --set min_credit_per_trade="
                    f"{eff.min_credit_per_trade + 25:.0f}"))
    if lesson:
        out.append(lesson)

    # The screen's own two conditions. Until the ledger recorded these, the
    # agent could tell you how a trade ended and nothing about whether the
    # setup that picked it was worth picking -- which is the only question the
    # screen exists to answer.
    lo, hi = _split(rows, lambda o: o.pct_off_high, 0.25)
    lesson = _compare(
        "off_high", "How far off the 52-week high", "screen", lo, hi,
        "moderately beaten down (15-25% off)", "deeply beaten down (>=25% off)",
        settings)
    if lesson:
        out.append(lesson)

    # More negative = further below the average. The primary band is -8% to
    # -3%, so this splits the band down the middle rather than at zero.
    lo, hi = _split(rows, lambda o: o.pct_from_dma50, -0.055)
    lesson = _compare(
        "dma_depth", "Depth of the pullback to the 50-day average", "screen", lo, hi,
        "deep in the band (>5.5% below the 50dma)",
        "shallow in the band (<5.5% below the 50dma)", settings)
    if lesson:
        out.append(lesson)

    # Delta is the cleanest proxy for how much risk the strike took on, and it
    # is the one number here that a setting can act on directly.
    # Reported, never turned into a suggestion. `_compare` only offers its
    # `config --set` line when the HIGH side wins, and high here is the NEAR
    # strike -- so a win for near strikes would have printed "widen the
    # cushion", which is the opposite of what the record just said. The cushion
    # lesson above already owns that knob, and it splits the right way round.
    lo, hi = _split(rows, lambda o: _abs(o.short_delta), 0.20)
    lesson = _compare(
        "delta", "Short-leg delta at entry", "strike", lo, hi,
        "far strike (|delta| < 0.20)", "near strike (|delta| >= 0.20)", settings)
    if lesson:
        out.append(lesson)

    lo, hi = _split(rows, lambda o: o.iv_at_open, 0.40)
    lesson = _compare(
        "iv", "Implied volatility at entry", "vol", lo, hi,
        "quiet vol (IV < 40%)", "rich vol (IV >= 40%)", settings)
    if lesson:
        out.append(lesson)

    # The operationally actionable one: a paper account that does measurably
    # worse on fills taken against stale quotes is telling you the haircut is
    # too small, not that the strategy is wrong.
    found = _by_group(rows, lambda o: o.quote_quality, "quote grade at fill",
                      settings, "quotes", "Quote quality at fill", "quotes")
    if found:
        worst, lesson = found
        if worst in ("closing_snapshot", "stale"):
            # The paper haircut lives in the fill model, which `config --set`
            # deliberately does not expose -- that file is edited with a reason,
            # not flipped in passing. So the suggestion names the file.
            lesson.suggestion = ""
            lesson.finding += (f" Fills taken on a {worst.replace('_', ' ')} quote are "
                               f"doing worst; if that holds, raise "
                               f"`paper_slippage_frac` in pcs/config.py "
                               f"(currently {settings.paper_slippage_frac:.2f}).")
        out.append(lesson)

    found = _by_group(rows, lambda o: o.sector, "sector", settings,
                      "sector", "Where the losses concentrate", "sector")
    if found:
        out.append(found[1])

    return out


# Reported by `feature_gaps`, and the reason each split filters None.
_ENTRY_FEATURES = (
    ("pct_off_high", "% off the 52-week high at entry"),
    ("pct_from_dma50", "% from the 50-day average at entry"),
    ("short_delta", "short-leg delta at entry"),
    ("iv_at_open", "implied volatility at entry"),
)


def feature_gaps(journal: Journal | None = None) -> list[str]:
    """What still cannot be learned from, and why.

    Stated rather than silently absent: "no signal from % off the 52-week high"
    and "% off the 52-week high was never written down" look identical on a
    dashboard and mean completely different things.

    The four entry features are recorded now. What replaces them here is the
    honest successor problem -- every trade closed before that change has them
    as `None` and is dropped from those splits, so the sample that can speak to
    the screen is smaller than the sample overall until the book turns over.
    Pass the journal to have that counted rather than described.
    """
    out = [
        "IV RANK at entry -- IV itself is recorded now, but a rank needs a year "
        "of implied-vol history for the name and nothing collects one. Absolute "
        "IV cannot tell 40% in a quiet name from 40% in a jumpy one.",
    ]
    if journal is None:
        return out
    rows = journal.outcomes
    if not rows:
        return out
    for attr, label in _ENTRY_FEATURES:
        blind = sum(getattr(o, attr) is None for o in rows)
        if blind:
            out.insert(0, f"{label} -- missing on {blind} of {len(rows)} closed "
                          f"trade(s), which were opened before the ledger recorded "
                          f"it. Those rows are dropped from this split, not "
                          f"counted as zero.")
    return out


def summary(journal: Journal, settings: Settings) -> dict:
    rows = journal.outcomes
    return {
        "closed": len(rows),
        "needed": settings.learning_min_sample,
        "wins": sum(o.result == WIN for o in rows),
        "losses": sum(o.result == LOSS for o in rows),
        "faults": len(journal.faults),
        "quarantined": sorted(blocked_symbols(journal)),
        "repairs": len(journal.repairs),
    }


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------
def save(journal: Journal, path=None):
    # Resolved at call time, not bound as a default: a default argument is
    # evaluated once at import, which makes the destination impossible to
    # redirect and the module impossible to test without touching real data.
    path = path or JOURNAL_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written to a sibling and renamed. `mark` rewrites this file every 15
    # minutes and the dashboard reads it on every render; a plain write_text
    # truncates first, so a reader that lands in the gap gets a parse error on
    # the one page that is supposed to say what is going on.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({
        "updated_at": journal.updated_at,
        "outcomes": [asdict(o) for o in journal.outcomes],
        "faults": [asdict(f) for f in journal.faults],
        "quarantines": [asdict(q) for q in journal.quarantines],
        "repairs": journal.repairs,
    }, indent=2))
    tmp.replace(path)
    return path


def load(path=None) -> Journal:
    """Always returns a Journal. A missing file is a new account, not an error."""
    path = path or JOURNAL_JSON
    if not path.exists():
        return Journal()
    raw = json.loads(path.read_text())
    return Journal(
        updated_at=raw.get("updated_at", ""),
        outcomes=[Outcome(**o) for o in raw.get("outcomes", [])],
        faults=[Fault(**f) for f in raw.get("faults", [])],
        quarantines=[Quarantine(**q) for q in raw.get("quarantines", [])],
        repairs=raw.get("repairs", []),
    )
