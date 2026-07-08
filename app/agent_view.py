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
