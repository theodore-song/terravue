#!/bin/bash
# Double-click this file in Finder to launch the Stock Advisor website.
cd "$(dirname "$0")" || exit 1

PORT=8000
URL="http://localhost:$PORT"

# Start the server only if it isn't already running.
if ! curl -s -o /dev/null "$URL/api/status" 2>/dev/null; then
  echo "Starting Stock Advisor server on $URL ..."
  nohup ./.venv/bin/uvicorn app.main:app --port $PORT --host 127.0.0.1 > data/server.log 2>&1 &
  disown 2>/dev/null
  # Wait for it to come up.
  for i in $(seq 1 20); do
    sleep 0.5
    curl -s -o /dev/null "$URL/api/status" 2>/dev/null && break
  done
else
  echo "Server already running."
fi

echo "Opening $URL"
open "$URL"
echo ""
echo "The site is running. You can close this window; the server keeps running."
echo "To stop it later, run:  pkill -f 'uvicorn app.main'"
