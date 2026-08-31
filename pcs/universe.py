"""Step 1 -- S&P 500 constituent universe, with GICS sector for the
concentration check in section 1.8.

Membership changes several times a year, so the cache carries the date it was
built and `staleness_days()` lets callers warn instead of silently trading an
out-of-date list.
"""

from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

from .config import SP500_CSV

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
# Fallback if Wikipedia's markup shifts or the fetch fails.
GITHUB_CSV = ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies"
              "/main/data/constituents.csv")
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


@dataclass
class Universe:
    frame: pd.DataFrame           # columns: symbol, name, sector, sub_industry
    as_of: dt.date
    source: str

    @property
    def symbols(self) -> list[str]:
        return self.frame["symbol"].tolist()

    def sector_of(self, symbol: str) -> str:
        row = self.frame.loc[self.frame.symbol == symbol, "sector"]
        return row.iloc[0] if len(row) else "Unknown"

    def staleness_days(self) -> int:
        return (dt.date.today() - self.as_of).days


def _get(url: str) -> str:
    # requests carries certifi's CA bundle; pandas' urlopen path does not on
    # this python.org build, so route every fetch through requests.
    resp = requests.get(url, headers=_UA, timeout=30)
    resp.raise_for_status()
    return resp.text


def refresh_from_wikipedia(path: Path = SP500_CSV) -> Universe:
    """Refresh the constituent cache. Tries Wikipedia, falls back to the
    datasets/s-and-p-500-companies CSV so a markup change is not fatal."""
    tbl, source = None, ""
    try:
        tables = pd.read_html(io.StringIO(_get(WIKI_URL)))
        for cand in tables:
            if "Symbol" in cand.columns and "GICS Sector" in cand.columns:
                tbl, source = cand, "wikipedia"
                break
    except Exception:
        tbl = None
    if tbl is None:
        tbl = pd.read_csv(io.StringIO(_get(GITHUB_CSV)))
        source = "datasets/s-and-p-500-companies"

    tbl = tbl.rename(columns={
        "Symbol": "symbol", "Security": "name", "Name": "name",
        "GICS Sector": "sector", "Sector": "sector",
        "GICS Sub-Industry": "sub_industry",
    })
    if "sub_industry" not in tbl.columns:
        tbl["sub_industry"] = ""
    tbl = tbl[["symbol", "name", "sector", "sub_industry"]].copy()
    # Wikipedia uses BRK.B / BF.B; the option venues and yfinance use BRK-B.
    tbl["symbol"] = tbl["symbol"].str.strip().str.replace(".", "-", regex=False)
    tbl = tbl.drop_duplicates("symbol").reset_index(drop=True)
    tbl["as_of"] = dt.date.today().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tbl.to_csv(path, index=False)
    return Universe(tbl.drop(columns=["as_of"]), dt.date.today(), source)


def load(path: Path = SP500_CSV, source: str = "cache") -> Universe:
    if source == "wikipedia" or not path.exists():
        return refresh_from_wikipedia(path)
    tbl = pd.read_csv(path)
    as_of = dt.date.fromisoformat(str(tbl["as_of"].iloc[0])) if "as_of" in tbl else dt.date.today()
    return Universe(tbl[["symbol", "name", "sector", "sub_industry"]], as_of, "cache")
