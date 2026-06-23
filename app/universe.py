"""Builds the trading universe: ~top 1000 US stocks + popular ETFs.

Constituents are pulled live from Wikipedia (S&P 500 + S&P 400 + S&P 600, which
are tiered by market cap: large -> mid -> small). Taking the first ~1000 of that
ordered, deduped list approximates the top 1000 US stocks by market cap. Results
are cached on disk for a week; if the network fetch fails we fall back to a
baked-in large-cap snapshot so the app always works offline.
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.request
from io import StringIO

import certifi
import pandas as pd

from .config import DATA_DIR

_CACHE = DATA_DIR / "universe.json"
_CACHE_TTL = 60 * 60 * 24 * 7  # 1 week
TARGET = 1000                  # number of single-name stocks to include

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
_UA = {"User-Agent": "Mozilla/5.0 (terravue)"}

# Sources, in market-cap tier order (largest first).
_SP_SOURCES = [
    ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ("symbol",)),
    ("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", ("symbol",)),
    ("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", ("symbol",)),
]

POPULAR_ETFS = [
    "SPY", "QQQ", "DIA", "IWM", "VTI", "VOO", "VEA", "VWO", "EFA", "EEM",
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE",
    "XLC", "SMH", "SOXX", "ARKK", "GLD", "SLV", "USO", "TLT", "HYG", "LQD",
    "VNQ", "VIG", "SCHD", "JEPI", "VYM", "IBIT", "GBTC", "BITO",
]

_FALLBACK = [
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA", "AVGO",
    "JPM", "V", "MA", "WMT", "XOM", "JNJ", "PG", "HD", "COST", "ORCL", "LLY",
    "MRK", "ABBV", "PEP", "KO", "BAC", "CRM", "ADBE", "AMD", "NFLX", "DIS",
    "CSCO", "INTC", "QCOM", "TXN", "AMAT", "PFE", "TMO", "ABT", "NKE", "MCD",
    "WFC", "GS", "MS", "CAT", "BA", "GE", "HON", "UNH", "CVX", "LIN", "PM",
]


def _read_html(url: str) -> list[pd.DataFrame]:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    return pd.read_html(StringIO(html))


def _symbols_from(url: str, col_names: tuple[str, ...]) -> list[str]:
    """Pull the ticker column from the first matching table on a Wikipedia page."""
    for tbl in _read_html(url):
        cols = {str(c).lower(): c for c in tbl.columns}
        for cand in col_names + ("ticker",):
            if cand in cols:
                return [str(t).strip() for t in tbl[cols[cand]].tolist()]
    return []


def _ordered_constituents() -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for url, cols in _SP_SOURCES:
        for raw in _symbols_from(url, cols):
            t = raw.replace(".", "-")           # Yahoo uses BRK-B not BRK.B
            if t and t not in ("nan", "None") and t not in seen:
                seen.add(t)
                ordered.append(t)
    return ordered


def get_universe(refresh: bool = False) -> list[str]:
    """Return the deduped list of tickers to analyze (~TARGET names + ETFs)."""
    if not refresh and _CACHE.exists():
        if time.time() - _CACHE.stat().st_mtime < _CACHE_TTL:
            try:
                data = json.loads(_CACHE.read_text())
                if data.get("tickers"):
                    return data["tickers"]
            except Exception:
                pass

    try:
        names = _ordered_constituents()
        source = "wikipedia"
        if len(names) < 500:                    # sanity check
            raise ValueError(f"only {len(names)} constituents fetched")
    except Exception as exc:
        print(f"[universe] live fetch failed ({exc}); using fallback list")
        names = _FALLBACK
        source = "fallback"

    top = names[:TARGET]
    combined = sorted(set(top) | set(POPULAR_ETFS))
    try:
        _CACHE.write_text(json.dumps(
            {"source": source, "count": len(combined), "tickers": combined},
            indent=2))
    except Exception:
        pass
    return combined


if __name__ == "__main__":
    u = get_universe(refresh=True)
    print(f"Universe: {len(u)} tickers")
    print(", ".join(u[:30]), "...")
