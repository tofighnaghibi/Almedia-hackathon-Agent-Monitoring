#!/usr/bin/env bash
# Start the Argus API for video recording: no --reload (no restarts mid-take), fixed port 8000
# (the frontend dev server proxies /api there). Loads LLM_* from backend/.env if present.
# Usage: ./run_demo_server.sh            (HOST=0.0.0.0 ./run_demo_server.sh to expose on the LAN)
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/uvicorn ]; then
  UVICORN=.venv/bin/uvicorn
elif command -v uvicorn >/dev/null 2>&1; then
  UVICORN=uvicorn
else
  echo "uvicorn not found: python -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
HOST="${HOST:-127.0.0.1}"
echo "Argus API on http://${HOST}:8000 (Ctrl+C to stop)"
exec "$UVICORN" argus.api:app --host "$HOST" --port 8000 --workers 1 --log-level warning
