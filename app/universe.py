"""Builds the trading universe: S&P 500 + Nasdaq-100 + popular ETFs.

Constituents are pulled live from Wikipedia and cached on disk for a week so
daily cron runs don't re-scrape. If the network fetch fails, we fall back to a
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

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
_UA = {"User-Agent": "Mozilla/5.0 (stock-advisor)"}


def _read_html(url: str) -> list[pd.DataFrame]:
    """read_html that uses certifi for TLS (system trust store may be empty)."""
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    return pd.read_html(StringIO(html))

_CACHE = DATA_DIR / "universe.json"
_CACHE_TTL = 60 * 60 * 24 * 7  # 1 week

# High-volume, broadly-traded ETFs worth watching alongside single names.
POPULAR_ETFS = [
    "SPY", "QQQ", "DIA", "IWM", "VTI", "VOO", "VEA", "VWO", "EFA", "EEM",
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE",
    "XLC", "SMH", "SOXX", "ARKK", "GLD", "SLV", "USO", "TLT", "HYG", "LQD",
    "VNQ", "VIG", "SCHD", "JEPI", "VYM", "IBIT", "GBTC", "BITO",
]

# Minimal offline fallback (mega/large caps) if Wikipedia is unreachable.
_FALLBACK = [
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA", "AVGO",
    "JPM", "V", "MA", "WMT", "XOM", "JNJ", "PG", "HD", "COST", "ORCL", "LLY",
    "MRK", "ABBV", "PEP", "KO", "BAC", "CRM", "ADBE", "AMD", "NFLX", "DIS",
    "CSCO", "INTC", "QCOM", "TXN", "AMAT", "PFE", "TMO", "ABT", "NKE", "MCD",
    "WFC", "GS", "MS", "CAT", "BA", "GE", "HON", "UNH", "CVX", "LIN", "PM",
]


def _from_wikipedia() -> list[str]:
    tickers: set[str] = set()

    sp = _read_html(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
    tickers.update(str(t).strip() for t in sp["Symbol"].tolist())

    ndx_tables = _read_html("https://en.wikipedia.org/wiki/Nasdaq-100")
    for tbl in ndx_tables:
        cols = [str(c).lower() for c in tbl.columns]
        for cand in ("ticker", "symbol"):
            if cand in cols:
                col = tbl.columns[cols.index(cand)]
                tickers.update(str(t).strip() for t in tbl[col].tolist())
                break

    # Yahoo uses '-' where indices use '.' (e.g. BRK.B -> BRK-B).
    cleaned = {t.replace(".", "-") for t in tickers
               if t and t not in ("nan", "None")}
    return sorted(cleaned)


def get_universe(refresh: bool = False) -> list[str]:
    """Return the deduped list of tickers to analyze."""
    if not refresh and _CACHE.exists():
        age = time.time() - _CACHE.stat().st_mtime
        if age < _CACHE_TTL:
            try:
                data = json.loads(_CACHE.read_text())
                if data.get("tickers"):
                    return data["tickers"]
            except Exception:
                pass

    try:
        names = _from_wikipedia()
        source = "wikipedia"
    except Exception as exc:
        print(f"[universe] live fetch failed ({exc}); using fallback list")
        names = _FALLBACK
        source = "fallback"

    combined = sorted(set(names) | set(POPULAR_ETFS))
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
