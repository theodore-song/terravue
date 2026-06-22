"""Daily market analysis -> ranked suggestions, with an optional LLM narrative."""
from __future__ import annotations

import json
from datetime import datetime

from . import data, store, strategies
from .config import (ANTHROPIC_API_KEY, ANTHROPIC_MODEL, LLM_ENABLED)
from .universe import get_universe

# How many names to send to the LLM for the narrative (top longs + bottom).
LLM_TOP_N = 15
LLM_BOTTOM_N = 5


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
    store.write_json("suggestions", result)
    return result


def load_suggestions() -> dict | None:
    return store.read_json("suggestions")
