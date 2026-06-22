"""Technical-analysis strategies that turn price history into trade signals.

Each strategy returns a score in roughly [-2, +2]:
    positive = bullish, negative = bearish, magnitude = conviction.
The advisor blends these into a single composite signal per ticker.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# --- indicator helpers -------------------------------------------------------

def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


# --- strategy results --------------------------------------------------------

@dataclass
class StrategySignal:
    name: str
    score: float          # -2..+2
    rationale: str


@dataclass
class TickerAnalysis:
    ticker: str
    price: float
    composite: float
    action: str           # BUY / HOLD / SELL
    signals: list[StrategySignal] = field(default_factory=list)
    indicators: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": round(self.price, 2),
            "composite": round(self.composite, 2),
            "action": self.action,
            "signals": [
                {"name": s.name, "score": round(s.score, 2),
                 "rationale": s.rationale}
                for s in self.signals
            ],
            "indicators": {k: (round(v, 2) if isinstance(v, float) else v)
                           for k, v in self.indicators.items()},
        }


# --- individual strategies ---------------------------------------------------

def trend_following(close: pd.Series) -> StrategySignal:
    """Golden/death cross of 50- vs 200-day moving averages (scaled by gap)."""
    s50, s200 = sma(close, 50), sma(close, 200)
    if s50.isna().iloc[-1] or s200.isna().iloc[-1]:
        s50, s200 = sma(close, 20), sma(close, 50)  # short-history fallback
    fast, slow = s50.iloc[-1], s200.iloc[-1]
    if np.isnan(fast) or np.isnan(slow) or slow == 0:
        return StrategySignal("Trend (MA cross)", 0.0, "insufficient history")
    gap = (fast - slow) / slow
    score = float(np.clip(gap * 40, -2, 2))
    direction = "above" if fast > slow else "below"
    return StrategySignal(
        "Trend (MA cross)", score,
        f"Fast MA is {abs(gap)*100:.1f}% {direction} slow MA",
    )


def momentum_rsi(close: pd.Series) -> StrategySignal:
    """RSI mean-reversion: oversold is bullish, overbought is bearish."""
    r = rsi(close).iloc[-1]
    if np.isnan(r):
        return StrategySignal("Momentum (RSI)", 0.0, "insufficient history")
    if r < 30:
        score = float(np.clip((30 - r) / 15, 0, 2))
        note = f"oversold (RSI {r:.0f})"
    elif r > 70:
        score = -float(np.clip((r - 70) / 15, 0, 2))
        note = f"overbought (RSI {r:.0f})"
    else:
        score = (50 - r) / 40
        note = f"neutral (RSI {r:.0f})"
    return StrategySignal("Momentum (RSI)", score, note)


def macd_signal(close: pd.Series) -> StrategySignal:
    macd_line, signal_line, hist = macd(close)
    h, prev = hist.iloc[-1], hist.iloc[-2] if len(hist) > 1 else 0
    if np.isnan(h):
        return StrategySignal("MACD", 0.0, "insufficient history")
    rising = h > prev
    base = np.sign(h) * min(abs(h) / (close.iloc[-1] * 0.01), 2)
    score = float(np.clip(base + (0.3 if rising else -0.3) * np.sign(h), -2, 2))
    state = "bullish" if h > 0 else "bearish"
    trend = "strengthening" if rising else "weakening"
    return StrategySignal("MACD", score, f"{state} & {trend}")


def breakout(close: pd.Series, window: int = 20) -> StrategySignal:
    """Price relative to its recent high/low channel."""
    if len(close) < window:
        return StrategySignal("Breakout (52w channel)", 0.0, "insufficient history")
    hi = close.rolling(window).max().iloc[-1]
    lo = close.rolling(window).min().iloc[-1]
    last = close.iloc[-1]
    if hi == lo:
        return StrategySignal("Breakout (52w channel)", 0.0, "flat channel")
    pos = (last - lo) / (hi - lo)            # 0..1 within channel
    score = float(np.clip((pos - 0.5) * 4, -2, 2))
    return StrategySignal(
        "Breakout (52w channel)", score,
        f"{pos*100:.0f}% up its {window}-day range",
    )


# --- volatility (used for sizing, not direction) -----------------------------

def annualized_volatility(close: pd.Series, window: int = 30) -> float:
    rets = close.pct_change().dropna()
    if len(rets) < 2:
        return 0.0
    return float(rets.tail(window).std() * np.sqrt(252))


# --- orchestration -----------------------------------------------------------

STRATEGY_WEIGHTS = {
    "Trend (MA cross)": 1.0,
    "Momentum (RSI)": 0.8,
    "MACD": 1.0,
    "Breakout (52w channel)": 0.7,
}


def analyze(ticker: str, df: pd.DataFrame) -> TickerAnalysis | None:
    """Run all strategies on a price frame and produce a blended verdict."""
    if df.empty or "Close" not in df:
        return None
    close = df["Close"].dropna()
    if len(close) < 30:
        return None

    sigs = [
        trend_following(close),
        momentum_rsi(close),
        macd_signal(close),
        breakout(close),
    ]
    total_w = sum(STRATEGY_WEIGHTS.get(s.name, 1.0) for s in sigs)
    composite = sum(s.score * STRATEGY_WEIGHTS.get(s.name, 1.0)
                    for s in sigs) / total_w

    if composite >= 0.5:
        action = "BUY"
    elif composite <= -0.5:
        action = "SELL"
    else:
        action = "HOLD"

    vol = annualized_volatility(close)
    return TickerAnalysis(
        ticker=ticker,
        price=float(close.iloc[-1]),
        composite=float(composite),
        action=action,
        signals=sigs,
        indicators={
            "rsi14": float(rsi(close).iloc[-1]),
            "volatility": vol,
            "chg_1d": float(close.pct_change().iloc[-1] * 100)
            if len(close) > 1 else 0.0,
            "ret_1m": float(close.pct_change(21).iloc[-1] * 100)
            if len(close) > 21 else 0.0,
        },
    )
