"""Best-effort live revaluation helpers for the read-only dashboard."""
from __future__ import annotations

from datetime import datetime, timezone


def revalue_agents_view(view: dict, prices: dict[str, float]) -> dict:
    """Recalculate agent snapshots from current prices without changing portfolios."""
    if not prices:
        return view
    view = dict(view or {})
    agents = []
    for agent in view.get("agents", []):
        agent = dict(agent)
        snap = dict(agent.get("snapshot") or {})
        cash = float(snap.get("cash", 0.0) or 0.0)
        holdings = []
        positions_value = 0.0
        for h in snap.get("holdings", []) or []:
            h = dict(h)
            ticker = h.get("ticker")
            price = prices.get(ticker, h.get("price"))
            try:
                price = float(price)
                shares = float(h.get("shares", 0.0) or 0.0)
                avg = float(h.get("avg_cost", price) or price)
            except (TypeError, ValueError):
                holdings.append(h)
                positions_value += float(h.get("market_value", 0.0) or 0.0)
                continue
            market_value = shares * price
            unrealized = (price - avg) * shares
            h["price"] = round(price, 2)
            h["market_value"] = round(market_value, 2)
            h["unrealized"] = round(unrealized, 2)
            h["unrealized_pct"] = round((price / avg - 1) * 100, 2) if avg else 0.0
            holdings.append(h)
            positions_value += market_value
        equity = cash + positions_value
        for h in holdings:
            h["weight"] = round((h.get("market_value", 0.0) or 0.0) / equity * 100, 1) if equity else 0.0
        start = float(snap.get("starting_cash", 100000.0) or 100000.0)
        snap.update({
            "positions_value": round(positions_value, 2),
            "equity": round(equity, 2),
            "total_return_pct": round((equity / start - 1) * 100, 2) if start else 0.0,
            "holdings": holdings,
            "num_positions": len(holdings),
        })
        agent["snapshot"] = snap
        agents.append(agent)

    leaderboard = sorted(
        (
            {
                "id": a["id"],
                "name": a["name"],
                "style": a["style"],
                "color": a["color"],
                "equity": a["snapshot"]["equity"],
                "return_pct": a["snapshot"]["total_return_pct"],
                "num_positions": a["snapshot"]["num_positions"],
            }
            for a in agents
        ),
        key=lambda x: x["return_pct"],
        reverse=True,
    )
    view["agents"] = agents
    view["leaderboard"] = leaderboard
    view["live_updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    view["live_prices"] = len(prices)
    return view
