import atexit
import datetime as dt
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Before anything from `pcs` is imported: `config` fixes its paths at import.
# A deploy runs this suite inside the live install, as the user the timers run
# as, so every path a test can write must point away from it -- the data, the
# logs and the dashboard, the web root nginx serves, and the login service's
# state. 2026-10-17: a test that saved proposals replaced the box's tickets.
# The web root and state folders are left nonexistent on purpose, which is how
# `config` reads "nothing serves this" and "no login service".
_RUNTIME = Path(tempfile.mkdtemp(prefix="pcs-tests-"))
atexit.register(shutil.rmtree, _RUNTIME, ignore_errors=True)
os.environ["PCS_RUNTIME"] = str(_RUNTIME)
os.environ["PCS_WEB_ROOT"] = str(_RUNTIME / "no-web-root")
os.environ["PCS_STATE_DIR"] = str(_RUNTIME / "no-state-dir")
os.environ.pop("PCS_OVERRIDES", None)
# The login service's signing key: rewriting it signs every viewer out.
os.environ["PCS_SESSION_KEY"] = str(_RUNTIME / "session.key")
os.environ["PCS_VIEWERS"] = str(_RUNTIME / "viewers")

from pcs.chains import PutChain, PutQuote  # noqa: E402
from pcs.config import Settings  # noqa: E402
from pcs.session import SessionState  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.chain_source = "yfinance"
    return s


@pytest.fixture
def live_session():
    return SessionState(dt.datetime(2026, 8, 31, 11, 0), True, "open", "live", "open")


def make_chain(symbol="TST", expiration=None, spot=100.0, strikes=None,
               price_at=None, oi=500, volume=100, iv=0.30, spread=0.05,
               mark=True) -> PutChain:
    """Synthetic chain with a controllable premium curve.

    `price_at(strike)` returns the mid for that strike; the default is a rough
    convex OTM put curve that behaves like a real one.
    """
    expiration = expiration or (dt.date.today() + dt.timedelta(days=32)).isoformat()
    strikes = strikes or [spot - i for i in range(0, 31)]
    if price_at is None:
        def price_at(k):
            otm = max(spot - k, 0.0)
            return round(max(0.05, 4.5 * 2.718 ** (-otm / 6.0)), 2)
    puts = []
    for k in strikes:
        m = price_at(k)
        puts.append(PutQuote(
            strike=round(k, 2), bid=round(max(m - spread, 0.01), 2),
            ask=round(m + spread, 2), last=m, volume=volume, open_interest=oi,
            iv=iv, mark=m if mark else 0.0))
    return PutChain(symbol, expiration, spot, puts, "test", "now")
