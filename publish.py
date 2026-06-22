#!/usr/bin/env python
"""Push the latest local data to the cloud store (Vercel KV / Upstash).

Run this after the daily job to publish results to the live Vercel site, or any
time you want to seed/refresh the cloud store from local state. Requires the KV
env vars to be set (in .env or the shell); otherwise it just reports no-op.
"""
from __future__ import annotations

from app import store


def main() -> None:
    if not store.USE_KV:
        print("No KV env vars found (KV_REST_API_URL / KV_REST_API_TOKEN). "
              "Nothing to publish. Add them to .env to enable cloud publishing.")
        return

    pushed = []
    for key in ("suggestions", "portfolio", "portfolio_view", "equity_history"):
        # read_json prefers KV, so read straight from the local file copy here.
        path = store._file(key)
        if path.exists():
            import json
            store.write_json(key, json.loads(path.read_text()))
            pushed.append(key)
    print("Published to cloud store:", ", ".join(pushed) or "nothing found locally")


if __name__ == "__main__":
    main()
