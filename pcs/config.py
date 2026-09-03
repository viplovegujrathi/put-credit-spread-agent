"""Configuration for the S&P 500 beaten-down put credit spread agent.

Two tiers of settings live here:

  STRATEGY RULES  - fixed by the sp500-put-credit-spread-agent skill. Do not
                    change these without the user explicitly asking for this
                    run. They are grouped under `STRATEGY` and validated at
                    import so a typo cannot silently loosen risk.

  PORTFOLIO/OPS   - the numbers the skill leaves to the user (account size,
                    total collateral cap, max positions, sector caps) plus
                    plumbing (data source, liquidity gates, fill model).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
RH_CHAIN_DIR = DATA_DIR / "rh_chains"
SP500_CSV = DATA_DIR / "sp500.csv"
LEDGER_JSON = DATA_DIR / "ledger.json"
PROPOSALS_JSON = DATA_DIR / "proposals.json"
DASHBOARD_HTML = ROOT / "dashboard.html"

# Where a web server serves the page from, if one does. `render()` writes
# DASHBOARD_HTML next to the code and then publishes a copy here. Without that
# second step the served page is only refreshed by a redeploy: propose, mark
# and watch all rewrite dashboard.html while nginx keeps serving the copy made
# at install time, so a live book renders as an empty one and the header
# timestamp only moves when someone deploys.
# Set PCS_WEB_ROOT to override; otherwise publish to the deploy's web root when
# it exists, which is exactly the case where something is serving it.
_web_root = os.environ.get("PCS_WEB_ROOT") or "/var/www/pcs"
WEB_INDEX = (Path(_web_root) / "index.html") if Path(_web_root).is_dir() else None

# Writable state belonging to the login service, which runs as `pcs` and must
# never be able to touch the ledger. Credentials stay in /etc/pcs, root-owned
# and read-only to that process; anything the web service WRITES lives here.
_state = os.environ.get("PCS_STATE_DIR") or "/var/lib/pcs"
STATE_DIR = Path(_state) if Path(_state).is_dir() else DATA_DIR

# Settings the dashboard may change, and the bounds it must respect.
#
# An allowlist, not "anything on Settings". This file is reachable from a
# browser over the internet, every login sees the same page and there is no
# admin tier -- so the set of things a viewer can change has to be chosen
# rather than inherited. `paper_trading`, `auto_approve`, `mode` and
# `starting_cash` are absent on purpose: nothing reachable from a browser may
# arm trading or waive the human approval gate.
DASHBOARD_SETTABLE: dict[str, tuple[int, int]] = {
    "max_open_positions": (1, 50),
}

# The dashboard writes here rather than into data/settings.json. Two reasons:
# the login service's write access stays confined to one directory that holds
# no account state, and this path is outside $APP, which is rsynced with
# --delete on every redeploy.
OVERRIDES_JSON = Path(os.environ.get("PCS_OVERRIDES")
                      or (STATE_DIR / "overrides.json"))


def _atomic_write(path: Path, text: str) -> None:
    """Write via a sibling temp file and rename.

    Two processes now write settings: the CLI and the login service. A reader
    that catches a half-written file gets a JSON error on the file that holds
    the risk limits, so the write is never allowed to be partially visible.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def load_overrides(path: Path | None = None) -> dict:
    """What the dashboard has changed. Never fatal: a corrupt or unreadable
    override file falls back to the configured value rather than refusing to
    start, because this file is written by a web request."""
    path = path or OVERRIDES_JSON
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in DASHBOARD_SETTABLE}


def set_override(key: str, value, path: Path | None = None) -> None:
    """Record a dashboard change. Raises ValueError if the key is not settable
    from a browser or the value is out of bounds -- the caller is a web
    request, so neither check may be skipped."""
    if key not in DASHBOARD_SETTABLE:
        raise ValueError(f"{key!r} cannot be set from the dashboard")
    lo, hi = DASHBOARD_SETTABLE[key]
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a whole number") from None
    if not lo <= value <= hi:
        raise ValueError(f"{key} must be between {lo} and {hi}")
    path = path or OVERRIDES_JSON
    cur = load_overrides(path)
    cur[key] = value
    _atomic_write(path, json.dumps(cur, indent=2))


def clear_override(key: str, path: Path | None = None) -> bool:
    """Drop a dashboard override so the CLI value takes effect again.

    Called by `run.py config --set`. Without it the two writers disagree
    silently and whichever wrote first appears to have been ignored.
    """
    path = path or OVERRIDES_JSON
    cur = load_overrides(path)
    if key not in cur:
        return False
    del cur[key]
    _atomic_write(path, json.dumps(cur, indent=2))
    return True


# --------------------------------------------------------------------------
# Tier 1: strategy rules (fixed by the skill)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Strategy:
    # --- 1.2 screen -------------------------------------------------------
    min_pct_off_52w_high: float = 0.15      # >= 15% off the 52-week high
    max_pct_off_52w_high: float | None = None   # no ceiling, by design

    # Distance from the 50dma, signed: (price - dma50) / dma50
    primary_band: tuple[float, float] = (-0.08, -0.03)   # the intended setup
    tight_band: tuple[float, float] = (-0.03, 0.0)       # near but little pullback
    stretched_band: tuple[float, float] = (-0.12, -0.08) # marginal, excluded by default
    broken_below: float = -0.12                          # excluded: broken down
    # anything > 0.0 is ABOVE_50DMA -> different thesis, reported separately

    # --- 1.5 expiration ---------------------------------------------------
    target_dte: int = 32
    dte_window: tuple[int, int] = (28, 38)   # acceptable range around the target

    # --- 1.4 sizing -------------------------------------------------------
    max_collateral_per_trade: float = 1000.0   # (width * 100) - (credit * 100)
    min_credit_per_trade: float = 100.0        # credit * 100
    max_credit_per_trade: float | None = None  # no upper cap, by design
    min_otm_cushion: float = 0.03              # short strike >= 3% below spot
    max_otm_cushion: float = 0.20              # don't chase strikes into the noise
    candidate_widths: tuple[float, ...] = (1.0, 2.5, 5.0, 10.0)  # filtered to
                                               # strikes that actually exist

    # --- 1.7 management ---------------------------------------------------
    # Book at the LOWER edge of the band: the rule fires on the first mark at or
    # above `take_profit_pct`, so the upper bound is the line the account should
    # never still be holding past, not a second target. If a position is sitting
    # above it, an exit was due and was not taken -- the market was shut or the
    # mark was stale -- and that is an alert, not a trade decision
    # (see pcs/health.py).
    #
    # Earlier is the risk-reducing direction: less time in the trade, less
    # gamma, more realised wins, at a lower average capture per trade.
    take_profit_pct: float = 0.50        # close at 50% of max credit (50-75% band)
    take_profit_band: tuple[float, float] = (0.50, 0.75)
    manage_dte: int = 21                 # start watching for a roll here
    defend_dte: int = 7                  # short strike tested this close -> act


STRATEGY = Strategy()

_OVERRIDABLE = ("max_collateral_per_trade", "min_credit_per_trade",
                "max_credit_per_trade", "min_otm_cushion", "take_profit_pct")


# --------------------------------------------------------------------------
# Tier 2: portfolio + operations (user-settable)
# --------------------------------------------------------------------------
@dataclass
class Settings:
    # --- account ----------------------------------------------------------
    mode: str = "paper"                  # "paper" | "live" (live is gated, see run.py)
    starting_cash: float = 3000.0
    account_label: str = "Paper $3,000"

    # --- portfolio-level risk (section 1.8; skill leaves these to the user) -
    max_total_collateral: float = 2400.0   # 80% of $3,000 -- keeps dry powder
    # A ceiling on COUNT, not on money. The binding constraints on this account
    # are max_total_collateral and the buying-power floor -- ten $5-wide spreads
    # would want ~$5,000 of collateral against a $2,400 cap, so the money runs
    # out first and this number stops being the thing that binds.
    max_open_positions: int = 10
    max_positions_per_sector: int = 2
    # Positions in one NAME, not contracts. Above 1 this is a ladder: two
    # expirations or two strikes on the same underlying, which is a real way to
    # scale into a setup and also the fastest way to concentrate an account.
    #
    # It does not bind on its own. A ticker has exactly one GICS sector, so
    # `max_positions_per_sector` is the ceiling this can ever reach -- at a
    # sector cap of 2, a per-ticker cap of 5 still tops out at 2. Raise the
    # sector cap too if a deeper ladder in one name is actually wanted.
    max_positions_per_ticker: int = 5

    # Days a symbol is kept out of new positions after one closes at a LOSS.
    # A stop that fires on a bad mark and a stop that fires on a real move look
    # identical from the outside, and without this the agent re-proposes the
    # name the same afternoon -- so a single bad print can be paid for twice.
    # A winning close does not cool off: selling premium again in a name that
    # just paid out is the strategy working, not churn.
    # 0 disables it.
    reentry_cooldown_days: int = 5

    # --- data sources -----------------------------------------------------
    chain_source: str = "yfinance"       # "yfinance" | "robinhood" | "model"
    universe_source: str = "cache"       # "cache" | "wikipedia"
    screen_batch_size: int = 40          # tickers per yfinance bulk download
    history_period: str = "1y"

    # --- liquidity gates (doc 1.1: reject untradeable chains) -------------
    # A vertical trades as a package, so the package market is the gate that
    # matters; per-leg checks only weed out genuinely dead strikes.
    min_open_interest: int = 25              # per leg -- OI 0 strikes are dead
    preferred_open_interest: int = 100       # scoring only, not a gate
    min_leg_volume: int = 0                  # per leg, same-session (0 = don't gate)
    max_package_spread_pct_of_width: float = 0.25   # scaled by session quality
    max_leg_spread_pct: float = 0.60         # per-leg backstop, scaled by session
    max_leg_spread_abs: float = 0.20         # ... or this many dollars, whichever is looser

    # --- fill model -------------------------------------------------------
    # nat = short_bid - long_ask   (package natural: worst realistic fill)
    # mid = short_mid - long_mid   (package mid, using the broker mark when it
    #                               exists -- this is the basis the strategy
    #                               doc's reference table was calibrated on)
    # used = mid - slippage_frac * (mid - nat)
    #
    # slippage_frac is scaled by quote quality: a wide book at the closing bell
    # says less about tomorrow's fill than a wide book at 11:00, so the sizing
    # basis moves toward the natural credit as confidence drops.
    slippage_frac: float = 0.15          # sizing basis: a realistic marketable limit
    slippage_by_quote_quality: dict = field(default_factory=lambda: {
        "live": 0.15, "closing_snapshot": 0.35, "stale": 0.50})
    paper_slippage_frac: float = 0.25    # simulated fill: deliberately worse

    # The natural credit is the floor a fill can realistically land on. Sizing
    # on the mid while the natural is far below it is how a wide book turns
    # into a proposal that never fills at the number shown -- so the natural
    # must clear this fraction of the $100 minimum, not merely be disclosed.
    min_natural_credit_frac: float = 0.75
    commission_per_contract: float = 0.0     # Robinhood: $0 on equity options
    per_contract_fees: float = 0.06          # ~ORF/OCC pass-through per contract leg

    # --- timing -----------------------------------------------------------
    # Wait this many minutes after the 09:30 ET bell before opening anything,
    # paper included. Enforced as a hard gate in paper_broker.open_approved.
    opening_settle_minutes: int = 30

    # --- exits (section 1.7) ----------------------------------------------
    # The agent acts on these itself rather than only advising. Closing reduces
    # risk and never opens it, so it does not go through the entry approval
    # gate -- but it is paper-only: see pcs/exits.py for the boundary.
    auto_exit: bool = True               # act on exit decisions in paper mode
    stop_loss_credit_multiple: float = 2.0    # close if buyback >= 2x the credit
    stop_loss_pct_of_max_loss: float = 0.50   # ... or if down half the max loss

    # --- strategy overrides ------------------------------------------------
    # STRATEGY holds the skill's numbers. These override them for THIS account.
    # None means "use the skill value", so an untouched install behaves exactly
    # as the mandate says. Anything set here is reported as a deviation on the
    # ticket and the dashboard rather than silently replacing the rule.
    #
    # For a credit spread max loss IS the collateral -- (width - credit) x 100.
    # max_loss_per_trade is offered because that is how people think about it;
    # whichever of the two is tighter binds.
    max_collateral_per_trade: float | None = None
    max_loss_per_trade: float | None = None
    min_credit_per_trade: float | None = None
    max_credit_per_trade: float | None = None
    min_otm_cushion: float | None = None
    take_profit_pct: float | None = None

    # --- approval ----------------------------------------------------------
    # The master switch. Off means nothing opens at all: the agent screens,
    # sizes and writes tickets, and open_approved refuses every fill. Use it to
    # pause the book without unwinding it -- exits and marking keep running,
    # because stopping new risk is not a reason to stop managing old risk.
    paper_trading: bool = True

    # Off means the agent opens clear proposals itself, without per-trade
    # sign-off. PAPER ONLY -- see require_approval(); a live ledger always
    # requires a human, and the agent cannot place a live order regardless.
    auto_approve: bool = False

    # --- self-learning (pcs/learning.py) -----------------------------------
    # The agent keeps a journal of closed trades and its own operational
    # failures. What it may do with each is deliberately different.
    #
    # Trades produce SUGGESTIONS only. These floors are what stops a run of
    # luck from being read as a finding: no lesson under `min_sample` closed
    # trades, no comparison unless both sides hold `min_group`, and no lesson
    # at all unless the win-rate gap clears `min_effect`. They are high on
    # purpose -- the cost of a missed pattern is a slightly worse rule, and
    # the cost of a false one is a rule fitted to noise.
    self_repair: bool = True             # off = journal still records, never acts
    learning_min_sample: int = 8         # closed trades before any lesson at all
    learning_strong_sample: int = 20     # ... before a lesson is called "supported"
    learning_min_group: int = 4          # per side of a two-way comparison
    learning_min_effect: float = 0.20    # win-rate gap that counts as a difference
    # Faults are the part the agent repairs itself, because benching a symbol
    # can only ever remove a candidate -- it has no path to opening anything.
    learning_fault_threshold: int = 3    # data failures before a symbol is benched
    learning_fault_window_days: int = 7  # ... counted inside this window
    learning_quarantine_days: int = 5    # how long a bench lasts before it expires

    # --- go-live readiness (pcs/readiness.py) ------------------------------
    # What the broker says, recorded after being checked rather than assumed.
    # Both go stale, so the timestamp is part of the fact.
    broker_option_level: str = ""        # "option_level_3" is what a spread needs
    broker_buying_power: float = 0.0
    broker_checked_at: str = ""          # ISO date of the last broker check
    min_closed_trades_for_live: int = 20
    min_win_rate_for_live: float = 0.60

    # --- earnings ---------------------------------------------------------
    earnings_buffer_days: int = 2        # exclude if earnings <= expiry + buffer

    # -- resolved rules ----------------------------------------------------
    def strategy(self) -> Strategy:
        """STRATEGY with this account's overrides applied.

        Returns a real frozen Strategy, so every consumer keeps reading the
        same attributes and cannot tell the difference -- the only place that
        knows an override happened is `deviations()`, which reports it.
        """
        over = {k: getattr(self, k) for k in _OVERRIDABLE
                if getattr(self, k) is not None}
        if self.max_loss_per_trade is not None:
            cap = min(over.get("max_collateral_per_trade",
                               STRATEGY.max_collateral_per_trade),
                      self.max_loss_per_trade)
            over["max_collateral_per_trade"] = cap
        return replace(STRATEGY, **over) if over else STRATEGY

    def deviations(self) -> list[str]:
        """Every rule this account has moved away from the skill baseline."""
        out = []
        eff = self.strategy()
        for k in _OVERRIDABLE:
            base, now = getattr(STRATEGY, k), getattr(eff, k)
            if base != now:
                out.append(f"{k.replace('_', ' ')}: {_fmt(base)} -> {_fmt(now)}")
        if not self.paper_trading:
            out.append("paper trading: ON -> OFF (nothing will open)")
        if self.auto_approve and self.mode == "paper":
            out.append("per-trade human approval: required -> OFF (paper only)")
        return out

    def require_approval(self) -> bool:
        """Whether a human must sign off before a position opens.

        Auto-approve is a paper-only convenience. A live ledger always requires
        a human, and no setting can change that -- the agent additionally has
        no path to placing a live order at all.
        """
        return not (self.auto_approve and self.mode == "paper")

    def auto_approver(self) -> str:
        """Who the ledger records when no human signed off.

        Never a person's name. An audit trail that reads like a human approved
        a trade nobody looked at is worse than no audit trail.
        """
        return "agent (auto-approve, paper)"

    @classmethod
    def load(cls, path: Path | None = None,
             overrides: Path | None = None) -> Settings:
        """settings.json first, then whatever the dashboard changed.

        The override is applied LAST so a value set from the page takes effect
        on the agent's very next run with no restart and no redeploy -- that is
        the whole point of making it settable there. `run.py config --set`
        clears the override for any key it writes, so the last human action
        wins whichever way it was made.
        """
        path = path or (DATA_DIR / "settings.json")
        s = cls()
        if path.exists():
            raw = json.loads(path.read_text())
            for k, v in raw.items():
                if hasattr(s, k):
                    setattr(s, k, v)
        for k, v in load_overrides(overrides).items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s

    def save(self, path: Path | None = None) -> Path:
        path = path or (DATA_DIR / "settings.json")
        _atomic_write(path, json.dumps(asdict(self), indent=2))
        return path


def _fmt(v) -> str:
    if v is None:
        return "none"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:.0%}" if 0 < v < 1 else f"${v:,.0f}"
    return str(v)


def _validate() -> None:
    s = STRATEGY
    assert s.min_pct_off_52w_high >= 0.15, "screen must stay at >=15% off the high"
    assert s.max_collateral_per_trade <= 1000.0, "per-trade collateral cap is $1,000"
    assert s.min_credit_per_trade >= 100.0, "per-trade credit floor is $100"
    assert s.max_credit_per_trade is None, "credit has no upper cap, by design"
    assert s.primary_band[0] < s.primary_band[1] <= 0.0
    # The trigger has to sit inside the band it is described by. The band is
    # rendered on the dashboard and on every ticket as the rule in force, so a
    # trigger outside it makes the page state something the engine does not do.
    lo, hi = s.take_profit_band
    assert lo <= s.take_profit_pct <= hi, "take profit must fire inside its band"
    assert 0.0 < lo < hi <= 1.0, "take profit band must be an ordered fraction"
    assert s.broken_below <= s.stretched_band[0]


_validate()

for _d in (DATA_DIR, LOG_DIR, RH_CHAIN_DIR):
    os.makedirs(_d, exist_ok=True)
