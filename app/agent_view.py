"""Helpers for displaying configured agents before their first published run."""
from __future__ import annotations

from datetime import datetime

from .agents import AGENTS
from .config import STARTING_CASH


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

    for agent in agents:
        adef = defs.get(agent.get("id"))
        if not adef:
            continue
        snapshot = agent.get("snapshot") or _empty_snapshot()
        cash_pct = snapshot.get("cash", 0) / max(snapshot.get("equity", 1), 1) * 100
        if not agent.get("strategy_note"):
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

    for adef in AGENTS:
        if adef.id in seen:
            continue
        agents.append({
            "id": adef.id,
            "name": adef.name,
            "style": adef.style,
            "blurb": adef.blurb + " Awaiting its first scheduled trading run.",
            "color": adef.color,
            "snapshot": _empty_snapshot(),
            "recent_trades": [],
            "actions": ["Ready — will start trading on the next scheduled run."],
            "strategy_note": (
                "Ready for the next scheduled run. This agent will publish a daily "
                "competition strategy after it sees the current market signals and "
                "the other agents' trades."
            ),
            "movement_note": (
                "Daily movement note: no open positions yet, so there is nothing to "
                "explain until this agent starts trading."
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
