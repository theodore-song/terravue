"""FastAPI server: serves the dashboard and JSON APIs (full local app)."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from . import advisor, store
from .config import BASE_DIR, LLM_ENABLED
from .agent import run_competition
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


@app.get("/api/agents")
def get_agents():
    return store.read_json("agents_view") or {"agents": [], "leaderboard": []}


@app.get("/api/equity-history")
def equity_history():
    return store.read_json("equity_curves") or {}


@app.post("/api/run-agent")
def run_agent(refresh: bool = True):
    """Generate fresh suggestions and let all three agents trade on them."""
    suggestions = (advisor.generate_suggestions() if refresh
                   else (advisor.load_suggestions() or advisor.generate_suggestions()))
    view = run_competition(suggestions)
    return JSONResponse({"suggestions_count": len(suggestions["suggestions"]),
                         "competition": view})
