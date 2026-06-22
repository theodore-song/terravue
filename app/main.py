"""FastAPI server: serves the dashboard and JSON APIs (full local app)."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from . import advisor, data, store
from .config import BASE_DIR, LLM_ENABLED
from .agent import run_cycle
from .portfolio import Portfolio
from .universe import get_universe

app = FastAPI(title="Stock Advisor + Paper Trading Agent")

PUBLIC_DIR = BASE_DIR / "public"


@app.get("/")
def index():
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/api/status")
def status():
    universe = get_universe()
    return {"llm_enabled": LLM_ENABLED, "universe_size": len(universe),
            "read_only": False}


@app.get("/api/suggestions")
def get_suggestions(refresh: bool = False):
    """Today's analysis. Pass ?refresh=true to recompute from live data."""
    result = None if refresh else advisor.load_suggestions()
    if result is None:
        result = advisor.generate_suggestions()
    return result


@app.get("/api/portfolio")
def get_portfolio():
    p = Portfolio.load()
    prices = {}
    for t in p.positions:
        price = data.latest_price(t)
        if price:
            prices[t] = price
    return {
        "snapshot": p.snapshot(prices),
        "recent_trades": p.recent_trades(),
    }


@app.get("/api/equity-history")
def equity_history():
    return store.read_json("equity_history") or []


@app.post("/api/run-agent")
def run_agent(refresh: bool = True):
    """Generate fresh suggestions and let the agent trade on them."""
    suggestions = (advisor.generate_suggestions() if refresh
                   else (advisor.load_suggestions() or advisor.generate_suggestions()))
    log = run_cycle(suggestions)
    return JSONResponse({"suggestions": suggestions, "agent": log})
