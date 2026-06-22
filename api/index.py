"""Slim, read-only API for Vercel.

Returns precomputed data from the cloud KV store. Imports NO heavy dependencies
(no pandas/yfinance) so the serverless bundle stays small and cold starts fast.
The dashboard itself (public/index.html) is served by Vercel as a static asset;
this function only answers /api/* requests. All compute happens elsewhere and
publishes results to the store; this app only reads them.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI                       # noqa: E402
from fastapi.responses import JSONResponse        # noqa: E402

from app import store                             # noqa: E402

app = FastAPI(title="Terravue (read-only)")


@app.get("/api/status")
def status():
    sugg = store.read_json("suggestions") or {}
    return {
        "llm_enabled": bool(sugg.get("narrative_source") == "claude"),
        "read_only": True,
        "universe_size": len(sugg.get("suggestions", [])),
    }


@app.get("/api/suggestions")
def suggestions():
    return store.read_json("suggestions") or {
        "date": None, "narrative": "No analysis published yet. Run the daily job.",
        "narrative_source": "rules", "suggestions": [],
    }


@app.get("/api/portfolio")
def portfolio():
    return store.read_json("portfolio_view") or {
        "snapshot": {"cash": 0, "positions_value": 0, "equity": 0,
                     "starting_cash": 0, "total_return_pct": 0, "holdings": [],
                     "num_positions": 0, "created": None},
        "recent_trades": [],
    }


@app.get("/api/equity-history")
def equity_history():
    return store.read_json("equity_history") or []


@app.post("/api/run-agent")
def run_agent():
    return JSONResponse(
        {"error": "read_only",
         "message": "This is a read-only deployment. The agent runs automatically "
                    "after each market close and publishes results here."},
        status_code=503,
    )
