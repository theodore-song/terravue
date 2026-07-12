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

def _ohlcv(ticker: str, rng: str, interval: str) -> dict | None:
    """Return an OHLCV series {t,o,h,l,c,v} (+ meta) from Yahoo's v8 chart."""
    _crumb_token()  # seeds Yahoo cookies; chart endpoint often needs this on serverless IPs
    for host in ("query1", "query2"):
        url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/"
               f"{urllib.parse.quote(ticker)}?range={rng}&interval={interval}")
        try:
            d = json.load(_get(url))
            r = d["chart"]["result"][0]
            ts = r.get("timestamp", []) or []
            q = r["indicators"]["quote"][0]
            o, h, l, c, v = (q.get(k, []) or [] for k in ("open", "high", "low", "close", "volume"))
            t2, o2, h2, l2, c2, v2 = [], [], [], [], [], []
            for i, tt in enumerate(ts):
                if i < len(c) and c[i] is not None:
                    t2.append(int(tt))
                    o2.append(round(float(o[i]), 4) if i < len(o) and o[i] is not None else None)
                    h2.append(round(float(h[i]), 4) if i < len(h) and h[i] is not None else None)
                    l2.append(round(float(l[i]), 4) if i < len(l) and l[i] is not None else None)
                    c2.append(round(float(c[i]), 4))
                    v2.append(int(v[i]) if i < len(v) and v[i] is not None else 0)
            return {"meta": r["meta"], "t": t2, "o": o2, "h": h2, "l": l2, "c": c2, "v": v2}
        except Exception as exc:
            print(f"[stockinfo] chart {ticker} {host} {rng}/{interval}: {exc}")
    return None


def _spark_ohlcv(ticker: str, rng: str, interval: str) -> dict | None:
    """Fallback Yahoo spark endpoint, useful for compact intraday series."""
    url = (f"https://query1.finance.yahoo.com/v7/finance/spark?symbols="
           f"{urllib.parse.quote(ticker)}&range={urllib.parse.quote(rng)}"
           f"&interval={urllib.parse.quote(interval)}")
    try:
        d = json.load(_get(url))
        results = d.get("spark", {}).get("result") or []
        response = (results[0].get("response") or [None])[0] if results else None
        if not response:
            return None
        ts = response.get("timestamp", []) or []
        quote = ((response.get("indicators") or {}).get("quote") or [{}])[0]
        c = quote.get("close", []) or []
        o = quote.get("open", []) or c
        h = quote.get("high", []) or c
        l = quote.get("low", []) or c
        v = quote.get("volume", []) or []
        t2, o2, h2, l2, c2, v2 = [], [], [], [], [], []
        for i, tt in enumerate(ts):
            if i < len(c) and c[i] is not None:
                close = float(c[i])
                t2.append(int(tt))
                o2.append(round(float(o[i]), 4) if i < len(o) and o[i] is not None else round(close, 4))
                h2.append(round(float(h[i]), 4) if i < len(h) and h[i] is not None else round(close, 4))
                l2.append(round(float(l[i]), 4) if i < len(l) and l[i] is not None else round(close, 4))
                c2.append(round(close, 4))
                v2.append(int(v[i]) if i < len(v) and v[i] is not None else 0)
        if not t2:
            return None
        return {"meta": response.get("meta", {}), "t": t2, "o": o2, "h": h2, "l": l2, "c": c2, "v": v2}
    except Exception as exc:
        print(f"[stockinfo] spark {ticker} {rng}/{interval}: {exc}")
        return None


def _nasdaq_intraday(ticker: str, timeframe: str = "1d") -> dict | None:
    """Fallback minute chart from Nasdaq's public quote API."""
    symbol = ticker.upper().replace(".", "-")
    url = (f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(symbol)}/chart"
           f"?assetclass=stocks&timeframe={urllib.parse.quote(timeframe)}")
    req = urllib.request.Request(url, headers={
        **_UA,
        "Accept": "application/json",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/",
    })
    try:
        with _OPENER.open(req, timeout=12) as resp:
            d = json.loads(resp.read().decode())
        chart = ((d.get("data") or {}).get("chart") or [])
        t2, o2, h2, l2, c2, v2 = [], [], [], [], [], []
        prev_close = None
        for p in chart:
            x = p.get("x")
            y = p.get("y")
            if x is None or y is None:
                continue
            price = float(y)
            opn = prev_close if prev_close is not None else price
            high = max(opn, price)
            low = min(opn, price)
            t2.append(int(float(x) / 1000))
            o2.append(round(opn, 4))
            h2.append(round(high, 4))
            l2.append(round(low, 4))
            c2.append(round(price, 4))
            v2.append(0)
            prev_close = price
        if not t2:
            return None
        return {"meta": {"symbol": ticker, "source": "nasdaq"}, "t": t2, "o": o2, "h": h2, "l": l2, "c": c2, "v": v2}
    except Exception as exc:
        print(f"[stockinfo] nasdaq {ticker} {timeframe}: {exc}")
        return None


def get_live_prices(tickers: list[str], allow_fallback: bool = True) -> dict[str, float]:
    """Best-effort current prices from Yahoo's quote endpoint."""
    tickers = sorted({t.upper().strip() for t in tickers if t})
    out: dict[str, float] = {}
    for i in range(0, len(tickers), 50):
        chunk = tickers[i:i + 50]
        url = ("https://query1.finance.yahoo.com/v7/finance/quote?symbols="
               + urllib.parse.quote(",".join(chunk)))
        try:
            d = json.load(_get(url, timeout=8))
            for q in d.get("quoteResponse", {}).get("result", []):
                sym = q.get("symbol")
                px = q.get("regularMarketPrice") or q.get("postMarketPrice") or q.get("preMarketPrice")
                if sym and px is not None:
                    out[sym.upper()] = float(px)
        except Exception as exc:
            print(f"[stockinfo] live quote batch failed: {exc}")
    if not allow_fallback:
        return out
    missing = [t for t in tickers if t not in out]
    for ticker in missing[:80]:
        chart = (_ohlcv(ticker, "1d", "1m") or _spark_ohlcv(ticker, "1d", "1m")
                 or _nasdaq_intraday(ticker, "1d")
                 or _ohlcv(ticker, "1d", "5m") or _spark_ohlcv(ticker, "1d", "5m")
                 or _ohlcv(ticker, "5d", "15m") or _spark_ohlcv(ticker, "5d", "15m")
                 or _nasdaq_intraday(ticker, "5d"))
        try:
            if chart and chart.get("c"):
                out[ticker] = float(chart["c"][-1])
        except (TypeError, ValueError):
            continue
    return out


def get_intraday_series(ticker: str) -> dict:
    ticker = ticker.upper().strip()
    intraday_1d = (_ohlcv(ticker, "1d", "1m") or _spark_ohlcv(ticker, "1d", "1m")
                   or _nasdaq_intraday(ticker, "1d")
                   or _ohlcv(ticker, "1d", "5m") or _spark_ohlcv(ticker, "1d", "5m"))
    intraday_5d = (_ohlcv(ticker, "5d", "15m") or _spark_ohlcv(ticker, "5d", "15m")
                   or _nasdaq_intraday(ticker, "5d"))
    price = None
    if intraday_1d and intraday_1d.get("c"):
        price = intraday_1d["c"][-1]
    elif intraday_5d and intraday_5d.get("c"):
        price = intraday_5d["c"][-1]
    return {
        "ticker": ticker,
        "price": price,
        "series": {"intraday_1d": intraday_1d, "intraday_5d": intraday_5d},
    }


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


def get_stock_detail(ticker: str, agents_view: dict | None = None,
                     force_intraday: bool = False) -> dict:
    ticker = ticker.upper().strip()
    cached = _CACHE.get(ticker)
    if cached and (_time.time() - cached[0]) < _CACHE_TTL and not force_intraday:
        d = dict(cached[1])
        d["holders"] = _holders_for(ticker, agents_view)   # keep holders fresh
        return d

    daily = _ohlcv(ticker, "1y", "1d")
    if not daily:
        return {"ticker": ticker,
                "error": "Live data is temporarily unavailable (rate limited). Try again shortly."}
    weekly = _ohlcv(ticker, "max", "1wk")
    intraday_1d = (_ohlcv(ticker, "1d", "1m") or _spark_ohlcv(ticker, "1d", "1m")
                   or _nasdaq_intraday(ticker, "1d")
                   or _ohlcv(ticker, "1d", "5m") or _spark_ohlcv(ticker, "1d", "5m"))
    intraday_5d = (_ohlcv(ticker, "5d", "15m") or _spark_ohlcv(ticker, "5d", "15m")
                   or _nasdaq_intraday(ticker, "5d"))

    closes = daily["c"]
    wcloses = weekly["c"] if weekly else []
    meta = daily["meta"]
    price = meta.get("regularMarketPrice") or (closes[-1] if closes else None)

    fun = get_fundamentals(ticker)
    ap, sd = fun.get("assetProfile", {}), fun.get("summaryDetail", {})
    ks, fd = fun.get("defaultKeyStatistics", {}), fun.get("financialData", {})
    pr = fun.get("price", {})
    dy = _raw(sd, "dividendYield")
    if dy is not None and dy < 1:        # quoteSummary gives a fraction; details gives %
        dy = dy * 100

    detail = {
        "ticker": ticker,
        "name": meta.get("longName") or meta.get("shortName") or pr.get("longName") or ticker,
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "currency": meta.get("currency", "USD"),
        "price": price,
        "chg_1d": _ret(closes, 1),
        "fiftyTwoWeekHigh": meta.get("fiftyTwoWeekHigh"),
        "fiftyTwoWeekLow": meta.get("fiftyTwoWeekLow"),
        "series": {
            "intraday_1d": ({k: intraday_1d[k] for k in ("t", "o", "h", "l", "c", "v")}
                            if intraday_1d else None),
            "intraday_5d": ({k: intraday_5d[k] for k in ("t", "o", "h", "l", "c", "v")}
                            if intraday_5d else None),
            "daily": {k: daily[k] for k in ("t", "o", "h", "l", "c", "v")},
            "weekly": ({k: weekly[k] for k in ("t", "o", "h", "l", "c", "v")} if weekly else None),
        },
        "technicals": {
            "rsi14": _rsi(closes), "ret_1m": _ret(closes, 21), "ret_3m": _ret(closes, 63),
            "ret_1y": ((closes[-1] / closes[0] - 1) * 100) if len(closes) > 1 else None,
            "ret_5y": ((wcloses[-1] / wcloses[-260] - 1) * 100) if len(wcloses) > 260 else None,
            "volatility": _volatility(closes),
        },
        "signal": {},
        "profile": {
            "sector": ap.get("sector"), "industry": ap.get("industry"),
            "country": ap.get("country"), "website": ap.get("website"),
            "employees": ap.get("fullTimeEmployees"), "summary": ap.get("longBusinessSummary"),
        },
        "financials": {
            "marketCap": _raw(pr, "marketCap") or _raw(sd, "marketCap"),
            "enterpriseValue": _raw(ks, "enterpriseValue"),
            "trailingPE": _raw(sd, "trailingPE"), "forwardPE": _raw(sd, "forwardPE"),
            "pegRatio": _raw(ks, "pegRatio") or _raw(ks, "trailingPegRatio"),
            "priceToBook": _raw(ks, "priceToBook"),
            "priceToSales": _raw(sd, "priceToSalesTrailing12Months"),
            "evToEbitda": _raw(ks, "enterpriseToEbitda"),
            "profitMargin": _raw(fd, "profitMargins"), "operatingMargin": _raw(fd, "operatingMargins"),
            "grossMargin": _raw(fd, "grossMargins"), "roe": _raw(fd, "returnOnEquity"),
            "roa": _raw(fd, "returnOnAssets"), "ebitda": _raw(fd, "ebitda"),
            "freeCashflow": _raw(fd, "freeCashflow"),
            "eps": _raw(ks, "trailingEps"), "forwardEps": _raw(ks, "forwardEps"),
            "bookValue": _raw(ks, "bookValue"), "revenue": _raw(fd, "totalRevenue"),
            "revenueGrowth": _raw(fd, "revenueGrowth"), "earningsGrowth": _raw(fd, "earningsGrowth"),
            "totalCash": _raw(fd, "totalCash"), "totalDebt": _raw(fd, "totalDebt"),
            "debtToEquity": _raw(fd, "debtToEquity"), "currentRatio": _raw(fd, "currentRatio"),
            "dividendYield": dy, "payoutRatio": _raw(sd, "payoutRatio"),
            "beta": _raw(sd, "beta") or _raw(ks, "beta"),
            "sma50": _raw(sd, "fiftyDayAverage"), "sma200": _raw(sd, "twoHundredDayAverage"),
            "avgVolume": _raw(sd, "averageVolume"), "sharesOutstanding": _raw(ks, "sharesOutstanding"),
            "heldInsiders": _raw(ks, "heldPercentInsiders"),
            "heldInstitutions": _raw(ks, "heldPercentInstitutions"),
            "shortRatio": _raw(ks, "shortRatio"),
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
