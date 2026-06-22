# 📈 Stock Advisor & Paper-Trading Agent

A self-contained web app that:

1. **Analyzes the market every day** — pulls real prices from Yahoo Finance and
   runs four technical strategies (MA trend, RSI momentum, MACD, breakout) over a
   watchlist, blending them into a single conviction score per ticker.
2. **Gives daily suggestions** — ranked BUY / HOLD / SELL calls plus a written
   market note. The note is rule-based by default, or written by **Claude** when
   an API key is present (the "hybrid" brain).
3. **Runs an autonomous AI agent** — a paper-trading portfolio that starts with
   $100k, consumes the daily suggestions, and trades them with risk-aware,
   volatility-scaled position sizing. Its equity curve, holdings, and full trade
   log are tracked over time.

> ⚠️ Educational simulation only, on delayed data. **Not financial advice.**
> No real money and no real orders are ever placed.

---

## Quick start

```bash
cd stock-advisor

# 1. (already done if you saw it run) create venv + install deps
python3 -m venv .venv
./.venv/bin/pip install -r requirements-local.txt   # full local deps

# 2. (optional) enable Claude-written narratives
cp .env.example .env          # then paste your ANTHROPIC_API_KEY

# 3. start the website
./.venv/bin/uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000>. Click **Run agent now** to generate fresh
suggestions and let the agent trade on them. Click any table column header to
sort by it (click again to reverse the direction).

### Easiest launch

Double-click **`start.command`** in Finder — it starts the server (if not
already running) and opens the site in your browser.

## Daily automation (already scheduled)

A cron job is installed to run the full cycle automatically every weekday at
**16:15 local time** (just after the 4:00pm ET market close):

```cron
15 16 * * 1-5 /Users/theodore/Downloads/stock-advisor/run_daily.sh >> .../data/cron.log 2>&1
```

Each run refreshes suggestions, lets the agent trade, and appends to the equity
curve. Output is logged to `data/cron.log`.

- See it:        `crontab -l`
- Remove it:     `crontab -e` (delete the `run_daily.sh` line)
- Run by hand:   `./.venv/bin/python run_daily.py`

> macOS note: cron only fires if the Mac is **awake** at 16:15. If yours is
> usually asleep then, ask to switch the schedule to `launchd`, which catches up
> on the next wake.

## Deploying to Vercel (live site + free subdomain)

Vercel is serverless with no persistent disk, so the deployment is **read-only**:
Vercel serves the dashboard and reads precomputed data from a cloud key-value
store. Your local daily job does the heavy compute and **publishes** to that store.

```
  local machine (cron)                    Vercel (free)
  ┌────────────────────┐   writes   ┌──────────────────┐   reads
  │ run_daily.py        ├──────────►│  Vercel KV /      │◄────────  visitors
  │ (yfinance + pandas) │           │  Upstash Redis    │           (read-only)
  └────────────────────┘           └──────────────────┘
```

**One-time setup**

1. Push this folder to a GitHub repo (already git-initialized — just add a remote
   and `git push`).
2. On <https://vercel.com> → **Add New Project** → import the repo. Framework
   preset: **Other**. Deploy. You get `https://<project>.vercel.app`.
3. In the project: **Storage → Create Database → KV (Upstash Redis)** and connect
   it. Vercel auto-injects `KV_REST_API_URL` and `KV_REST_API_TOKEN`. **Redeploy.**
4. Point your local job at the same store so it publishes there: open the KV
   store's **`.env.local`** tab in Vercel, copy `KV_REST_API_URL` and
   `KV_REST_API_TOKEN` into your local **`.env`** file.
5. Seed it once: `./.venv/bin/python run_daily.py` (writes locally **and** to KV).
   Refresh your `.vercel.app` URL — the live site now shows your data.

From then on the weekday cron publishes automatically. To re-push manually:
`./.venv/bin/python publish.py`.

The live site hides **Run agent** (it runs on schedule and publishes); everything
else — tabs, screener, charts — works fully.

| Vercel-specific file | Role |
|------|------|
| `api/index.py` | Slim read-only FastAPI function (no pandas) Vercel runs |
| `app/store.py` | KV/Upstash-or-local-file JSON storage used by everything |
| `vercel.json` | Serves `public/` statically; routes `/api/*` to the function |
| `requirements.txt` | Slim deps for Vercel (`requirements-local.txt` = full local) |

## How it works

| File | Role |
|------|------|
| `app/universe.py` | Builds the ~550-name universe (S&P 500 + Nasdaq-100 + ETFs) |
| `app/data.py` | Yahoo Finance fetch (batched) + 6h disk cache |
| `app/strategies.py` | The four strategies + composite signal blending |
| `app/advisor.py` | Ranks the watchlist, writes the daily note (rules or Claude) |
| `app/portfolio.py` | Persistent paper portfolio: cash, positions, trades, equity curve |
| `app/agent.py` | The trading agent — exit/entry rules + position sizing |
| `app/main.py` | FastAPI server + JSON APIs |
| `public/index.html` | Tabbed dashboard: Overview · Markets · Signals · Portfolio · Trades |
| `run_daily.py` | One-shot daily cycle for cron |

### Agent logic (in `app/config.py`, tweakable)

- Exit a holding when its signal turns bearish (`SELL_CONVICTION`).
- Enter/add the highest-conviction names above `MIN_CONVICTION_TO_BUY`.
- Size by conviction **and** inverse volatility, capped at `MAX_POSITION_PCT`
  (15%) per name, always keeping a `CASH_RESERVE_PCT` cash buffer.

The trading universe (~550 names) is built in `app/universe.py`: it pulls the
**S&P 500** and **Nasdaq-100** constituents live from Wikipedia (cached for a
week, with an offline fallback) and adds a curated list of **popular ETFs**.
Edit `POPULAR_ETFS` or `get_universe()` there to customize it. Data is fetched
in batches (~28s for the full universe) and cached for 6 hours.

## APIs

- `GET /api/suggestions?refresh=true` — today's ranked analysis + narrative
- `GET /api/portfolio` — snapshot + recent trades
- `GET /api/equity-history` — equity curve points
- `POST /api/run-agent` — refresh suggestions and run one trading cycle
