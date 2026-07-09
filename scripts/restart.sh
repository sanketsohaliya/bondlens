#!/bin/bash
# Restart the BondLens dev server cleanly.
cd "$(dirname "$0")/.." || exit 1
PID=$(pgrep -f "uvicorn bondlens.app:app" | head -1)
[ -n "$PID" ] && kill "$PID" && sleep 1
setsid nohup .venv/bin/uvicorn bondlens.app:app --host 0.0.0.0 --port 8000 \
  > /tmp/bondlens.log 2>&1 < /dev/null &
sleep 3
curl -s http://localhost:8000/api/nsdl/status > /dev/null \
  && echo "server up" || echo "server FAILED; see /tmp/bondlens.log"
