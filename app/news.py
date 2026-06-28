"""Headline fetching + lightweight sentiment for news-reactive trading.

Sentiment is a finance-tuned keyword lexicon (no API key needed). If a Claude
key is configured it could be swapped in, but the lexicon keeps it free and
deterministic. Scores are in roughly [-1, +1].
"""
from __future__ import annotations

import re

_POS = {
    "beat", "beats", "surge", "surges", "soar", "soars", "jump", "jumps", "rally",
    "rallies", "upgrade", "upgraded", "raises", "raised", "record", "strong",
    "growth", "outperform", "bullish", "gains", "gain", "tops", "wins", "approval",
    "approved", "breakthrough", "expands", "profit", "buyback", "upbeat", "rebound",
    "higher", "boosts", "boost", "optimistic", "wins", "soaring", "rallies",
}
_NEG = {
    "miss", "misses", "plunge", "plunges", "slump", "slumps", "fall", "falls",
    "drop", "drops", "downgrade", "downgraded", "cut", "cuts", "weak", "lawsuit",
    "probe", "investigation", "recall", "warns", "warning", "bearish", "loss",
    "losses", "fraud", "halt", "halts", "slashes", "slash", "tumble", "tumbles",
    "sinks", "sink", "fears", "concern", "concerns", "decline", "declines", "lower",
    "layoffs", "bankruptcy", "selloff", "slides", "slide", "crash", "plummet",
}
_WORD = re.compile(r"[a-z']+")


def score_text(text: str) -> float:
    words = _WORD.findall((text or "").lower())
    if not words:
        return 0.0
    pos = sum(1 for w in words if w in _POS)
    neg = sum(1 for w in words if w in _NEG)
    if pos == neg == 0:
        return 0.0
    return max(-1.0, min(1.0, (pos - neg) / (pos + neg)))


def _item_fields(item: dict) -> dict | None:
    c = item.get("content", item)
    title = c.get("title")
    if not title:
        return None
    prov = c.get("provider")
    publisher = prov.get("displayName") if isinstance(prov, dict) else c.get("publisher")
    link = ""
    for k in ("canonicalUrl", "clickThroughUrl"):
        v = c.get(k)
        if isinstance(v, dict) and v.get("url"):
            link = v["url"]; break
    return {
        "title": title,
        "publisher": publisher or "",
        "link": link or c.get("link", ""),
        "published": c.get("pubDate") or c.get("providerPublishTime") or "",
        "sentiment": round(score_text(title + " " + (c.get("summary") or "")), 3),
    }


def fetch_news(ticker: str, limit: int = 8) -> list[dict]:
    try:
        import yfinance as yf
        raw = yf.Ticker(ticker).news or []
    except Exception:
        return []
    out = []
    for item in raw[:limit * 2]:
        f = _item_fields(item)
        if f:
            out.append(f)
        if len(out) >= limit:
            break
    return out


def sentiment_map(tickers: list[str], limit: int = 6) -> dict[str, dict]:
    """For each ticker: {sentiment: float, headlines: [...]}.

    sentiment = mean headline sentiment (0 if no news)."""
    from concurrent.futures import ThreadPoolExecutor
    out: dict[str, dict] = {}

    def one(t):
        items = fetch_news(t, limit)
        if not items:
            return t, {"sentiment": 0.0, "headlines": []}
        # average only the headlines that actually carry sentiment, so neutral
        # filler doesn't drown out a strong beat/miss/downgrade.
        nz = [i["sentiment"] for i in items if i["sentiment"] != 0]
        avg = (sum(nz) / len(nz)) if nz else 0.0
        return t, {"sentiment": round(avg, 3), "headlines": items}

    with ThreadPoolExecutor(max_workers=8) as ex:
        for t, v in ex.map(one, tickers):
            out[t] = v
    return out
