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
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
RH_CHAIN_DIR = DATA_DIR / "rh_chains"
SP500_CSV = DATA_DIR / "sp500.csv"
LEDGER_JSON = DATA_DIR / "ledger.json"
PROPOSALS_JSON = DATA_DIR / "proposals.json"
DASHBOARD_HTML = ROOT / "dashboard.html"


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
    take_profit_pct: float = 0.55        # close at 55% of max credit (50-65% band)
    take_profit_band: tuple[float, float] = (0.50, 0.65)
    manage_dte: int = 21                 # start watching for a roll here
    defend_dte: int = 7                  # short strike tested this close -> act


STRATEGY = Strategy()


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
    max_open_positions: int = 4
    max_positions_per_sector: int = 2
    max_positions_per_ticker: int = 1

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

    # --- earnings ---------------------------------------------------------
    earnings_buffer_days: int = 2        # exclude if earnings <= expiry + buffer

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or (DATA_DIR / "settings.json")
        s = cls()
        if path.exists():
            raw = json.loads(path.read_text())
            for k, v in raw.items():
                if hasattr(s, k):
                    setattr(s, k, v)
        return s

    def save(self, path: Path | None = None) -> Path:
        path = path or (DATA_DIR / "settings.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))
        return path


def _validate() -> None:
    s = STRATEGY
    assert s.min_pct_off_52w_high >= 0.15, "screen must stay at >=15% off the high"
    assert s.max_collateral_per_trade <= 1000.0, "per-trade collateral cap is $1,000"
    assert s.min_credit_per_trade >= 100.0, "per-trade credit floor is $100"
    assert s.max_credit_per_trade is None, "credit has no upper cap, by design"
    assert s.primary_band[0] < s.primary_band[1] <= 0.0
    assert s.broken_below <= s.stretched_band[0]


_validate()

for _d in (DATA_DIR, LOG_DIR, RH_CHAIN_DIR):
    os.makedirs(_d, exist_ok=True)
