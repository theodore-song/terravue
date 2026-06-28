"""On-demand per-stock detail from Yahoo's public JSON APIs.

Pure stdlib + certifi (NO pandas/yfinance) so it runs inside the slim Vercel
function. Fetches a price chart and company fundamentals for a single ticker,
computes a few technicals in plain Python, and (optionally) reports which agents
hold the name. Fundamentals degrade gracefully if Yahoo blocks the request.
"""
from __future__ import annotations

import http.cookiejar
import json
import math
import ssl
import urllib.parse
import urllib.request

try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _CTX = ssl.create_default_context()

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
_CJ = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=_CTX),
    urllib.request.HTTPCookieProcessor(_CJ))
_crumb: str | None = None


def _get(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers=_UA)
    return _OPENER.open(req, timeout=timeout)


def _crumb_token() -> str | None:
    global _crumb
    if _crumb:
        return _crumb
    # Seed the A3 cookie from a normal Yahoo page, then request a crumb.
    for seed in ("https://finance.yahoo.com/", "https://fc.yahoo.com"):
        try:
            _get(seed)
        except Exception:
            pass
    for host in ("query2", "query1"):
        try:
            c = _get(f"https://{host}.finance.yahoo.com/v1/test/getcrumb").read().decode().strip()
            if c and "<" not in c:
                _crumb = c
                return _crumb
        except Exception:
            continue
    return None


# --- technicals (plain python) ----------------------------------------------

def _sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def _rsi(xs, n=14):
    if len(xs) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        d = xs[i] - xs[i - 1]
        gains += max(d, 0); losses += max(-d, 0)
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100 - 100 / (1 + rs)


def _ret(xs, k):
    if len(xs) <= k or xs[-k - 1] == 0:
        return None
    return (xs[-1] / xs[-k - 1] - 1) * 100


def _volatility(xs, window=30):
    if len(xs) < 3:
        return None
    rets = [xs[i] / xs[i - 1] - 1 for i in range(1, len(xs)) if xs[i - 1]]
    tail = rets[-window:]
    if len(tail) < 2:
        return None
    mean = sum(tail) / len(tail)
    var = sum((r - mean) ** 2 for r in tail) / (len(tail) - 1)
    return math.sqrt(var) * math.sqrt(252) * 100


# --- fetchers ---------------------------------------------------------------

def get_chart(ticker: str) -> dict | None:
    import datetime as _dt
    for host in ("query1", "query2"):
        url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/"
               f"{urllib.parse.quote(ticker)}?range=1y&interval=1d")
        try:
            d = json.load(_get(url))
            r = d["chart"]["result"][0]
            meta = r["meta"]
            ts = r.get("timestamp", []) or []
            closes_raw = r["indicators"]["quote"][0].get("close", []) or []
            pts = [(t, c) for t, c in zip(ts, closes_raw) if c is not None]
            dates = [_dt.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d") for t, _ in pts]
            return {"meta": meta, "dates": dates, "closes": [float(c) for _, c in pts]}
        except Exception as exc:
            print(f"[stockinfo] chart {ticker} {host}: {exc}")
    return None


def get_fundamentals(ticker: str) -> dict:
    global _crumb
    modules = "assetProfile,summaryDetail,defaultKeyStatistics,financialData,price"
    for attempt in range(2):
        crumb = _crumb_token()
        if not crumb:
            return {}
        for host in ("query2", "query1"):
            url = (f"https://{host}.finance.yahoo.com/v10/finance/quoteSummary/"
                   f"{urllib.parse.quote(ticker)}?modules={modules}"
                   f"&crumb={urllib.parse.quote(crumb)}")
            try:
                d = json.load(_get(url))
                res = d.get("quoteSummary", {}).get("result")
                if res:
                    return res[0]
            except Exception as exc:
                print(f"[stockinfo] fundamentals {ticker} {host}: {exc}")
        _crumb = None  # crumb likely stale; refetch once
    return {}


def _raw(node, key):
    v = (node or {}).get(key)
    if isinstance(v, dict):
        return v.get("raw")
    return v


import time as _time
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 60 * 30  # 30 min


def get_stock_detail(ticker: str, agents_view: dict | None = None) -> dict:
    ticker = ticker.upper().strip()
    cached = _CACHE.get(ticker)
    if cached and (_time.time() - cached[0]) < _CACHE_TTL:
        d = dict(cached[1])
        d["holders"] = _holders_for(ticker, agents_view)   # keep holders fresh
        return d

    chart = get_chart(ticker)
    if not chart:
        return {"ticker": ticker,
                "error": "Live data is temporarily unavailable (rate limited). Try again shortly."}

    closes = chart["closes"]
    meta = chart["meta"]
    price = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
    chg_1d = _ret(closes, 1)   # last close vs prior close

    fun = get_fundamentals(ticker)
    ap, sd = fun.get("assetProfile", {}), fun.get("summaryDetail", {})
    ks, fd = fun.get("defaultKeyStatistics", {}), fun.get("financialData", {})
    pr = fun.get("price", {})

    detail = {
        "ticker": ticker,
        "name": meta.get("longName") or meta.get("shortName") or (pr.get("longName")) or ticker,
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "currency": meta.get("currency", "USD"),
        "price": price,
        "chg_1d": chg_1d,
        "fiftyTwoWeekHigh": meta.get("fiftyTwoWeekHigh"),
        "fiftyTwoWeekLow": meta.get("fiftyTwoWeekLow"),
        "chart": {"dates": chart["dates"], "closes": closes},
        "technicals": {
            "rsi14": _rsi(closes),
            "sma50": _sma(closes, 50),
            "sma200": _sma(closes, 200),
            "ret_1m": _ret(closes, 21),
            "ret_3m": _ret(closes, 63),
            "ret_1y": ((closes[-1] / closes[0] - 1) * 100) if len(closes) > 1 else None,
            "volatility": _volatility(closes),
        },
        "profile": {
            "sector": ap.get("sector"),
            "industry": ap.get("industry"),
            "country": ap.get("country"),
            "website": ap.get("website"),
            "employees": ap.get("fullTimeEmployees"),
            "summary": ap.get("longBusinessSummary"),
        },
        "financials": {
            "marketCap": _raw(pr, "marketCap") or _raw(sd, "marketCap"),
            "trailingPE": _raw(sd, "trailingPE"),
            "forwardPE": _raw(sd, "forwardPE"),
            "eps": _raw(ks, "trailingEps"),
            "beta": _raw(sd, "beta") or _raw(ks, "beta"),
            "dividendYield": _raw(sd, "dividendYield"),
            "revenue": _raw(fd, "totalRevenue"),
            "profitMargin": _raw(fd, "profitMargins"),
            "revenueGrowth": _raw(fd, "revenueGrowth"),
            "targetMeanPrice": _raw(fd, "targetMeanPrice"),
            "recommendation": (fd.get("recommendationKey") or "").replace("_", " ") or None,
            "numAnalysts": _raw(fd, "numberOfAnalystOpinions"),
        },
    }
    _CACHE[ticker] = (_time.time(), detail)
    out = dict(detail)
    out["holders"] = _holders_for(ticker, agents_view)
    return out


def _holders_for(ticker: str, agents_view: dict | None) -> list[dict]:
    holders = []
    for a in (agents_view or {}).get("agents", []):
        for h in a.get("snapshot", {}).get("holdings", []):
            if h["ticker"] == ticker:
                holders.append({"id": a["id"], "name": a["name"], "color": a["color"],
                                "shares": h["shares"], "weight": h["weight"],
                                "unrealized_pct": h["unrealized_pct"]})
    return holders
