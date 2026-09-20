"""Step 1 -- S&P 500 constituent universe, with GICS sector for the
concentration check in section 1.8.

Membership changes several times a year, so the cache carries the date it was
built and `staleness_days()` lets callers warn instead of silently trading an
out-of-date list.
"""

from __future__ import annotations

import datetime as dt
import io
import re
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


# Two share classes of one company are one issuer, one earnings date and one
# gap. GOOG and GOOGL were both open 2026-09-09 to 2026-09-14 -- $703.74 of a
# $2,400 budget on Alphabet -- and neither concentration cap saw it: the ticker
# cap counts symbols and the sector cap counts GICS labels, and 23 labels do
# not separate a company from itself. See LEARNING.md 44.
#
# The constituent table's own `name` carries the class in parentheses
# ("Alphabet Inc. (Class A)"), so stripping that groups every dual-class pair
# without a list anyone has to maintain -- across the 503 rows of the cached
# S&P 500 it merges Alphabet, Fox and News Corp and nothing else. The table
# below is a backstop for rows whose name did not survive: `screener` writes
# "Unknown" when the universe lookup misses, and two Unknowns must not merge.
_DUAL_CLASS = {"GOOG": "ALPHABET", "GOOGL": "ALPHABET",
               "FOX": "FOX-CORP", "FOXA": "FOX-CORP",
               "NWS": "NEWS-CORP", "NWSA": "NEWS-CORP"}
_CLASS_SUFFIX = re.compile(r"\s*\((?:class|series)\s+[a-z0-9]{1,2}\)\s*$", re.I)
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")


def issuer_key(name: str, symbol: str) -> str:
    """The company behind a ticker, so two share classes count as one name.

    An opaque grouping key, never a label -- do not render it. Callers that
    need to tell the user which position is in the way should name the symbol.

    Falls back to the symbol when the name is missing or unrecognisable, which
    makes the key equal to the ticker for every single-class name in the index
    and keeps an unknown name concentrated only with itself.
    """
    sym = (symbol or "").strip().upper()
    if sym in _DUAL_CLASS:
        return _DUAL_CLASS[sym]
    base = _NOT_ALNUM.sub("", _CLASS_SUFFIX.sub("", (name or "").strip()).lower())
    # "Unknown" is `screener`'s sentinel for a universe lookup that missed, not
    # a company. Two of them are two different companies and must not merge.
    return sym if base in ("", "unknown") else base


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

    def issuer_of(self, symbol: str) -> str:
        row = self.frame.loc[self.frame.symbol == symbol, "name"]
        return issuer_key(row.iloc[0] if len(row) else "", symbol)

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
