"""Market data access via Yahoo Finance (yfinance), with light on-disk caching."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

from .config import DATA_DIR

_CACHE_DIR = DATA_DIR / "price_cache"
_CACHE_DIR.mkdir(exist_ok=True)
_CACHE_TTL_SECONDS = 60 * 60 * 6  # 6 hours


def _cache_path(ticker: str) -> Path:
    return _CACHE_DIR / f"{ticker.upper()}.csv"


def get_history(ticker: str, period: str = "1y", interval: str = "1d",
                use_cache: bool = True) -> pd.DataFrame:
    """Return a DataFrame of OHLCV history for a ticker.

    Cached to disk for a few hours so repeated runs (and the web UI) don't
    hammer Yahoo. Returns an empty frame on failure rather than raising.
    """
    path = _cache_path(ticker)
    if use_cache and path.exists():
        age = time.time() - path.stat().st_mtime
        if age < _CACHE_TTL_SECONDS:
            try:
                df = pd.read_csv(path, index_col=0, parse_dates=True)
                if not df.empty:
                    return df
            except Exception:
                pass

    try:
        df = yf.download(ticker, period=period, interval=interval,
                         auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if not df.empty:
            df.to_csv(path)
        return df
    except Exception as exc:  # network / symbol errors
        print(f"[data] failed to fetch {ticker}: {exc}")
        if path.exists():
            try:
                return pd.read_csv(path, index_col=0, parse_dates=True)
            except Exception:
                pass
        return pd.DataFrame()


def get_histories(tickers: list[str], period: str = "1y",
                  use_cache: bool = True, batch_size: int = 100) -> dict[str, pd.DataFrame]:
    """Fetch history for many tickers efficiently.

    Loads fresh disk-cached frames first, then batch-downloads only the rest via
    one yfinance call per chunk (threaded). Returns {ticker: DataFrame}; tickers
    that fail are simply absent.
    """
    out: dict[str, pd.DataFrame] = {}
    stale: list[str] = []

    for t in tickers:
        path = _cache_path(t)
        if use_cache and path.exists() and (time.time() - path.stat().st_mtime) < _CACHE_TTL_SECONDS:
            try:
                df = pd.read_csv(path, index_col=0, parse_dates=True)
                if not df.empty:
                    out[t] = df
                    continue
            except Exception:
                pass
        stale.append(t)

    for i in range(0, len(stale), batch_size):
        chunk = stale[i:i + batch_size]
        try:
            raw = yf.download(chunk, period=period, interval="1d",
                              auto_adjust=True, progress=False,
                              group_by="ticker", threads=True)
        except Exception as exc:
            print(f"[data] batch download failed for {len(chunk)} tickers: {exc}")
            continue
        for t in chunk:
            try:
                df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = df.dropna(how="all")
                if not df.empty:
                    df.to_csv(_cache_path(t))
                    out[t] = df
            except Exception:
                continue
    return out


def latest_price(ticker: str) -> Optional[float]:
    """Most recent closing price, or None if unavailable."""
    df = get_history(ticker, period="5d")
    if df.empty or "Close" not in df:
        return None
    try:
        return float(df["Close"].dropna().iloc[-1])
    except (IndexError, ValueError):
        return None


def prices_for(tickers: list[str]) -> dict[str, float]:
    """Latest price for each ticker (skips any that fail)."""
    out: dict[str, float] = {}
    for t in tickers:
        p = latest_price(t)
        if p is not None:
            out[t] = p
    return out
