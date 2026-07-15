"""Helpers for displaying configured agents before their first published run."""
from __future__ import annotations

from datetime import datetime

from .agents import AGENTS
from .config import STARTING_CASH


def _signal_score(s: dict, name: str) -> float:
    for x in s.get("signals", []):
        if x.get("name") == name:
            return x.get("score", 0.0)
    return 0.0


def _empty_snapshot() -> dict:
    return {
        "cash": round(STARTING_CASH, 2),
        "positions_value": 0.0,
        "equity": round(STARTING_CASH, 2),
        "starting_cash": STARTING_CASH,
        "total_return_pct": 0.0,
        "holdings": [],
        "num_positions": 0,
        "created": datetime.utcnow().isoformat(),
    }


def _starter_buyable(adef, s: dict, score: float) -> bool:
    indicators = s.get("indicators", {}) or {}
    composite = s.get("composite", 0.0) or 0.0
    ret_1m = indicators.get("ret_1m", 0.0) or 0.0
    chg_1d = indicators.get("chg_1d", 0.0) or 0.0
    volatility = indicators.get("volatility", 0.3) or 0.3
    trend = _signal_score(s, "Trend (MA cross)")
    macd = _signal_score(s, "MACD")
    breakout = _signal_score(s, "Breakout (52w channel)")
    rsi = _signal_score(s, "Momentum (RSI)")
    threshold = max(adef.buy_threshold * 0.55, 0.35)

    if score < threshold or composite < 0.25:
        return False
    if chg_1d < -2.5 or volatility > 1.2:
        return False
    if s.get("ticker") in adef.focus_tickers:
        return ret_1m > -8 and (trend > -0.25 or macd > -0.25 or breakout > 0.35)
    if adef.id == "sage":
        return ret_1m > -10 and (rsi > 0.15 or macd > -0.15) and trend > -0.45
    return ret_1m > -8 and trend > -0.3 and macd > -0.35


def _long_term_ideas(suggestions: dict) -> list[dict]:
    ideas = suggestions.get("long_term_suggestions") or []
    if ideas:
        return ideas
    fallback = []
    for s in suggestions.get("suggestions", []):
        indicators = s.get("indicators", {}) or {}
        composite = s.get("composite", 0.0) or 0.0
        ret_1m = indicators.get("ret_1m", 0.0) or 0.0
        chg_1d = indicators.get("chg_1d", 0.0) or 0.0
        volatility = indicators.get("volatility", 0.3) or 0.3
        if composite < 0.35 or ret_1m < -6 or chg_1d < -2.5 or volatility > 1.05:
            continue
        score = composite * 55 + ret_1m * 0.9 + chg_1d * 0.35
        score += max(_signal_score(s, "Trend (MA cross)"), 0) * 9
        score += max(_signal_score(s, "MACD"), 0) * 6
        score -= max(volatility - 0.45, 0) * 18
        fallback.append({
            "ticker": s.get("ticker"),
            "score": round(score, 2),
            "confidence": round(max(45, min(90, 54 + score / 2.2))),
        })
    fallback.sort(key=lambda x: (x["confidence"], x["score"]), reverse=True)
    return fallback[:18]


def _snapshot_from_tickers(tickers: list[str], suggestions: dict,
                           weight: float = 0.04) -> dict:
    by_ticker = {s.get("ticker"): s for s in suggestions.get("suggestions", [])}
    holdings = []
    cash = STARTING_CASH
    for ticker in tickers:
        s = by_ticker.get(ticker)
        price = s.get("price") if s else None
        if not price or cash <= STARTING_CASH * 0.22:
            continue
        spend = min(STARTING_CASH * weight, cash - STARTING_CASH * 0.22)
        if spend < price:
            continue
        shares = spend / price
        cash -= spend
        holdings.append({
            "ticker": ticker,
            "shares": round(shares, 2),
            "avg_cost": round(price, 2),
            "price": round(price, 2),
            "market_value": round(spend, 2),
            "unrealized": 0.0,
            "unrealized_pct": 0.0,
            "weight": round(spend / STARTING_CASH * 100, 1),
        })
    positions_value = round(sum(h["market_value"] for h in holdings), 2)
    return {
        "cash": round(cash, 2),
        "positions_value": positions_value,
        "equity": round(cash + positions_value, 2),
        "starting_cash": STARTING_CASH,
        "total_return_pct": 0.0,
        "holdings": holdings,
        "num_positions": len(holdings),
        "created": datetime.utcnow().isoformat(),
    }


def _starter_snapshot(adef, suggestions: dict) -> dict:
    if adef.long_term_suggestions:
        tickers = [x["ticker"] for x in _long_term_ideas(suggestions)[:adef.max_positions]]
        return _snapshot_from_tickers(tickers, suggestions, weight=0.055)
    scored = [
        (adef.score(s), s.get("ticker"))
        for s in suggestions.get("suggestions", [])
        if _starter_buyable(adef, s, adef.score(s))
    ]
    scored.sort(reverse=True)
    tickers = [ticker for _, ticker in scored[:min(5, adef.max_positions)]]
    return _snapshot_from_tickers(tickers, suggestions, weight=0.04)


def _copy_leader_snapshot(leader: dict | None, max_positions: int) -> dict:
    if not leader:
        return _empty_snapshot()
    holdings = (leader.get("snapshot", {}) or {}).get("holdings", [])[:max_positions]
    tickers = [h["ticker"] for h in holdings]
    suggestions = {"suggestions": [
        {"ticker": h["ticker"], "price": h.get("price") or h.get("avg_cost")}
        for h in holdings
    ]}
    return _snapshot_from_tickers(tickers, suggestions, weight=0.04)


def augment_agents_view(view: dict | None) -> dict:
    """Merge current code-defined agents into the last stored competition view.

    The trading job persists real portfolios to KV. When new agents are deployed
    before the next trading job runs, this keeps them visible on the site as
    pending agents instead of hiding them behind the stale snapshot.
    """
    view = dict(view or {})
    agents = list(view.get("agents") or [])
    seen = {a.get("id") for a in agents}
    defs = {a.id: a for a in AGENTS}
    try:
        from . import store
        suggestions = store.read_json("suggestions") or {"suggestions": []}
    except Exception:
        suggestions = {"suggestions": []}

    leader = max(
        (a for a in agents if (a.get("snapshot", {}) or {}).get("num_positions", 0) > 0),
        key=lambda a: (a.get("snapshot", {}) or {}).get("total_return_pct", 0),
        default=None,
    )

    for agent in agents:
        adef = defs.get(agent.get("id"))
        if not adef:
            continue
        agent["name"] = adef.name
        agent["style"] = adef.style
        agent["blurb"] = adef.blurb
        agent["color"] = adef.color
        snapshot = agent.get("snapshot") or _empty_snapshot()
        cash_pct = snapshot.get("cash", 0) / max(snapshot.get("equity", 1), 1) * 100
        if adef.copy_leader:
            agent["strategy_note"] = (
                "Daily strategy: use the leader as a watchlist, then copy only holdings "
                "with positive MACD, trend and breakout momentum. If the leader's book "
                f"is weak, fill with stronger standalone momentum names and keep about {cash_pct:.0f}% cash."
            )
        elif not agent.get("strategy_note"):
            if adef.long_term_suggestions:
                agent["strategy_note"] = (
                    "Daily strategy: hold the highest-confidence long-term suggestions "
                    f"as a benchmark basket, rebalance slowly, and keep about {cash_pct:.0f}% cash."
                )
            else:
                agent["strategy_note"] = (
                    f"Daily strategy: run {adef.style.lower()} scoring, watch the other agents' "
                    f"same-day trades, copy only leading buys that pass this agent's filters, "
                    f"and keep about {cash_pct:.0f}% cash while risk is elevated."
                )
        if not agent.get("movement_note"):
            holdings = snapshot.get("holdings") or []
            if holdings:
                best = max(holdings, key=lambda h: h.get("unrealized_pct", 0))
                worst = min(holdings, key=lambda h: h.get("unrealized_pct", 0))
                agent["movement_note"] = (
                    f"Daily movement note: this stored snapshot shows {best['ticker']} as "
                    f"the strongest open position at {best.get('unrealized_pct', 0):+.1f}% "
                    f"versus cost and {worst['ticker']} as the weakest at "
                    f"{worst.get('unrealized_pct', 0):+.1f}%. Fresh up/down explanations "
                    "will update after the next scheduled run."
                )
            else:
                agent["movement_note"] = (
                    "Daily movement note: no open positions yet, so this agent stayed mostly "
                    "in cash while waiting for stronger setups."
                )
        if snapshot.get("num_positions", 0) == 0 and suggestions.get("suggestions"):
            seeded = (_copy_leader_snapshot(leader, adef.max_positions)
                      if adef.copy_leader else _starter_snapshot(adef, suggestions))
            if seeded.get("num_positions", 0) > 0:
                agent["snapshot"] = seeded
                agent["recent_trades"] = []
                agent["actions"] = [
                    (
                        f"Mirroring {leader.get('name', 'the current leader')}'s visible book "
                        "until the next saved competition run."
                    )
                    if adef.copy_leader else
                    "Opened a risk-capped starter basket from the current live suggestions."
                ]
                agent["movement_note"] = (
                    "Daily movement note: these starter positions are newly seeded from "
                    "current signals, so up/down attribution will begin after prices move."
                )

    for adef in AGENTS:
        if adef.id in seen:
            continue
        snapshot = (_copy_leader_snapshot(leader, adef.max_positions)
                    if adef.copy_leader else _starter_snapshot(adef, suggestions))
        if snapshot.get("num_positions", 0) == 0:
            snapshot = _empty_snapshot()
        agents.append({
            "id": adef.id,
            "name": adef.name,
            "style": adef.style,
            "blurb": adef.blurb,
            "color": adef.color,
            "snapshot": snapshot,
            "recent_trades": [],
            "actions": [
                (
                    f"Mirroring {leader.get('name', 'the current leader')}'s visible book "
                    "until the next saved competition run."
                )
                if adef.copy_leader and snapshot.get("num_positions", 0) else
                "Opened a risk-capped starter basket from the current live suggestions."
                if snapshot.get("num_positions", 0) else
                "Ready — waiting for enough live suggestions to seed positions."
            ],
            "strategy_note": (
                "Daily strategy: hold the highest-confidence long-term suggestions "
                "as a benchmark basket and rebalance slowly."
                if adef.long_term_suggestions else
                "Daily strategy: enter a small starter book from current signals, watch "
                "the other agents' trades, then copy only leading buys that also fit "
                "this agent's strategy."
            ),
            "movement_note": (
                "Daily movement note: these starter positions are newly seeded from "
                "current signals, so up/down attribution will begin after prices move."
                if snapshot.get("num_positions", 0) else
                "Daily movement note: no open positions yet, so there is nothing to explain."
            ),
        })

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

    return {
        "updated": view.get("updated"),
        "agents": agents,
        "leaderboard": leaderboard,
    }


def augment_equity_curves(curves: dict | None, view: dict | None = None) -> dict:
    curves = dict(curves or {})
    date = datetime.utcnow().strftime("%Y-%m-%d")
    for adef in AGENTS:
        curves.setdefault(adef.id, [{"date": date, "equity": round(STARTING_CASH, 2)}])
    return curves
