#!/usr/bin/env python
"""Daily cycle: refresh suggestions, then run the three competing agents.

Run manually:        ./.venv/bin/python run_daily.py
Schedule (cron, weekday 4:30pm ET example):
    30 16 * * 1-5  cd /path/to/stock-advisor && ./.venv/bin/python run_daily.py >> data/cron.log 2>&1
"""
from __future__ import annotations

from app import advisor
from app.agent import run_competition


def main() -> None:
    print("=" * 64)
    print("Generating daily suggestions...")
    suggestions = advisor.generate_suggestions()
    print(f"  source: {suggestions['narrative_source']} | "
          f"{len(suggestions['suggestions'])} names analyzed\n")

    print("Running agent competition...")
    view = run_competition(suggestions)

    print("\nLeaderboard:")
    for i, a in enumerate(view["leaderboard"], 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, "  ")
        print(f"  {medal} {a['name']:6} ({a['style']:16}) "
              f"${a['equity']:>12,.2f}  {a['return_pct']:+.2f}%  "
              f"{a['num_positions']} positions")
    print("=" * 64)


if __name__ == "__main__":
    main()
