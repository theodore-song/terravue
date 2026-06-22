#!/bin/bash
# Wrapper used by cron to run the daily cycle after market close.
# Uses absolute paths so it works in cron's minimal environment.
cd /Users/theodore/Downloads/stock-advisor || exit 1
echo "===== $(date) ====="
./.venv/bin/python run_daily.py
echo ""
