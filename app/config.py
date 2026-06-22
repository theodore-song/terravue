"""Central configuration for the stock advisor."""
from __future__ import annotations

import os
from pathlib import Path

# python-dotenv is only needed for local dev; it's absent in the slim Vercel build.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
# On Vercel the project dir is read-only; only /tmp is writable.
DATA_DIR = BASE_DIR / "data"
try:
    DATA_DIR.mkdir(exist_ok=True)
except OSError:
    DATA_DIR = Path("/tmp/terravue-data")
    DATA_DIR.mkdir(exist_ok=True)

PORTFOLIO_FILE = DATA_DIR / "portfolio.json"
SUGGESTIONS_FILE = DATA_DIR / "suggestions.json"
HISTORY_FILE = DATA_DIR / "equity_history.json"

# The trading universe (S&P 500 + Nasdaq-100 + popular ETFs) is built
# dynamically in app/universe.py. See get_universe() there to customize it.

# Paper-trading parameters.
STARTING_CASH = 100_000.0
MAX_POSITION_PCT = 0.15          # never put more than 15% of equity in one name
MIN_CONVICTION_TO_BUY = 0.5      # composite signal threshold to open/add
SELL_CONVICTION = -0.5           # composite signal threshold to trim/exit
CASH_RESERVE_PCT = 0.05          # always keep some cash

# LLM (hybrid narrative). Falls back to rule-based text if unset.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")

LLM_ENABLED = bool(ANTHROPIC_API_KEY)
