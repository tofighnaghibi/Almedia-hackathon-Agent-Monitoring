#!/usr/bin/env bash
# The causal engine as its own local service (agent/engine_service.py), on http://127.0.0.1:8770 (this machine only).
#   scripts/uc2_engine.sh start | stop | status | logs
# The watch and record scripts start it when it is not running. Status page: http://127.0.0.1:8770/
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
PY="${PY:-$HOME/.venvs/mkt-emulator/bin/python}"
PORT="${UC2_ENGINE_PORT:-8770}"
RUNDIR="$HOME/data/mkt/engine-service"; mkdir -p "$RUNDIR"
PIDF="$RUNDIR/service.pid"; LOG="$RUNDIR/service.log"
up() { curl -sf -o /dev/null "http://127.0.0.1:$PORT/health"; }
case "${1:-status}" in
  start)
    if up; then echo "engine service already running: http://127.0.0.1:$PORT/"; exit 0; fi
    nohup "$PY" -u -m agent.engine_service --port "$PORT" >> "$LOG" 2>&1 &
    echo $! > "$PIDF"
    for i in $(seq 1 40); do up && break; sleep 0.25; done
    up && echo "engine service started (pid $(cat "$PIDF")): http://127.0.0.1:$PORT/   log: $LOG" || { echo "engine service did not start; see $LOG"; tail -5 "$LOG"; exit 1; } ;;
  stop)
    if [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null; then echo "engine service stopped"; rm -f "$PIDF"; else echo "engine service not running"; fi ;;
  status)
    up && curl -s "http://127.0.0.1:$PORT/health" && echo || echo "engine service not running" ;;
  logs)
    tail -n 30 -f "$LOG" ;;
  *) echo "usage: $0 start|stop|status|logs"; exit 2 ;;
esac
