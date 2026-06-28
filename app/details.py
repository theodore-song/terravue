"""Precompute per-stock detail blobs during the daily run and store them in KV.

Runs in the cloud job (GitHub Actions), where Yahoo is reachable. The read-only
Vercel site then serves these from KV (Vercel's IP is blocked by Yahoo, so it
can't fetch on demand). Charts + technicals come from the price frames we already
download; fundamentals are fetched best-effort for the names agents actually hold
plus the strongest/weakest signals (so the most-clicked stocks have full data).
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor

from . import data, store
from .universe import _FALLBACK as POPULAR, get_universe

# Widely-followed names that should always have full fundamentals (most clicked).
_ALWAYS_ENRICH = set(POPULAR) | {
    "TSLA", "NFLX", "AMD", "CRM", "PYPL", "SBUX", "UBER", "ABNB", "COIN",
    "PLTR", "SHOP", "SQ", "SNOW", "RIVN", "F", "GM", "T", "VZ", "PEP",
}


def _ret(closes, k):
    if len(closes) <= k or closes[-k - 1] == 0:
        return None
    return (closes[-1] / closes[-k - 1] - 1) * 100


def _detail_from_frame(ticker, sugg, df):
    closes = [float(c) for c in df["Close"].dropna().tolist()] if df is not None and not df.empty else []
    dates = [str(d)[:10] for d in df["Close"].dropna().index] if closes else []
    ind = (sugg or {}).get("indicators", {})
    hi = max(closes) if closes else None
    lo = min(closes) if closes else None
    return {
        "ticker": ticker,
        "name": ticker,
        "exchange": None,
        "currency": "USD",
        "price": (sugg or {}).get("price") or (closes[-1] if closes else None),
        "chg_1d": ind.get("chg_1d"),
        "fiftyTwoWeekHigh": hi,
        "fiftyTwoWeekLow": lo,
        "chart": {"dates": dates, "closes": closes},
        "technicals": {
            "rsi14": ind.get("rsi14"),
            "ret_1m": ind.get("ret_1m"),
            "ret_3m": _ret(closes, 63),
            "ret_1y": ((closes[-1] / closes[0] - 1) * 100) if len(closes) > 1 else None,
            "volatility": (ind.get("volatility") * 100) if ind.get("volatility") is not None else None,
        },
        "signal": {
            "composite": (sugg or {}).get("composite"),
            "action": (sugg or {}).get("action"),
            "signals": (sugg or {}).get("signals", []),
        },
        "profile": {"sector": None, "industry": None, "country": None,
                    "website": None, "employees": None, "summary": None},
        "financials": {},
    }


def _fundamentals(ticker):
    """Best-effort fundamentals via yfinance for a single ticker."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).get_info()
    except Exception:
        return ticker, None, None
    if not info:
        return ticker, None, None
    prof = {
        "sector": info.get("sector"), "industry": info.get("industry"),
        "country": info.get("country"), "website": info.get("website"),
        "employees": info.get("fullTimeEmployees"),
        "summary": info.get("longBusinessSummary"),
    }
    fin = {
        "marketCap": info.get("marketCap"),
        "trailingPE": info.get("trailingPE"), "forwardPE": info.get("forwardPE"),
        "eps": info.get("trailingEps"), "beta": info.get("beta"),
        "dividendYield": info.get("dividendYield"),
        "revenue": info.get("totalRevenue"),
        "profitMargin": info.get("profitMargins"),
        "revenueGrowth": info.get("revenueGrowth"),
        "targetMeanPrice": info.get("targetMeanPrice"),
        "recommendation": (info.get("recommendationKey") or "").replace("_", " ") or None,
        "numAnalysts": info.get("numberOfAnalystOpinions"),
    }
    name = info.get("longName") or info.get("shortName")
    return ticker, prof, fin, name


def build_and_store(suggestions: dict, agents_view: dict,
                    fundamentals_for: int = 280) -> int:
    """Build detail blobs for the whole universe and write them to KV."""
    sugg_by = {s["ticker"]: s for s in suggestions.get("suggestions", [])}
    tickers = get_universe()
    frames = data.get_histories(tickers, period="1y")  # reads fresh disk cache

    details = {t: _detail_from_frame(t, sugg_by.get(t), frames.get(t)) for t in tickers}

    # which names to enrich with fundamentals: agent holdings + strongest/weakest
    held = {h["ticker"] for a in agents_view.get("agents", [])
            for h in a.get("snapshot", {}).get("holdings", [])}
    ranked = sorted(sugg_by.values(), key=lambda s: s.get("composite", 0))
    top = {s["ticker"] for s in ranked[-80:]} | {s["ticker"] for s in ranked[:20]}
    want = (held | top | _ALWAYS_ENRICH) & set(details)
    # prioritize held + popular, then fill with top/bottom signals
    priority = list((held | _ALWAYS_ENRICH) & want) + list(want - held - _ALWAYS_ENRICH)
    enrich = priority[:fundamentals_for]

    print(f"[details] fetching fundamentals for {len(enrich)} names...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(_fundamentals, enrich):
            t, prof, fin = res[0], res[1], res[2]
            if prof:
                details[t]["profile"] = prof
                details[t]["financials"] = fin
                if len(res) > 3 and res[3]:
                    details[t]["name"] = res[3]

    n = store.write_many({f"sd:{t}": d for t, d in details.items()})
    print(f"[details] stored {n} stock detail blobs ({len(enrich)} with fundamentals)")
    return n
