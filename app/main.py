"""FastAPI server: serves the dashboard and JSON APIs (full local app)."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from . import advisor, store
from .agent_view import augment_agents_view, augment_equity_curves
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
    return augment_agents_view(store.read_json("agents_view"))


@app.get("/api/stock/{ticker}")
def get_stock(ticker: str):
    from . import stockinfo
    av = store.read_json("agents_view")
    pre = store.read_json("sd:" + ticker.upper().strip())
    if pre:
        pre["holders"] = stockinfo._holders_for(ticker.upper().strip(), av)
        return pre
    return stockinfo.get_stock_detail(ticker, av)  # fallback (works off non-blocked IPs)


@app.get("/api/equity-history")
def equity_history():
    return augment_equity_curves(store.read_json("equity_curves"))


@app.get("/api/news")
def get_news():
    return store.read_json("news_feed") or {"reactions": [], "headlines": []}


@app.post("/api/chat")
async def chat(request: Request):
    from . import chat as stock_chat
    body = await request.json()
    suggestions = advisor.load_suggestions() or {"suggestions": []}
    agents = augment_agents_view(store.read_json("agents_view"))
    news = store.read_json("news_feed") or {"reactions": [], "headlines": []}
    return stock_chat.answer(body.get("message", ""), suggestions, agents, news)


@app.post("/api/run-agent")
def run_agent(refresh: bool = True):
    """Generate fresh suggestions and let all agents trade on them."""
    suggestions = (advisor.generate_suggestions() if refresh
                   else (advisor.load_suggestions() or advisor.generate_suggestions()))
    view = run_competition(suggestions)
    return JSONResponse({"suggestions_count": len(suggestions["suggestions"]),
                         "competition": view})
