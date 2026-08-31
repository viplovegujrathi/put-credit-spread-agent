#!/usr/bin/env python3
"""Turn Robinhood MCP output into an option-chain snapshot the sizer can read.

The Python process cannot call the MCP server itself -- MCP tools are held by
the agent, not the script. So the broker-data path is deliberately two-phase:

    1. ./run.py chain-requests           what to pull, and over which strikes
    2. agent runs get_option_instruments + get_option_quotes via MCP,
       and drops the raw payloads into data/rh_raw/<SYMBOL>_<EXPIRY>.json
    3. python3 tools/rh_ingest.py        normalises them into data/rh_chains/
    4. ./run.py propose --source robinhood

Robinhood's book is materially better than the free alternative: for MCD's
2026-10-02 expiration it lists 44 put strikes against Yahoo's 16, and it
carries the mark, full greeks, and its own chance-of-profit, none of which
Yahoo exposes.

Raw input shape (one file per symbol/expiration):

    {"symbol": "MCD", "expiration": "2026-10-02", "spot": 263.54,
     "instruments": [ <get_option_instruments data.instruments> ],
     "quotes":      [ <get_option_quotes  data.results[].quote> ]}
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pcs.config import DATA_DIR, RH_CHAIN_DIR  # noqa: E402

RAW_DIR = DATA_DIR / "rh_raw"


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def normalise(blob: dict) -> dict:
    """Join instruments to quotes on instrument id and emit the snapshot schema."""
    strikes = {i["id"]: _f(i.get("strike_price")) for i in blob.get("instruments", [])}
    puts = []
    for q in blob.get("quotes", []):
        iid = q.get("instrument_id")
        strike = strikes.get(iid)
        if strike is None:
            continue
        bid, ask = _f(q.get("bid_price")), _f(q.get("ask_price"))
        puts.append({
            "strike": strike,
            "bid": bid,
            "ask": ask,
            # adjusted_mark_price collapses to 0.01 on a no-bid contract; the
            # plain mark is the usable number, and a no-bid strike is rejected
            # by the optimizer's liquidity gate anyway.
            "mark": _f(q.get("mark_price")),
            "volume": int(_f(q.get("volume"))),
            "open_interest": int(_f(q.get("open_interest"))),
            "iv": _f(q.get("implied_volatility")),
            "delta": _f(q.get("delta")) if q.get("delta") is not None else None,
            "theta": _f(q.get("theta")) if q.get("theta") is not None else None,
            "vega": _f(q.get("vega")) if q.get("vega") is not None else None,
            "pop_short": _f(q.get("chance_of_profit_short")) if q.get("chance_of_profit_short") else None,
            "fill_sell": _f(q.get("high_fill_rate_sell_price")) or None,
            "fill_buy": _f(q.get("high_fill_rate_buy_price")) or None,
            "instrument_id": iid,
            "quoted_at": q.get("updated_at"),
        })
    puts.sort(key=lambda p: p["strike"])
    return {
        "symbol": blob["symbol"],
        "expiration": blob["expiration"],
        "spot": _f(blob.get("spot")),
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": "robinhood-mcp",
        "puts": puts,
    }


def ingest(path: Path, out_dir: Path = RH_CHAIN_DIR) -> Path:
    snap = normalise(json.loads(path.read_text()))
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{snap['symbol']}_{snap['expiration']}.json"
    dest.write_text(json.dumps(snap, indent=2))
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path,
                    help=f"raw payloads (default: every .json in {RAW_DIR})")
    args = ap.parse_args()
    files = args.files or sorted(RAW_DIR.glob("*.json"))
    if not files:
        print(f"nothing to ingest; drop raw MCP payloads into {RAW_DIR}")
        return 1
    for f in files:
        dest = ingest(f)
        n = len(json.loads(dest.read_text())["puts"])
        print(f"  {f.name} -> {dest.relative_to(ROOT)}  ({n} put strikes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
