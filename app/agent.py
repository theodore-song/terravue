"""Trading agents: each consumes the daily suggestions and manages its own
paper portfolio with its own strategy. They compete on total return.

Per agent, each cycle:
  * SELL/exit holdings whose agent-score has turned negative.
  * BUY/add the highest agent-score names, sized by conviction and inverse
    volatility, capped per position and per number of holdings, keeping a cash
    reserve.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import store
from .agents import AGENTS, AgentDef
from .config import CASH_RESERVE_PCT
from .portfolio import Portfolio


SMART_CASH_RESERVE_PCT = max(CASH_RESERVE_PCT, 0.08)
STOP_LOSS_PCT = -8.0
WEAK_GAIN_EXIT_PCT = 10.0


def _target_weight(adef: AgentDef, score: float, volatility: float) -> float:
    conviction = min(max(score, 0) / 3.0, 1.0)         # 0..1, less eager
    vol_adj = 1.0 / (1.0 + max(volatility, 0.1) * 2.6) # damp volatile names
    return min(conviction * vol_adj * adef.max_position_pct, adef.max_position_pct)


def _is_buyable(adef: AgentDef, s: dict, score: float) -> bool:
    """Risk gate shared by agents so they avoid obvious falling knives."""
    indicators = s.get("indicators", {})
    composite = s.get("composite", 0.0)
    ret_1m = indicators.get("ret_1m", 0.0) or 0.0
    volatility = indicators.get("volatility", 0.3) or 0.3
    trend = _signal_score(s, "Trend (MA cross)")
    macd = _signal_score(s, "MACD")
    breakout = _signal_score(s, "Breakout (52w channel)")
    rsi = _signal_score(s, "Momentum (RSI)")

    if score < adef.buy_threshold or composite < -0.15:
        return False
    if volatility > 1.05 and s.get("ticker") not in adef.focus_tickers:
        return False
    if ret_1m < -18 and (trend < 0 or macd < 0):
        return False
    if ret_1m > 45 and rsi < -0.5 and s.get("ticker") not in adef.focus_tickers:
        return False

    # Contrarian agents may buy an oversold bounce, but only if MACD or trend is
    # stabilizing. Everyone else needs trend, MACD, breakout, or a focus ticker.
    if adef.id == "sage":
        return rsi > 0 and (macd > 0 or trend > -0.25 or breakout > 0.25)
    if s.get("ticker") in adef.focus_tickers and composite >= 0.15:
        return trend > -0.75 or macd > -0.5
    return trend > 0 or macd > 0.25 or breakout > 0.75


def _signal_score(s: dict, name: str) -> float:
    for x in s.get("signals", []):
        if x.get("name") == name:
            return x.get("score", 0.0)
    return 0.0


def _sync_to_leader(adef: AgentDef, suggestions: dict, leader: AgentDef | None) -> dict:
    portfolio = Portfolio.load(adef.id)
    actions: list[str] = []
    sugg = suggestions.get("suggestions", [])
    prices = {s["ticker"]: s["price"] for s in sugg}

    if leader is None or leader.id == adef.id:
        curve = portfolio.record_equity(prices)
        portfolio.save()
        return {
            "id": adef.id,
            "actions": ["No leader to copy yet."],
            "snapshot": portfolio.snapshot(prices),
            "recent_trades": portfolio.recent_trades(),
            "curve": curve,
        }

    leader_pf = Portfolio.load(leader.id)
    leader_equity = leader_pf.equity(prices)
    equity = portfolio.equity(prices)

    target_weights = {}
    if leader_equity > 0:
        for ticker, pos in leader_pf.positions.items():
            if ticker in prices:
                target_weights[ticker] = min(
                    pos.market_value(prices[ticker]) / leader_equity,
                    adef.max_position_pct,
                )

    # Sell names the leader no longer owns, plus trim oversized positions.
    for ticker in list(portfolio.positions.keys()):
        price = prices.get(ticker, portfolio.positions[ticker].avg_cost)
        target_val = target_weights.get(ticker, 0.0) * equity
        current_val = portfolio.positions[ticker].market_value(price)
        if target_val <= 0:
            shares = portfolio.positions[ticker].shares
            if portfolio.sell(ticker, shares, price, f"{adef.name}: leader exited {ticker}"):
                actions.append(f"SOLD {shares:.2f} {ticker} @ ${price:.2f} (leader copy)")
        elif current_val > target_val * 1.15:
            shares = (current_val - target_val) / price
            if portfolio.sell(ticker, shares, price, f"{adef.name}: trim to leader weight"):
                actions.append(f"TRIMMED {shares:.2f} {ticker} @ ${price:.2f}")

    equity = portfolio.equity(prices)
    investable = max(portfolio.cash - equity * SMART_CASH_RESERVE_PCT, 0)
    for ticker, weight in sorted(target_weights.items(), key=lambda x: x[1], reverse=True):
        if len(portfolio.positions) >= adef.max_positions and ticker not in portfolio.positions:
            continue
        price = prices[ticker]
        current_val = (portfolio.positions[ticker].market_value(price)
                       if ticker in portfolio.positions else 0.0)
        target_val = weight * equity
        spend = min(max(target_val - current_val, 0.0), investable)
        if spend < max(price, equity * 0.01):
            continue
        shares = spend / price
        if portfolio.buy(ticker, shares, price, f"{adef.name}: copied {leader.name}"):
            actions.append(f"BOUGHT {shares:.2f} {ticker} @ ${price:.2f} (copy {leader.name})")
            investable -= spend

    if not actions:
        actions.append(f"No trades — already aligned with {leader.name}.")

    curve = portfolio.record_equity(prices)
    portfolio.save()
    return {
        "id": adef.id,
        "actions": actions,
        "snapshot": portfolio.snapshot(prices),
        "recent_trades": portfolio.recent_trades(),
        "curve": curve,
    }


def run_cycle(adef: AgentDef, suggestions: dict,
              portfolio: Portfolio | None = None) -> dict:
    """Run one trading cycle for a single agent. Returns a log + snapshot."""
    portfolio = portfolio or Portfolio.load(adef.id)
    actions: list[str] = []

    sugg = suggestions.get("suggestions", [])
    by_ticker = {s["ticker"]: s for s in sugg}
    prices = {s["ticker"]: s["price"] for s in sugg}

    # --- 1. Exits: drop holdings whose agent-score turned negative -----------
    for ticker in list(portfolio.positions.keys()):
        s = by_ticker.get(ticker)
        price = prices.get(ticker, portfolio.positions[ticker].avg_cost)
        if s is None:
            continue
        sc = adef.score(s)
        unrealized_pct = (price / portfolio.positions[ticker].avg_cost - 1) * 100 \
            if portfolio.positions[ticker].avg_cost else 0.0
        should_exit = (
            sc <= adef.sell_threshold
            or unrealized_pct <= STOP_LOSS_PCT
            or (unrealized_pct >= WEAK_GAIN_EXIT_PCT and sc < 0.2)
        )
        if should_exit:
            shares = portfolio.positions[ticker].shares
            reason = f"{adef.name}: score/risk exit ({sc:+.2f}, {unrealized_pct:+.1f}%)"
            if portfolio.sell(ticker, shares, price,
                              reason):
                actions.append(f"SOLD {shares:.2f} {ticker} @ ${price:.2f} ({sc:+.2f})")

    # --- 2. Entries / adds: top agent-score names ---------------------------
    equity = portfolio.equity(prices)
    scored = [(adef.score(s), s) for s in sugg]
    candidates = sorted([x for x in scored if _is_buyable(adef, x[1], x[0])],
                        key=lambda x: x[0], reverse=True)

    for sc, s in candidates:
        if len(portfolio.positions) >= adef.max_positions and s["ticker"] not in portfolio.positions:
            continue
        ticker, price = s["ticker"], s["price"]
        vol = s.get("indicators", {}).get("volatility", 0.3)
        target_val = _target_weight(adef, sc, vol) * equity
        current_val = (portfolio.positions[ticker].market_value(price)
                       if ticker in portfolio.positions else 0.0)
        gap = target_val - current_val
        investable = max(portfolio.cash - equity * SMART_CASH_RESERVE_PCT, 0)
        spend = min(gap, investable)
        if spend < max(price, equity * 0.01):       # skip dust
            continue
        shares = spend / price
        if portfolio.buy(ticker, shares, price,
                         f"{adef.name}: conviction {sc:+.2f}"):
            actions.append(f"BOUGHT {shares:.2f} {ticker} @ ${price:.2f} ({sc:+.2f})")

    if not actions:
        actions.append("No trades — holdings already aligned with signals.")

    curve = portfolio.record_equity(prices)
    portfolio.save()
    snapshot = portfolio.snapshot(prices)
    return {
        "id": adef.id,
        "actions": actions,
        "snapshot": snapshot,
        "recent_trades": portfolio.recent_trades(),
        "curve": curve,
    }


def run_competition(suggestions: dict) -> dict:
    """Run all agents, then publish the combined competition view + curves."""
    agents_out = []
    curves: dict[str, list] = {}
    for adef in [a for a in AGENTS if not a.copy_leader]:
        log = run_cycle(adef, suggestions)
        curves[adef.id] = log["curve"]
        agents_out.append({
            "id": adef.id, "name": adef.name, "style": adef.style,
            "blurb": adef.blurb, "color": adef.color,
            "snapshot": log["snapshot"], "recent_trades": log["recent_trades"],
            "actions": log["actions"],
        })

    interim_leader_id = max(
        agents_out,
        key=lambda a: a["snapshot"]["total_return_pct"],
        default={"id": None},
    )["id"]
    interim_leader = next((a for a in AGENTS if a.id == interim_leader_id), None)

    for adef in [a for a in AGENTS if a.copy_leader]:
        log = _sync_to_leader(adef, suggestions, interim_leader)
        curves[adef.id] = log["curve"]
        agents_out.append({
            "id": adef.id, "name": adef.name, "style": adef.style,
            "blurb": adef.blurb, "color": adef.color,
            "snapshot": log["snapshot"], "recent_trades": log["recent_trades"],
            "actions": log["actions"],
        })

    leaderboard = sorted(
        ({"id": a["id"], "name": a["name"], "style": a["style"], "color": a["color"],
          "equity": a["snapshot"]["equity"], "return_pct": a["snapshot"]["total_return_pct"],
          "num_positions": a["snapshot"]["num_positions"]} for a in agents_out),
        key=lambda x: x["return_pct"], reverse=True)

    view = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "agents": agents_out,
        "leaderboard": leaderboard,
    }
    store.write_json("agents_view", view)
    store.write_json("equity_curves", curves)
    return view


NEWS_SELL = -0.35   # mean headline sentiment at/below which to react risk-off
NEWS_BUY = 0.5      # strong positive sentiment to open on news


def _publish_view(prices):
    """Rebuild agents_view + equity_curves from saved portfolios."""
    from .portfolio import Portfolio
    agents_out, curves = [], {}
    for adef in AGENTS:
        pf = Portfolio.load(adef.id)
        snap = pf.snapshot(prices)
        curves[adef.id] = pf.record_equity(prices)
        agents_out.append({"id": adef.id, "name": adef.name, "style": adef.style,
                           "blurb": adef.blurb, "color": adef.color, "snapshot": snap,
                           "recent_trades": pf.recent_trades(), "actions": []})
    leaderboard = sorted(
        ({"id": a["id"], "name": a["name"], "style": a["style"], "color": a["color"],
          "equity": a["snapshot"]["equity"], "return_pct": a["snapshot"]["total_return_pct"],
          "num_positions": a["snapshot"]["num_positions"]} for a in agents_out),
        key=lambda x: x["return_pct"], reverse=True)
    view = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "agents": agents_out, "leaderboard": leaderboard}
    store.write_json("agents_view", view)
    store.write_json("equity_curves", curves)
    return view


def run_news_reactions(suggestions: dict) -> dict:
    """Off-hours cycle: agents react to fresh news on their names."""
    from . import news
    from .portfolio import Portfolio

    sugg = suggestions.get("suggestions", [])
    by_ticker = {s["ticker"]: s for s in sugg}
    prices = {s["ticker"]: s["price"] for s in sugg}

    trading_agents = [a for a in AGENTS if not a.copy_leader]
    held = {t for adef in trading_agents for t in Portfolio.load(adef.id).positions}
    top = [s["ticker"] for s in sorted(sugg, key=lambda s: s.get("composite", 0),
                                       reverse=True)[:30]]
    candidates = sorted(held | set(top))
    smap = news.sentiment_map(candidates)

    feed = []
    for adef in trading_agents:
        pf = Portfolio.load(adef.id)
        # risk-off: exit holdings with strongly negative news
        for ticker in list(pf.positions.keys()):
            info = smap.get(ticker)
            if info and info["sentiment"] <= NEWS_SELL and ticker in prices:
                shares = pf.positions[ticker].shares
                hl = info["headlines"][0]["title"] if info["headlines"] else "negative news"
                if pf.sell(ticker, shares, prices[ticker],
                           f"{adef.name}: news risk-off ({info['sentiment']:+.2f}) — {hl[:80]}"):
                    feed.append({"agent": adef.name, "color": adef.color, "ticker": ticker,
                                 "side": "SELL", "sentiment": info["sentiment"], "headline": hl})
        # opportunistic: open on strong positive news if the agent also likes it
        equity = pf.equity(prices)
        for ticker in top:
            info = smap.get(ticker); s = by_ticker.get(ticker)
            if not info or not s or ticker in pf.positions:
                continue
            if info["sentiment"] >= NEWS_BUY and adef.score(s) >= adef.buy_threshold \
                    and len(pf.positions) < adef.max_positions and ticker in prices:
                spend = min(equity * adef.max_position_pct * 0.5,
                            max(pf.cash - equity * SMART_CASH_RESERVE_PCT, 0))
                if spend > prices[ticker]:
                    hl = info["headlines"][0]["title"] if info["headlines"] else "positive news"
                    if pf.buy(ticker, spend / prices[ticker], prices[ticker],
                              f"{adef.name}: news momentum ({info['sentiment']:+.2f}) — {hl[:80]}"):
                        feed.append({"agent": adef.name, "color": adef.color, "ticker": ticker,
                                     "side": "BUY", "sentiment": info["sentiment"], "headline": hl})
        pf.save()

    view = _publish_view(prices)
    store.write_json("news_feed", {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reactions": feed[-40:],
        "headlines": [{"ticker": t, **(smap[t]["headlines"][0])}
                      for t in candidates if smap.get(t, {}).get("headlines")][:30],
    })
    return {"reactions": len(feed), "view": view}
