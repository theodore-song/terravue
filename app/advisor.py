"""Daily market analysis -> ranked suggestions, with an optional LLM narrative."""
from __future__ import annotations

import json
import math
from datetime import datetime

from . import data, store, strategies
from .config import (ANTHROPIC_API_KEY, ANTHROPIC_MODEL, LLM_ENABLED)
from .universe import get_universe

# How many names to send to the LLM for the narrative (top longs + bottom).
LLM_TOP_N = 15
LLM_BOTTOM_N = 5
LONG_TERM_TOP_N = 14
LONG_TERM_DETAIL_SCAN = 160


def analyze_watchlist(tickers: list[str] | None = None) -> list[strategies.TickerAnalysis]:
    tickers = tickers or get_universe()
    frames = data.get_histories(tickers, period="1y")
    results: list[strategies.TickerAnalysis] = []
    for t in tickers:
        df = frames.get(t)
        if df is None:
            continue
        a = strategies.analyze(t, df)
        if a is not None:
            results.append(a)
    results.sort(key=lambda a: a.composite, reverse=True)
    return results


def _rule_based_narrative(analyses: list[strategies.TickerAnalysis]) -> str:
    buys = [a for a in analyses if a.action == "BUY"]
    sells = [a for a in analyses if a.action == "SELL"]
    parts = []
    if buys:
        names = ", ".join(f"{a.ticker} (+{a.composite:.1f})" for a in buys[:5])
        parts.append(f"Bullish setups today: {names}. These show the strongest "
                     f"blend of trend, momentum and breakout signals.")
    if sells:
        names = ", ".join(f"{a.ticker} ({a.composite:.1f})" for a in sells[:5])
        parts.append(f"Weak / overextended: {names} — consider trimming or avoiding.")
    if not buys and not sells:
        parts.append("Signals are mixed across the watchlist; mostly a hold day "
                     "with no strong edge.")
    parts.append("This is an automated technical read, not financial advice.")
    return " ".join(parts)


def _llm_narrative(analyses: list[strategies.TickerAnalysis]) -> str | None:
    if not LLM_ENABLED:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    # Only send the strongest longs and weakest names, not the whole universe.
    subset = analyses[:LLM_TOP_N] + analyses[-LLM_BOTTOM_N:] if len(analyses) > LLM_TOP_N + LLM_BOTTOM_N else analyses
    payload = [a.to_dict() for a in subset]
    prompt = (
        "You are a markets analyst writing a brief daily note for a paper-trading "
        "portfolio. Below is today's quantitative signal data for a watchlist "
        "(composite score ranges roughly -2 bearish to +2 bullish, built from "
        "moving-average trend, RSI, MACD and breakout strategies).\n\n"
        f"{json.dumps(payload, indent=2)}\n\n"
        "Write a concise note (max ~180 words): the overall market tone implied by "
        "these signals, the 2-3 most attractive longs and why, anything to reduce or "
        "avoid, and one risk to watch. Be specific and reference the numbers. End "
        "with a one-line reminder that this is automated analysis, not financial "
        "advice. Plain prose, no markdown headers."
    )
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        print(f"[advisor] LLM narrative failed, falling back: {exc}")
        return None


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _num(v, default: float | None = None) -> float | None:
    try:
        if v is None:
            return default
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _news_sentiment() -> dict[str, float]:
    feed = store.read_json("news_feed") or {}
    vals: dict[str, list[float]] = {}
    for item in (feed.get("headlines") or []) + (feed.get("reactions") or []):
        ticker = item.get("ticker")
        sent = _num(item.get("sentiment"))
        if ticker and sent is not None:
            vals.setdefault(ticker, []).append(sent)
    return {t: sum(xs) / len(xs) for t, xs in vals.items() if xs}


def _long_term_score(s: dict, detail: dict | None, news_by: dict[str, float]) -> dict:
    ind = s.get("indicators", {})
    fin = (detail or {}).get("financials", {})
    ticker = s.get("ticker")

    ret_1m = _num(ind.get("ret_1m"), 0.0) or 0.0
    composite = _num(s.get("composite"), 0.0) or 0.0
    profit_margin = _num(fin.get("profitMargin"))
    operating_margin = _num(fin.get("operatingMargin"))
    revenue_growth = _num(fin.get("revenueGrowth"))
    earnings_growth = _num(fin.get("earningsGrowth"))
    revenue = _num(fin.get("revenue"))
    target = _num(fin.get("targetMeanPrice"))
    price = _num(s.get("price"))
    news = news_by.get(ticker, 0.0)

    momentum = _clamp(ret_1m / 18.0)
    signal = _clamp(composite / 1.6)
    profitability = _clamp(((profit_margin or 0.0) * 2.1 + (operating_margin or 0.0) * 1.4))
    growth = _clamp(((revenue_growth or 0.0) * 1.8 + (earnings_growth or 0.0) * 1.4))
    sales = _clamp((math.log10(max(revenue or 0.0, 1.0)) - 9.0) / 3.0) if revenue else 0.0
    analyst = _clamp(((target / price - 1) if target and price else 0.0) * 2.0)
    news_score = _clamp(news)

    score = (
        momentum * 0.22
        + signal * 0.18
        + profitability * 0.17
        + growth * 0.20
        + sales * 0.08
        + analyst * 0.05
        + news_score * 0.10
    )

    positives = []
    cautions = []
    if ret_1m >= 6:
        positives.append(f"1-month momentum is strong at {ret_1m:+.1f}%")
    elif ret_1m < 0:
        cautions.append(f"recent momentum is soft at {ret_1m:+.1f}%")
    if composite >= 0.7:
        positives.append(f"technical signal is supportive ({composite:+.2f})")
    elif composite < 0:
        cautions.append(f"technical signal is not yet confirming ({composite:+.2f})")
    if profit_margin is not None and profit_margin > 0.10:
        positives.append(f"profit margin is healthy at {profit_margin * 100:.1f}%")
    elif profit_margin is not None and profit_margin < 0:
        cautions.append("profitability is negative")
    if revenue_growth is not None and revenue_growth > 0.08:
        positives.append(f"sales growth is running {revenue_growth * 100:.1f}%")
    if earnings_growth is not None and earnings_growth > 0.05:
        positives.append(f"earnings growth is {earnings_growth * 100:.1f}%")
    elif earnings_growth is not None and earnings_growth < 0:
        cautions.append("earnings growth is negative")
    if news > 0.2:
        positives.append("recent news tone is positive")
    elif news < -0.2:
        cautions.append("recent news tone is negative")

    reason = "; ".join(positives[:3]) or "best blend of available longer-term factors"
    if cautions:
        reason += ". Watch: " + "; ".join(cautions[:2])

    explanation = (
        f"Confidence is {round(max(0, min(100, 50 + score * 50)), 0):.0f}% because the model "
        f"weighted momentum ({momentum * 100:+.0f}), technical signal ({signal * 100:+.0f}), "
        f"profitability ({profitability * 100:+.0f}), growth ({growth * 100:+.0f}), "
        f"sales scale ({sales * 100:+.0f}), analyst upside ({analyst * 100:+.0f}), "
        f"and news tone ({news_score * 100:+.0f})."
    )

    return {
        "ticker": ticker,
        "price": s.get("price"),
        "score": round(score * 100, 1),
        "confidence": round(max(0, min(100, 50 + score * 50)), 0),
        "horizon": "3-12 months",
        "call": "Long-term buy" if score >= 0.35 else "Watchlist",
        "reason": reason,
        "confidence_explanation": explanation,
        "factors": {
            "momentum": round(momentum * 100, 0),
            "earnings": round((earnings_growth or 0.0) * 100, 1) if earnings_growth is not None else None,
            "news": round(news_score * 100, 0),
            "profitability": round(profitability * 100, 0),
            "growth": round(growth * 100, 0),
            "sales": round(sales * 100, 0),
        },
    }


def build_long_term_suggestions(suggestions: dict) -> list[dict]:
    """Rank calmer, longer-horizon ideas using technicals plus stored fundamentals."""
    rows = suggestions.get("suggestions", [])
    if not rows:
        return []
    news_by = _news_sentiment()
    pool = sorted(
        rows,
        key=lambda s: (
            (s.get("composite", 0) or 0) * 0.65
            + (s.get("indicators", {}).get("ret_1m", 0) or 0) / 25.0 * 0.35
        ),
        reverse=True,
    )[:LONG_TERM_DETAIL_SCAN]
    scored = []
    for s in pool:
        detail = store.read_json("sd:" + s["ticker"]) or {}
        scored.append(_long_term_score(s, detail, news_by))
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:LONG_TERM_TOP_N]


def generate_suggestions(tickers: list[str] | None = None) -> dict:
    """Produce today's suggestions object and persist it."""
    analyses = analyze_watchlist(tickers)
    narrative = _llm_narrative(analyses) or _rule_based_narrative(analyses)
    result = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "narrative": narrative,
        "narrative_source": "claude" if LLM_ENABLED else "rules",
        "suggestions": [a.to_dict() for a in analyses],
    }
    result["long_term_suggestions"] = build_long_term_suggestions(result)
    store.write_json("suggestions", result)
    return result


def load_suggestions() -> dict | None:
    return store.read_json("suggestions")


def refresh_long_term_suggestions(suggestions: dict) -> dict:
    """Rebuild longer-term ideas after detail/fundamental blobs are refreshed."""
    suggestions = dict(suggestions or {})
    suggestions["long_term_suggestions"] = build_long_term_suggestions(suggestions)
    store.write_json("suggestions", suggestions)
    return suggestions
