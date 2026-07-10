"""Stock-focused chat over the latest Terravue data."""
from __future__ import annotations

import json
import re

from .config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL


def _fmt_pct(v) -> str:
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_score(v) -> str:
    try:
        return f"{float(v):+.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _find_tickers(message: str, suggestions: dict) -> list[str]:
    known = {s.get("ticker") for s in suggestions.get("suggestions", [])}
    found = []
    for token in re.findall(r"\b[A-Z]{1,5}\b", message.upper()):
        if token in known and token not in found:
            found.append(token)
    return found[:5]


def _ticker_context(ticker: str, suggestions: dict, agents_view: dict,
                    news_feed: dict) -> dict:
    s = next((x for x in suggestions.get("suggestions", []) if x.get("ticker") == ticker), {})
    lt = next((x for x in suggestions.get("long_term_suggestions", []) if x.get("ticker") == ticker), {})
    holders = []
    for a in agents_view.get("agents", []):
        for h in a.get("snapshot", {}).get("holdings", []):
            if h.get("ticker") == ticker:
                holders.append({
                    "agent": a.get("name"),
                    "weight": h.get("weight"),
                    "unrealized_pct": h.get("unrealized_pct"),
                })
    headlines = [
        h for h in news_feed.get("headlines", [])
        if h.get("ticker") == ticker
    ][:3]
    return {"signal": s, "long_term": lt, "holders": holders, "headlines": headlines}


def _rule_answer(message: str, suggestions: dict, agents_view: dict,
                 news_feed: dict) -> str:
    tickers = _find_tickers(message, suggestions)
    if tickers:
        parts = []
        for ticker in tickers:
            ctx = _ticker_context(ticker, suggestions, agents_view, news_feed)
            s = ctx["signal"]
            lt = ctx["long_term"]
            ind = s.get("indicators", {})
            line = (
                f"{ticker}: signal {s.get('action', 'n/a')} at {_fmt_score(s.get('composite'))}, "
                f"1-day {_fmt_pct(ind.get('chg_1d'))}, 1-month {_fmt_pct(ind.get('ret_1m'))}."
            )
            if lt:
                line += (
                    f" Longer-term view: {lt.get('call')} with {lt.get('confidence')}% "
                    f"confidence because {lt.get('reason')}"
                )
            if ctx["holders"]:
                held = ", ".join(
                    f"{h['agent']} ({h.get('weight', 0)}%, {_fmt_pct(h.get('unrealized_pct'))})"
                    for h in ctx["holders"][:4]
                )
                line += f" Held by: {held}."
            if ctx["headlines"]:
                line += " Latest headline: " + ctx["headlines"][0].get("title", "")
            parts.append(line)
        return "\n\n".join(parts) + "\n\nThis is automated educational analysis, not financial advice."

    ideas = suggestions.get("long_term_suggestions") or []
    if ideas:
        top = ideas[:5]
        rows = [
            f"{x['ticker']} ({x.get('confidence')}% confidence): {x.get('reason')}"
            for x in top
        ]
        return (
            "The strongest longer-term ideas right now are:\n"
            + "\n".join(f"- {r}" for r in rows)
            + "\n\nAsk about a ticker like AAPL or NVDA for a focused read. This is not financial advice."
        )

    ranked = sorted(
        suggestions.get("suggestions", []),
        key=lambda x: x.get("composite", 0),
        reverse=True,
    )[:5]
    rows = [
        f"{x['ticker']}: {x.get('action')} signal {_fmt_score(x.get('composite'))}, "
        f"1-month {_fmt_pct(x.get('indicators', {}).get('ret_1m'))}"
        for x in ranked
    ]
    return (
        "The long-term list will fill in after the next daily run. From current signals, "
        "the strongest names are:\n"
        + "\n".join(f"- {r}" for r in rows)
        + "\n\nThis is automated educational analysis, not financial advice."
    )


def _llm_answer(message: str, suggestions: dict, agents_view: dict,
                news_feed: dict) -> str | None:
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
    except ImportError:
        return None

    tickers = _find_tickers(message, suggestions)
    contexts = [_ticker_context(t, suggestions, agents_view, news_feed) for t in tickers]
    if not contexts:
        contexts = (suggestions.get("long_term_suggestions") or [])[:8]
    prompt = (
        "You are Terravue's stock-analysis chat. Answer using only this app data. "
        "Be concise, specific, and include uncertainty. Do not claim guaranteed returns. "
        "End by saying this is educational, not financial advice.\n\n"
        f"User question: {message}\n\n"
        f"Context JSON:\n{json.dumps(contexts, indent=2)[:12000]}"
    )
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=650,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        print(f"[chat] LLM failed, using rule answer: {exc}")
        return None


def answer(message: str, suggestions: dict, agents_view: dict, news_feed: dict) -> dict:
    message = (message or "").strip()
    if not message:
        return {"answer": "Ask me about a ticker, a long-term idea, or what the agents are holding."}
    text = _llm_answer(message, suggestions, agents_view, news_feed)
    source = "claude" if text else "rules"
    text = text or _rule_answer(message, suggestions, agents_view, news_feed)
    return {"answer": text, "source": source}
