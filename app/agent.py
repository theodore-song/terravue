"""The trading agent: consumes daily suggestions and manages the paper portfolio.

Rules (deterministic, risk-aware):
  * SELL/trim positions whose composite signal has turned negative.
  * BUY/add the highest-conviction names, sized inversely to volatility and
    capped per position, while keeping a cash reserve.
"""
from __future__ import annotations

from datetime import datetime

from . import data
from .config import (CASH_RESERVE_PCT, MAX_POSITION_PCT,
                     MIN_CONVICTION_TO_BUY, SELL_CONVICTION)
from .portfolio import Portfolio


def _target_weight(composite: float, volatility: float) -> float:
    """Higher conviction -> bigger target; higher vol -> smaller target."""
    conviction = min(composite / 2.0, 1.0)            # 0..1
    vol_adj = 1.0 / (1.0 + max(volatility, 0.1) * 2)  # dampen volatile names
    return min(conviction * vol_adj * MAX_POSITION_PCT, MAX_POSITION_PCT)


def run_cycle(suggestions: dict, portfolio: Portfolio | None = None) -> dict:
    """Execute one trading cycle against the given suggestions. Returns a log."""
    portfolio = portfolio or Portfolio.load()
    actions: list[str] = []

    sugg = suggestions.get("suggestions", [])
    by_ticker = {s["ticker"]: s for s in sugg}

    # Price map for valuation (use the suggestion's price; refresh holdings).
    prices = {s["ticker"]: s["price"] for s in sugg}
    for t in portfolio.positions:
        if t not in prices:
            p = data.latest_price(t)
            if p:
                prices[t] = p

    # --- 1. Exits: sell holdings that have turned bearish or left the list ---
    for ticker in list(portfolio.positions.keys()):
        s = by_ticker.get(ticker)
        price = prices.get(ticker, portfolio.positions[ticker].avg_cost)
        if s is None:
            continue  # no fresh signal; hold
        if s["composite"] <= SELL_CONVICTION:
            shares = portfolio.positions[ticker].shares
            if portfolio.sell(ticker, shares, price,
                              f"signal turned bearish ({s['composite']:+.2f})"):
                actions.append(f"SOLD {shares:.2f} {ticker} @ ${price:.2f} "
                               f"(signal {s['composite']:+.2f})")

    # --- 2. Entries / adds: top conviction names ----------------------------
    equity = portfolio.equity(prices)
    candidates = sorted(
        [s for s in sugg if s["composite"] >= MIN_CONVICTION_TO_BUY],
        key=lambda s: s["composite"], reverse=True,
    )

    for s in candidates:
        ticker, price = s["ticker"], s["price"]
        vol = s.get("indicators", {}).get("volatility", 0.3)
        target_val = _target_weight(s["composite"], vol) * equity

        current_val = 0.0
        if ticker in portfolio.positions:
            current_val = portfolio.positions[ticker].market_value(price)

        gap = target_val - current_val
        min_cash = equity * CASH_RESERVE_PCT
        investable = max(portfolio.cash - min_cash, 0)
        spend = min(gap, investable)
        if spend < max(price, equity * 0.01):   # skip dust trades
            continue
        shares = spend / price
        if portfolio.buy(ticker, shares, price,
                         f"high conviction ({s['composite']:+.2f}), "
                         f"target {target_val/equity*100:.0f}% weight"):
            actions.append(f"BOUGHT {shares:.2f} {ticker} @ ${price:.2f} "
                           f"(signal {s['composite']:+.2f})")

    if not actions:
        actions.append("No trades — holdings already aligned with signals.")

    portfolio.record_equity(prices)
    portfolio.save()
    portfolio.publish_view(prices)  # precomputed snapshot for the read-only site

    return {
        "ran_at": datetime.now().isoformat(timespec="seconds"),
        "actions": actions,
        "snapshot": portfolio.snapshot(prices),
    }
