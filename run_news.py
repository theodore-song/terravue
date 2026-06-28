#!/usr/bin/env python
"""Off-hours news-reaction cycle: agents react to fresh headlines on their names.

Uses the most recent daily analysis/prices (last close) as the basis, fetches
current news, and lets each agent trim names with bad news or open on strong
positive news. Runs several times a day (pre-market / after-hours) via GitHub
Actions, so the portfolios react to news even when markets are closed.
"""
from __future__ import annotations

from app import advisor
from app.agent import run_news_reactions


def main() -> None:
    print("=" * 64)
    print("News-reaction cycle...")
    suggestions = advisor.load_suggestions() or advisor.generate_suggestions()
    result = run_news_reactions(suggestions)
    print(f"  {result['reactions']} news-driven trades")
    for a in result["view"]["leaderboard"]:
        print(f"  {a['name']:6} {a['return_pct']:+.2f}%  {a['num_positions']} positions")
    print("=" * 64)


if __name__ == "__main__":
    main()
