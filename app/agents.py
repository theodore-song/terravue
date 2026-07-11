"""Competing AI trading agents, each with a distinct personality/strategy.

They all read the same daily analysis but rank and select stocks differently, so
they build different portfolios and compete on total return.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    focus_tickers: tuple[str, ...] = field(default_factory=tuple)
    copy_leader: bool = False
    long_term_suggestions: bool = False

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


def _focus_bonus(s: dict, tickers: tuple[str, ...], bonus: float = 0.85) -> float:
    return bonus if s.get("ticker") in tickers else 0.0


BERKSHIRE_BOOK = (
    "AAPL", "AXP", "KO", "BAC", "CVX", "OXY", "GOOGL", "GOOG",
    "CB", "MCO", "KHC", "KR", "VRSN", "COF", "ALLY", "DAL", "LEN", "NUE",
)
PERSHING_BOOK = (
    "BN", "AMZN", "UBER", "MSFT", "GOOG", "GOOGL", "CP", "CMG", "HLT", "QSR",
)
COATUE_BOOK = (
    "NVDA", "TSLA", "META", "AMZN", "MSFT", "GOOGL", "AVGO", "CRWD",
    "SNOW", "DDOG", "NET", "PANW", "UBER",
)
DUQUESNE_BOOK = (
    "NVDA", "MSFT", "AMZN", "META", "AVGO", "GE", "NUE", "TECK",
    "FCX", "XLE", "XLF", "SMH",
)


def _berkshire(s: dict) -> float:
    # Buffett/Greg Abel style: durable compounders, financials/consumer staples,
    # reasonable volatility, and still-positive technical confirmation.
    vol = s.get("indicators", {}).get("volatility", 0.3)
    return (
        0.9 * _sig(s, TREND)
        + 0.55 * _sig(s, MACD)
        + 0.35 * _sig(s, BREAKOUT)
        + 0.65 * s.get("composite", 0.0)
        + _focus_bonus(s, BERKSHIRE_BOOK, 1.0)
        - 0.45 * max(vol - 0.25, 0)
    )


def _pershing(s: dict) -> float:
    # Ackman style: concentrated quality franchises; buy only when signals agree
    # enough to avoid averaging down into deteriorating trends.
    return (
        0.95 * s.get("composite", 0.0)
        + 0.85 * _sig(s, TREND)
        + 0.55 * _sig(s, MACD)
        + _focus_bonus(s, PERSHING_BOOK, 1.05)
    )


def _coatue(s: dict) -> float:
    # Growth/technology specialist: favors software, AI and platform winners,
    # but still requires momentum and breakout confirmation.
    return (
        1.05 * _sig(s, TREND)
        + 0.95 * _sig(s, MACD)
        + 0.75 * _sig(s, BREAKOUT)
        + 0.35 * s.get("composite", 0.0)
        + _focus_bonus(s, COATUE_BOOK, 0.95)
    )


def _duquesne(s: dict) -> float:
    # Druckenmiller style: macro momentum and relative strength, with willingness
    # to rotate between tech, industrials and cyclicals.
    vol = s.get("indicators", {}).get("volatility", 0.3)
    return (
        1.15 * _sig(s, TREND)
        + 0.8 * _sig(s, BREAKOUT)
        + 0.65 * _sig(s, MACD)
        + _focus_bonus(s, DUQUESNE_BOOK, 0.8)
        - 0.25 * max(vol - 0.55, 0)
    )


def _echo(s: dict) -> float:
    # Placeholder scorer; Echo's actual behavior is handled by copying the
    # current leader's portfolio after the strategy agents have traded.
    return s.get("composite", 0.0)


def _longview(s: dict) -> float:
    # Actual selection is handled from suggestions["long_term_suggestions"].
    return s.get("composite", 0.0)


AGENTS: list[AgentDef] = [
    AgentDef("apex", "Apex", "Momentum",
             "Buys only confirmed uptrends and cuts failed momentum quickly.",
             "#3F7CAC", 0.08, 8, 1.2, -0.2, _apex),
    AgentDef("sage", "Sage", "Contrarian value",
             "Waits for oversold names to recover before entering; no blind dip-buying.",
             "#E2F89C", 0.06, 12, 1.0, -0.2, _sage),
    AgentDef("atlas", "Atlas", "Balanced",
             "A conservative blend of trend, momentum and breakout signals. Risk-managed.",
             "#BDC4A7", 0.07, 12, 0.8, -0.15, _atlas),
    AgentDef("nova", "Nova", "Breakout",
             "Hunts only clean breakouts with trend and MACD confirmation.",
             "#95AFBA", 0.08, 8, 1.15, -0.2, _nova),
    AgentDef("orion", "Orion", "Low-volatility quality",
             "Prefers steady, low-volatility names with positive momentum. Defensive and broad.",
             "#D5E1A3", 0.06, 14, 0.55, -0.15, _orion),
    AgentDef("echo", "Echo", "Leader copycat",
             "Copies whoever is leading the scoreboard, matching that agent's holdings after each run.",
             "#F4D35E", 0.14, 20, 0.0, -99.0, _echo, copy_leader=True),
    AgentDef("berkshire", "Berkshire Bot", "Buffett-style quality",
             "Models Berkshire's concentrated durable-compounder playbook with a public-holdings watchlist.",
             "#5BC0BE", 0.08, 10, 1.05, -0.15, _berkshire, BERKSHIRE_BOOK),
    AgentDef("pershing", "Pershing Bot", "Ackman-style concentration",
             "Runs a concentrated quality-franchise strategy inspired by Pershing Square's public book.",
             "#F08A5D", 0.08, 8, 1.05, -0.15, _pershing, PERSHING_BOOK),
    AgentDef("coatue", "Coatue Bot", "Tech growth",
             "Copies the growth-investor mindset: AI, software and platform leaders with strong momentum.",
             "#B83B5E", 0.08, 10, 1.15, -0.2, _coatue, COATUE_BOOK),
    AgentDef("duquesne", "Duquesne Bot", "Macro momentum",
             "Tracks a Druckenmiller-like macro momentum basket across tech, cyclicals and sector ETFs.",
             "#6A2C70", 0.08, 10, 1.1, -0.2, _duquesne, DUQUESNE_BOOK),
    AgentDef("longview", "LongView", "Long-term suggestions",
             "Holds the slower 3-12 month suggestion basket so it can be compared against the trading agents.",
             "#A78BFA", 0.08, 14, 0.0, -99.0, _longview, long_term_suggestions=True),
]


def by_id(aid: str) -> AgentDef | None:
    return next((a for a in AGENTS if a.id == aid), None)
