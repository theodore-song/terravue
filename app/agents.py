"""Three competing AI trading agents, each with a distinct personality/strategy.

They all read the same daily analysis but rank and select stocks differently, so
they build different portfolios and compete on total return.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# Signal names produced by strategies.py
TREND = "Trend (MA cross)"
RSI = "Momentum (RSI)"
MACD = "MACD"
BREAKOUT = "Breakout (52w channel)"


def _sig(s: dict, name: str) -> float:
    for x in s.get("signals", []):
        if x["name"] == name:
            return x["score"]
    return 0.0


@dataclass
class AgentDef:
    id: str
    name: str
    style: str
    blurb: str
    color: str
    max_position_pct: float   # max weight of one holding
    max_positions: int        # max number of holdings
    buy_threshold: float      # min agent-score to open/add
    sell_threshold: float     # agent-score at/below which to exit
    scorer: Callable[[dict], float]

    def score(self, s: dict) -> float:
        return self.scorer(s)


def _apex(s: dict) -> float:
    # Momentum: ride the strongest trend, MACD and breakouts; ignore mean-reversion.
    return 1.2 * _sig(s, TREND) + 1.1 * _sig(s, MACD) + 0.9 * _sig(s, BREAKOUT)


def _sage(s: dict) -> float:
    # Contrarian value: lean into oversold names (RSI signal is bullish when oversold).
    return 1.6 * _sig(s, RSI) + 0.5 * _sig(s, MACD) + 0.3 * _sig(s, TREND)


def _atlas(s: dict) -> float:
    # Balanced: the blended composite score.
    return s.get("composite", 0.0)


def _nova(s: dict) -> float:
    # Breakout specialist: rides names pushing to new highs out of their range.
    return 1.6 * _sig(s, BREAKOUT) + 0.8 * _sig(s, TREND) + 0.4 * _sig(s, MACD)


def _orion(s: dict) -> float:
    # Low-volatility quality: positive composite, damped hard by volatility.
    vol = s.get("indicators", {}).get("volatility", 0.3)
    return s.get("composite", 0.0) / (1.0 + max(vol, 0.05) * 1.5)


AGENTS: list[AgentDef] = [
    AgentDef("apex", "Apex", "Momentum",
             "Chases the strongest trends and breakouts. Aggressive and concentrated.",
             "#3F7CAC", 0.20, 10, 0.6, -0.4, _apex),
    AgentDef("sage", "Sage", "Contrarian value",
             "Buys oversold, beaten-down names betting on the bounce. Patient and diversified.",
             "#E2F89C", 0.12, 18, 0.5, -0.7, _sage),
    AgentDef("atlas", "Atlas", "Balanced",
             "A diversified blend of trend, momentum and breakout signals. Risk-managed.",
             "#BDC4A7", 0.15, 15, 0.5, -0.5, _atlas),
    AgentDef("nova", "Nova", "Breakout",
             "Hunts stocks breaking out to new highs out of their trading range. Trend-hungry.",
             "#95AFBA", 0.18, 12, 0.6, -0.4, _nova),
    AgentDef("orion", "Orion", "Low-volatility quality",
             "Prefers steady, low-volatility names with positive momentum. Defensive and broad.",
             "#D5E1A3", 0.10, 20, 0.4, -0.5, _orion),
]


def by_id(aid: str) -> AgentDef | None:
    return next((a for a in AGENTS if a.id == aid), None)
