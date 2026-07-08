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
from .config import CASH_RESERVE_PCT, STARTING_CASH
from .portfolio import Portfolio


SMART_CASH_RESERVE_PCT = max(CASH_RESERVE_PCT, 0.22)
DRAWDOWN_CASH_RESERVE_PCT = 0.45
STOP_LOSS_PCT = -4.5
WEAK_GAIN_EXIT_PCT = 6.0
MIN_BUY_COMPOSITE = 0.7
MAX_NORMAL_POSITION_PCT = 0.10
MAX_DRAWDOWN_POSITION_PCT = 0.06
PEER_COPY_MAX_TRADES = 3
PEER_COPY_EDGE_PCT = 0.01


def _cash_reserve_pct(portfolio_return_pct: float) -> float:
    return DRAWDOWN_CASH_RESERVE_PCT if portfolio_return_pct < -2.0 else SMART_CASH_RESERVE_PCT


def _position_cap(adef: AgentDef, portfolio_return_pct: float) -> float:
    base = MAX_DRAWDOWN_POSITION_PCT if portfolio_return_pct < -2.0 else MAX_NORMAL_POSITION_PCT
    return min(adef.max_position_pct, base)


def _target_weight(adef: AgentDef, score: float, volatility: float,
                   portfolio_return_pct: float) -> float:
    cap = _position_cap(adef, portfolio_return_pct)
    conviction = min(max(score - adef.buy_threshold, 0) / 3.0, 1.0)
    vol_adj = 1.0 / (1.0 + max(volatility, 0.1) * 3.4)
    return min(conviction * vol_adj * cap, cap)


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

    chg_1d = indicators.get("chg_1d", 0.0) or 0.0

    if score < adef.buy_threshold or composite < MIN_BUY_COMPOSITE:
        return False
    if volatility > 0.85 and s.get("ticker") not in adef.focus_tickers:
        return False
    if ret_1m < 3.0 and s.get("ticker") not in adef.focus_tickers:
        return False
    if chg_1d < -1.0 and s.get("ticker") not in adef.focus_tickers:
        return False
    if ret_1m > 45 and rsi < -0.5:
        return False
    if trend < 0 or macd < -0.2:
        return False

    # Even contrarian entries now need real stabilization; no blind dip-buying.
    if adef.id == "sage":
        return rsi > 0 and trend > 0 and macd > 0
    if s.get("ticker") in adef.focus_tickers:
        return trend > 0.25 and macd > 0
    return trend > 0.4 and (macd > 0.25 or breakout > 0.75)


def _signal_score(s: dict, name: str) -> float:
    for x in s.get("signals", []):
        if x.get("name") == name:
            return x.get("score", 0.0)
    return 0.0


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _daily_strategy_note(adef: AgentDef, snapshot: dict, actions: list[str],
                         rank: int | None = None, leader_name: str | None = None) -> str:
    cash_pct = snapshot["cash"] / max(snapshot["equity"], 1) * 100
    prefix = f"Rank #{rank}. " if rank else ""
    if adef.copy_leader:
        target = leader_name or "the current leader"
        return (
            f"{prefix}Copy {target}'s best risk-adjusted holdings, but skip losing or "
            f"technically weak positions. Keep about {cash_pct:.0f}% cash until the "
            "leader's book proves it is working."
        )
    if snapshot["total_return_pct"] < -2:
        posture = "capital-defense mode"
    elif cash_pct > 30:
        posture = "selective attack mode"
    else:
        posture = "fully engaged mode"
    peer = "Copy same-day buy trades from agents ranked ahead when those names also pass this agent's own filters."
    return (
        f"{prefix}{posture}: use {adef.style.lower()} scoring, keep losers on a short leash, "
        f"cap position sizes, and deploy fresh cash only into confirmed strength. {peer}"
    )


def _sync_to_leader(adef: AgentDef, suggestions: dict, leader: AgentDef | None) -> dict:
    portfolio = Portfolio.load(adef.id)
    actions: list[str] = []
    sugg = suggestions.get("suggestions", [])
    prices = {s["ticker"]: s["price"] for s in sugg}
    by_ticker = {s["ticker"]: s for s in sugg}

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
    leader_is_profitable = leader_equity >= STARTING_CASH
    if leader_equity > 0:
        for ticker, pos in leader_pf.positions.items():
            s = by_ticker.get(ticker)
            price = prices.get(ticker)
            unrealized_pct = (price / pos.avg_cost - 1) * 100 if price and pos.avg_cost else 0
            if ticker in prices and (
                leader_is_profitable
                or (unrealized_pct >= 0 and s and _is_buyable(leader, s, leader.score(s)))
            ):
                target_weights[ticker] = min(
                    pos.market_value(prices[ticker]) / leader_equity,
                    _position_cap(adef, (equity / STARTING_CASH - 1) * 100),
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
    portfolio_return_pct = (equity / STARTING_CASH - 1) * 100
    investable = max(portfolio.cash - equity * _cash_reserve_pct(portfolio_return_pct), 0)
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


def _copy_peer_trades(adef: AgentDef, suggestions: dict, peers: list[dict]) -> dict:
    """Let an agent copy today's strongest peer buys when its own filters agree."""
    portfolio = Portfolio.load(adef.id)
    actions: list[str] = []
    sugg = suggestions.get("suggestions", [])
    by_ticker = {s["ticker"]: s for s in sugg}
    prices = {s["ticker"]: s["price"] for s in sugg}
    equity = portfolio.equity(prices)
    own_return = (equity / STARTING_CASH - 1) * 100

    copied: set[str] = set()
    candidates: list[tuple[float, str, str, dict]] = []
    for peer in sorted(peers, key=lambda a: a["snapshot"]["total_return_pct"], reverse=True):
        if peer["id"] == adef.id:
            continue
        peer_return = peer["snapshot"]["total_return_pct"]
        if peer_return + PEER_COPY_EDGE_PCT < own_return:
            continue
        for trade in peer.get("recent_trades", []):
            if not str(trade.get("timestamp", "")).startswith(_today()):
                continue
            if trade.get("side") != "BUY":
                continue
            ticker = trade.get("ticker")
            s = by_ticker.get(ticker)
            if not s or ticker in copied:
                continue
            score = adef.score(s)
            if _is_buyable(adef, s, score):
                candidates.append((peer_return, peer["name"], ticker, s))
                copied.add(ticker)

    portfolio_return_pct = (equity / STARTING_CASH - 1) * 100
    investable = max(portfolio.cash - equity * _cash_reserve_pct(portfolio_return_pct), 0)
    cap = _position_cap(adef, portfolio_return_pct) * 0.65

    for _, peer_name, ticker, s in candidates[:PEER_COPY_MAX_TRADES]:
        if len(portfolio.positions) >= adef.max_positions and ticker not in portfolio.positions:
            continue
        price = prices[ticker]
        current_val = (portfolio.positions[ticker].market_value(price)
                       if ticker in portfolio.positions else 0.0)
        target_val = cap * equity
        spend = min(max(target_val - current_val, 0.0), investable)
        if spend < max(price, equity * 0.0075):
            continue
        if portfolio.buy(ticker, spend / price, price,
                         f"{adef.name}: copied {peer_name}'s leading buy"):
            actions.append(f"BOUGHT {spend / price:.2f} {ticker} @ ${price:.2f} (copied {peer_name})")
            investable -= spend

    if not actions:
        actions.append("No peer-copy trades — no leading buy passed this agent's filters.")

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
    portfolio_return_pct = (portfolio.equity(prices) / STARTING_CASH - 1) * 100

    # --- 1. Exits: aggressively drop weak holdings and protect capital --------
    for ticker in list(portfolio.positions.keys()):
        s = by_ticker.get(ticker)
        price = prices.get(ticker, portfolio.positions[ticker].avg_cost)
        if s is None:
            continue
        sc = adef.score(s)
        buyable_now = _is_buyable(adef, s, sc)
        unrealized_pct = (price / portfolio.positions[ticker].avg_cost - 1) * 100 \
            if portfolio.positions[ticker].avg_cost else 0.0
        ret_1m = s.get("indicators", {}).get("ret_1m", 0.0) or 0.0
        chg_1d = s.get("indicators", {}).get("chg_1d", 0.0) or 0.0
        should_exit = (
            sc <= adef.sell_threshold
            or unrealized_pct <= STOP_LOSS_PCT
            or (unrealized_pct < 0 and not buyable_now)
            or (portfolio_return_pct < -2.0 and unrealized_pct < 0)
            or (ret_1m < 0 and chg_1d < 0 and sc < adef.buy_threshold)
            or (unrealized_pct >= WEAK_GAIN_EXIT_PCT and sc < 0.2)
        )
        if should_exit:
            shares = portfolio.positions[ticker].shares
            reason = f"{adef.name}: score/risk exit ({sc:+.2f}, {unrealized_pct:+.1f}%)"
            if portfolio.sell(ticker, shares, price,
                              reason):
                actions.append(f"SOLD {shares:.2f} {ticker} @ ${price:.2f} ({sc:+.2f})")

    # --- 1b. Migration/risk cleanup: enforce new smaller books and caps -------
    equity = portfolio.equity(prices)
    portfolio_return_pct = (equity / STARTING_CASH - 1) * 100
    cap = _position_cap(adef, portfolio_return_pct)
    ranked_holdings = []
    for ticker, pos in portfolio.positions.items():
        s = by_ticker.get(ticker, {})
        price = prices.get(ticker, pos.avg_cost)
        ranked_holdings.append((adef.score(s), ticker, price))

    ranked_holdings.sort(reverse=True)
    keep = {ticker for _, ticker, _ in ranked_holdings[:adef.max_positions]}
    for _, ticker, price in ranked_holdings[adef.max_positions:]:
        if ticker in portfolio.positions:
            shares = portfolio.positions[ticker].shares
            if portfolio.sell(ticker, shares, price,
                              f"{adef.name}: reduce to max {adef.max_positions} positions"):
                actions.append(f"SOLD {shares:.2f} {ticker} @ ${price:.2f} (risk cleanup)")

    equity = portfolio.equity(prices)
    for ticker in list(keep):
        if ticker not in portfolio.positions:
            continue
        price = prices.get(ticker, portfolio.positions[ticker].avg_cost)
        current_val = portfolio.positions[ticker].market_value(price)
        max_val = equity * cap
        if current_val > max_val * 1.1:
            shares = (current_val - max_val) / price
            if portfolio.sell(ticker, shares, price,
                              f"{adef.name}: trim position above {cap:.0%} cap"):
                actions.append(f"TRIMMED {shares:.2f} {ticker} @ ${price:.2f} (cap)")

    # --- 2. Entries / adds: top agent-score names ---------------------------
    equity = portfolio.equity(prices)
    portfolio_return_pct = (equity / STARTING_CASH - 1) * 100
    scored = [(adef.score(s), s) for s in sugg]
    candidates = sorted([x for x in scored if _is_buyable(adef, x[1], x[0])],
                        key=lambda x: x[0], reverse=True)

    for sc, s in candidates:
        if len(portfolio.positions) >= adef.max_positions and s["ticker"] not in portfolio.positions:
            continue
        ticker, price = s["ticker"], s["price"]
        vol = s.get("indicators", {}).get("volatility", 0.3)
        target_val = _target_weight(adef, sc, vol, portfolio_return_pct) * equity
        current_val = (portfolio.positions[ticker].market_value(price)
                       if ticker in portfolio.positions else 0.0)
        if ticker in portfolio.positions:
            pos = portfolio.positions[ticker]
            unrealized_pct = (price / pos.avg_cost - 1) * 100 if pos.avg_cost else 0
            if unrealized_pct < 0:
                continue
        gap = target_val - current_val
        investable = max(portfolio.cash - equity * _cash_reserve_pct(portfolio_return_pct), 0)
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

    # Competition round: after everyone makes their own move, each strategy can
    # copy better-ranked agents' same-day BUY trades if its own risk filters agree.
    refreshed = []
    for agent_out in agents_out:
        adef = next(a for a in AGENTS if a.id == agent_out["id"])
        log = _copy_peer_trades(adef, suggestions, agents_out)
        curves[adef.id] = log["curve"]
        agent_out = {
            **agent_out,
            "snapshot": log["snapshot"],
            "recent_trades": log["recent_trades"],
            "actions": agent_out["actions"] + log["actions"],
        }
        refreshed.append(agent_out)
    agents_out = refreshed

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

    ranks = {row["id"]: i + 1 for i, row in enumerate(leaderboard)}
    leader_name = leaderboard[0]["name"] if leaderboard else None
    for agent in agents_out:
        adef = next(a for a in AGENTS if a.id == agent["id"])
        agent["strategy_note"] = _daily_strategy_note(
            adef, agent["snapshot"], agent["actions"],
            ranks.get(agent["id"]), leader_name,
        )

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
                           "recent_trades": pf.recent_trades(), "actions": [],
                           "strategy_note": _daily_strategy_note(adef, snap, [])})
    leaderboard = sorted(
        ({"id": a["id"], "name": a["name"], "style": a["style"], "color": a["color"],
          "equity": a["snapshot"]["equity"], "return_pct": a["snapshot"]["total_return_pct"],
          "num_positions": a["snapshot"]["num_positions"]} for a in agents_out),
        key=lambda x: x["return_pct"], reverse=True)
    ranks = {row["id"]: i + 1 for i, row in enumerate(leaderboard)}
    leader_name = leaderboard[0]["name"] if leaderboard else None
    for agent in agents_out:
        adef = next(a for a in AGENTS if a.id == agent["id"])
        agent["strategy_note"] = _daily_strategy_note(
            adef, agent["snapshot"], agent["actions"],
            ranks.get(agent["id"]), leader_name,
        )
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
