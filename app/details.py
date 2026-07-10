"""Precompute per-stock detail blobs during the daily run and store them in KV.

Runs in the cloud job (GitHub Actions), where Yahoo is reachable. The read-only
Vercel site serves these from KV (Vercel's IP is blocked by Yahoo). Each blob
carries OHLCV history (daily ~1y + weekly ~15y) so the frontend can draw
candlesticks, volume, moving averages and MACD across 1M -> All ranges, plus a
broad fundamentals set. Fundamentals are fetched for the most-clicked names.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor

from . import data, store
from .universe import _FALLBACK as POPULAR, get_universe

_ALWAYS_ENRICH = set(POPULAR) | {
    "TSLA", "NFLX", "AMD", "CRM", "PYPL", "SBUX", "UBER", "ABNB", "COIN",
    "PLTR", "SHOP", "SQ", "SNOW", "RIVN", "F", "GM", "T", "VZ", "PEP",
}

_DAILY_CAP = 400     # ~1.5y of daily bars
_WEEKLY_CAP = 800    # ~15y of weekly bars
_INTRADAY_CAP = 420  # enough 5-minute/15-minute bars for 1H, 1D and 5D charts


def _clean(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else round(f, 4)
    except (TypeError, ValueError):
        return None


def _series(df, cap):
    if df is None or df.empty:
        return None
    df = df.tail(cap)
    try:
        t = [int(ts.timestamp()) for ts in df.index]
        vol = [int(x) if (x == x and x is not None) else 0 for x in df["Volume"].tolist()]
        return {"t": t,
                "o": [_clean(x) for x in df["Open"].tolist()],
                "h": [_clean(x) for x in df["High"].tolist()],
                "l": [_clean(x) for x in df["Low"].tolist()],
                "c": [_clean(x) for x in df["Close"].tolist()],
                "v": vol}
    except Exception:
        return None


def _fetch_weekly(tickers, batch=200):
    import yfinance as yf
    out = {}
    for i in range(0, len(tickers), batch):
        chunk = tickers[i:i + batch]
        try:
            raw = yf.download(chunk, period="max", interval="1wk", auto_adjust=True,
                              progress=False, group_by="ticker", threads=True)
        except Exception as exc:
            print(f"[details] weekly batch failed: {exc}")
            continue
        multi = hasattr(raw.columns, "levels")
        for t in chunk:
            try:
                df = raw[t] if multi else raw
                df = df.dropna(how="all")
                if not df.empty:
                    out[t] = df
            except Exception:
                continue
    return out


def _fetch_intraday(tickers, period, interval, batch=120):
    import yfinance as yf
    out = {}
    for i in range(0, len(tickers), batch):
        chunk = tickers[i:i + batch]
        try:
            raw = yf.download(chunk, period=period, interval=interval, auto_adjust=True,
                              progress=False, group_by="ticker", threads=True)
        except Exception as exc:
            print(f"[details] intraday {period}/{interval} batch failed: {exc}")
            continue
        multi = hasattr(raw.columns, "levels")
        for t in chunk:
            try:
                df = raw[t] if multi else raw
                df = df.dropna(how="all")
                if not df.empty:
                    out[t] = df
            except Exception:
                continue
    return out


def _ret(closes, k):
    if len(closes) <= k or closes[-k - 1] == 0:
        return None
    return (closes[-1] / closes[-k - 1] - 1) * 100


def _detail(ticker, sugg, daily_df, weekly_df, day_df=None, five_day_df=None):
    daily = _series(daily_df, _DAILY_CAP)
    weekly = _series(weekly_df, _WEEKLY_CAP)
    intraday_1d = _series(day_df, _INTRADAY_CAP)
    intraday_5d = _series(five_day_df, _INTRADAY_CAP)
    closes = [c for c in (daily["c"] if daily else []) if c is not None]
    wcloses = [c for c in (weekly["c"] if weekly else []) if c is not None]
    ind = (sugg or {}).get("indicators", {})
    hi = max(closes) if closes else None
    lo = min(closes) if closes else None
    return {
        "ticker": ticker, "name": ticker, "exchange": None, "currency": "USD",
        "price": (sugg or {}).get("price") or (closes[-1] if closes else None),
        "chg_1d": ind.get("chg_1d"),
        "fiftyTwoWeekHigh": hi, "fiftyTwoWeekLow": lo,
        "series": {
            "intraday_1d": intraday_1d,
            "intraday_5d": intraday_5d,
            "daily": daily,
            "weekly": weekly,
        },
        "technicals": {
            "rsi14": ind.get("rsi14"),
            "ret_1m": ind.get("ret_1m"),
            "ret_3m": _ret(closes, 63),
            "ret_1y": ((closes[-1] / closes[0] - 1) * 100) if len(closes) > 1 else None,
            "ret_5y": ((wcloses[-1] / wcloses[-260] - 1) * 100) if len(wcloses) > 260 else None,
            "volatility": (ind.get("volatility") * 100) if ind.get("volatility") is not None else None,
        },
        "signal": {"composite": (sugg or {}).get("composite"),
                   "action": (sugg or {}).get("action"),
                   "signals": (sugg or {}).get("signals", [])},
        "profile": {"sector": None, "industry": None, "country": None,
                    "website": None, "employees": None, "summary": None},
        "financials": {},
    }


def _fundamentals(ticker):
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).get_info()
    except Exception:
        return ticker, None, None, None
    if not info:
        return ticker, None, None, None
    g = info.get
    prof = {"sector": g("sector"), "industry": g("industry"), "country": g("country"),
            "website": g("website"), "employees": g("fullTimeEmployees"),
            "summary": g("longBusinessSummary")}
    fin = {
        # valuation
        "marketCap": g("marketCap"), "enterpriseValue": g("enterpriseValue"),
        "trailingPE": g("trailingPE"), "forwardPE": g("forwardPE"),
        "pegRatio": g("trailingPegRatio") or g("pegRatio"),
        "priceToBook": g("priceToBook"),
        "priceToSales": g("priceToSalesTrailing12Months"),
        "evToEbitda": g("enterpriseToEbitda"),
        # profitability
        "profitMargin": g("profitMargins"), "operatingMargin": g("operatingMargins"),
        "grossMargin": g("grossMargins"), "roe": g("returnOnEquity"),
        "roa": g("returnOnAssets"), "ebitda": g("ebitda"),
        "freeCashflow": g("freeCashflow"),
        # per share / growth
        "eps": g("trailingEps"), "forwardEps": g("forwardEps"),
        "bookValue": g("bookValue"), "revenue": g("totalRevenue"),
        "revenueGrowth": g("revenueGrowth"), "earningsGrowth": g("earningsGrowth"),
        # health
        "totalCash": g("totalCash"), "totalDebt": g("totalDebt"),
        "debtToEquity": g("debtToEquity"), "currentRatio": g("currentRatio"),
        # dividend
        "dividendYield": g("dividendYield"), "payoutRatio": g("payoutRatio"),
        # trading / ownership
        "beta": g("beta"), "sma50": g("fiftyDayAverage"), "sma200": g("twoHundredDayAverage"),
        "avgVolume": g("averageVolume"), "sharesOutstanding": g("sharesOutstanding"),
        "heldInsiders": g("heldPercentInsiders"), "heldInstitutions": g("heldPercentInstitutions"),
        "shortRatio": g("shortRatio"),
        # analyst
        "targetMeanPrice": g("targetMeanPrice"),
        "recommendation": (g("recommendationKey") or "").replace("_", " ") or None,
        "numAnalysts": g("numberOfAnalystOpinions"),
    }
    name = g("longName") or g("shortName")
    exch = g("fullExchangeName") or g("exchange")
    return ticker, prof, fin, (name, exch)


def build_and_store(suggestions: dict, agents_view: dict,
                    fundamentals_for: int = 280) -> int:
    sugg_by = {s["ticker"]: s for s in suggestions.get("suggestions", [])}
    tickers = get_universe()
    daily = data.get_histories(tickers, period="1y")     # OHLCV daily (cached)
    print("[details] fetching weekly history...")
    weekly = _fetch_weekly(tickers)
    print("[details] fetching intraday history...")
    intraday_1d = _fetch_intraday(tickers, "1d", "5m")
    intraday_5d = _fetch_intraday(tickers, "5d", "15m")

    details = {
        t: _detail(
            t, sugg_by.get(t), daily.get(t), weekly.get(t),
            intraday_1d.get(t), intraday_5d.get(t),
        )
        for t in tickers
    }

    held = {h["ticker"] for a in agents_view.get("agents", [])
            for h in a.get("snapshot", {}).get("holdings", [])}
    ranked = sorted(sugg_by.values(), key=lambda s: s.get("composite", 0))
    top = {s["ticker"] for s in ranked[-80:]} | {s["ticker"] for s in ranked[:20]}
    want = (held | top | _ALWAYS_ENRICH) & set(details)
    priority = list((held | _ALWAYS_ENRICH) & want) + list(want - held - _ALWAYS_ENRICH)
    enrich = priority[:fundamentals_for]

    print(f"[details] fetching fundamentals for {len(enrich)} names...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        for t, prof, fin, meta in ex.map(_fundamentals, enrich):
            if prof:
                details[t]["profile"] = prof
                details[t]["financials"] = fin
                if meta and meta[0]:
                    details[t]["name"] = meta[0]
                if meta and meta[1]:
                    details[t]["exchange"] = meta[1]

    n = store.write_many({f"sd:{t}": d for t, d in details.items()}, chunk=60)
    print(f"[details] stored {n} detail blobs ({len(enrich)} enriched, "
          f"{sum(1 for d in details.values() if d['series']['weekly'])} with weekly history)")
    return n
