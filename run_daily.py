#!/usr/bin/env python
"""Daily cycle: refresh suggestions, then run the paper-trading agent.

Run manually:        ./.venv/bin/python run_daily.py
Schedule (cron, weekday 4:30pm ET example):
    30 16 * * 1-5  cd /path/to/stock-advisor && ./.venv/bin/python run_daily.py >> data/cron.log 2>&1
"""
from __future__ import annotations

from app import advisor
from app.agent import run_cycle


def main() -> None:
    print("=" * 60)
    print("Generating daily suggestions...")
    suggestions = advisor.generate_suggestions()
    print(f"  source: {suggestions['narrative_source']}")
    print(f"  {suggestions['narrative']}\n")

    top = suggestions["suggestions"][:5]
    print("Top signals:")
    for s in top:
        print(f"  {s['action']:4} {s['ticker']:6} composite {s['composite']:+.2f} "
              f"@ ${s['price']:.2f}")

    print("\nRunning trading agent...")
    log = run_cycle(suggestions)
    for a in log["actions"]:
        print(f"  - {a}")

    snap = log["snapshot"]
    print(f"\nPortfolio equity: ${snap['equity']:,.2f} "
          f"({snap['total_return_pct']:+.2f}%) | "
          f"cash ${snap['cash']:,.2f} | {snap['num_positions']} positions")
    print("=" * 60)


if __name__ == "__main__":
    main()
