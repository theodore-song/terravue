"""Paper-trading portfolio: persistent cash + positions, with trade logging."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from . import store
from .config import STARTING_CASH


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Position:
    ticker: str
    shares: float
    avg_cost: float
    peak_price: float | None = None
    profit_taken: bool = False

    def market_value(self, price: float) -> float:
        return self.shares * price

    def unrealized(self, price: float) -> float:
        return (price - self.avg_cost) * self.shares

    def mark_price(self, price: float) -> None:
        self.peak_price = max(self.peak_price or self.avg_cost or price, price)


@dataclass
class Trade:
    timestamp: str
    ticker: str
    side: str          # BUY / SELL
    shares: float
    price: float
    rationale: str


@dataclass
class Portfolio:
    agent_id: str = "default"
    cash: float = STARTING_CASH
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    created: str = field(default_factory=_now)

    # --- persistence (one KV key per agent) ----------------------------------
    @classmethod
    def load(cls, agent_id: str = "default") -> "Portfolio":
        raw = store.read_json(f"pf:{agent_id}")
        if not raw:
            p = cls(agent_id=agent_id)
            p.save()
            return p
        positions = {t: Position(**d) for t, d in raw.get("positions", {}).items()}
        trades = [Trade(**t) for t in raw.get("trades", [])]
        return cls(agent_id=agent_id, cash=raw["cash"], positions=positions,
                   trades=trades, created=raw.get("created", _now()))

    def save(self) -> None:
        store.write_json(f"pf:{self.agent_id}", {
            "cash": self.cash,
            "positions": {t: asdict(p) for t, p in self.positions.items()},
            "trades": [asdict(t) for t in self.trades],
            "created": self.created,
        })

    # --- trading -------------------------------------------------------------
    def buy(self, ticker: str, shares: float, price: float, rationale: str) -> bool:
        cost = shares * price
        if shares <= 0 or cost > self.cash:
            return False
        self.cash -= cost
        if ticker in self.positions:
            pos = self.positions[ticker]
            new_shares = pos.shares + shares
            pos.avg_cost = (pos.avg_cost * pos.shares + cost) / new_shares
            pos.shares = new_shares
            pos.mark_price(price)
        else:
            self.positions[ticker] = Position(ticker, shares, price, peak_price=price)
        self.trades.append(Trade(_now(), ticker, "BUY", shares, price, rationale))
        return True

    def sell(self, ticker: str, shares: float, price: float, rationale: str) -> bool:
        if ticker not in self.positions:
            return False
        pos = self.positions[ticker]
        shares = min(shares, pos.shares)
        if shares <= 0:
            return False
        self.cash += shares * price
        pos.shares -= shares
        if pos.shares <= 1e-6:
            del self.positions[ticker]
        self.trades.append(Trade(_now(), ticker, "SELL", shares, price, rationale))
        return True

    # --- valuation -----------------------------------------------------------
    def positions_value(self, prices: dict[str, float]) -> float:
        return sum(p.market_value(prices.get(t, p.avg_cost))
                   for t, p in self.positions.items())

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.positions_value(prices)

    def snapshot(self, prices: dict[str, float]) -> dict:
        equity = self.equity(prices)
        total_ret = (equity / STARTING_CASH - 1) * 100
        holdings = []
        for t, p in sorted(self.positions.items()):
            price = prices.get(t, p.avg_cost)
            p.mark_price(price)
            mv = p.market_value(price)
            peak = p.peak_price or p.avg_cost or price
            peak_gain = (peak / p.avg_cost - 1) * 100 if p.avg_cost else 0.0
            drawdown_from_peak = (price / peak - 1) * 100 if peak else 0.0
            holdings.append({
                "ticker": t,
                "shares": round(p.shares, 2),
                "avg_cost": round(p.avg_cost, 2),
                "price": round(price, 2),
                "peak_price": round(peak, 2),
                "market_value": round(mv, 2),
                "unrealized": round(p.unrealized(price), 2),
                "unrealized_pct": round((price / p.avg_cost - 1) * 100, 2)
                if p.avg_cost else 0.0,
                "peak_gain_pct": round(peak_gain, 2),
                "drawdown_from_peak_pct": round(drawdown_from_peak, 2),
                "profit_taken": p.profit_taken,
                "weight": round(mv / equity * 100, 1) if equity else 0.0,
            })
        return {
            "cash": round(self.cash, 2),
            "positions_value": round(self.positions_value(prices), 2),
            "equity": round(equity, 2),
            "starting_cash": STARTING_CASH,
            "total_return_pct": round(total_ret, 2),
            "holdings": holdings,
            "num_positions": len(self.positions),
            "created": self.created,
        }

    # --- equity curve (per agent) --------------------------------------------
    def record_equity(self, prices: dict[str, float]) -> list[dict]:
        history = store.read_json(f"eq:{self.agent_id}") or []
        history.append({"date": datetime.now().strftime("%Y-%m-%d"),
                        "equity": round(self.equity(prices), 2)})
        # keep one point per day (latest wins)
        dedup = {h["date"]: h for h in history}
        curve = sorted(dedup.values(), key=lambda h: h["date"])
        store.write_json(f"eq:{self.agent_id}", curve)
        return curve

    def recent_trades(self, n: int = 25) -> list[dict]:
        return [asdict(t) for t in self.trades[-n:][::-1]]
