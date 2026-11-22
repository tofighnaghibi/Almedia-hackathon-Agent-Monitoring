#!/usr/bin/env bash
# Run the video-2 scenario and watch it live, without recording anything.
#
#   scripts/uc2_watch.sh            # watchable speed on the last normal day and the fault day (~8 min)
#   scripts/uc2_watch.sh --fast     # full speed (~6 min)
# Opens one page, side by side: the agent dashboard (left) and, if the shop was started with
# `scripts/uc2_shop.sh up --traces`, Jaeger traces / Monitor rates or the shop itself (right).
# To watch the containers themselves, in another terminal:
#   docker logs -f --since 1s payment       # payment service log: failed charges show up here during the fault
#   docker logs -f --since 1s checkout      # checkout service log
#   docker stats                            # live CPU / memory per container
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
PY="${PY:-$( [ -x "$HERE/../.venv/bin/python" ] && echo "$HERE/../.venv/bin/python" || echo "$HOME/.venvs/mkt-emulator/bin/python" )}"
PORT="${PORT:-8765}"
ARGS=(--paced-days "${PACED_DAYS:-2018-02-15,2018-02-16}" --pace "${PACE:-1}" --event-pause "${EVENT_PAUSE:-7}")
[ "${1:-}" = "--fast" ] && ARGS=()
curl -sf -o /dev/null http://localhost:8080/ || { echo "The shop is not running: scripts/uc2_shop.sh up [--traces]"; exit 1; }
scripts/uc2_engine.sh start                 # the causal engine runs as its own service; the agent calls it
python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 & SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
sleep 1
rm -f outputs/uc2/live.json
# Jaeger rejects a search link without a time window (400 Bad Request), so give it one: the last hour, up to the end of
# the next hour (it covers the whole run). In Jaeger press "Find Traces" to refresh; add error=true under Attributes
# to see only failed requests.
NOW_US=$(( $(date +%s) * 1000000 )); START_US=$(( NOW_US - 3600 * 1000000 )); END_US=$(( NOW_US + 3600 * 1000000 ))
JAEGER="http://localhost:8080/jaeger/ui/search?service=payment&limit=50&lookback=custom&start=$START_US&end=$END_US"
if [ -z "${NO_OPEN:-}" ]; then
  open "http://127.0.0.1:$PORT/scenes/uc2_watch.html"      # side by side: agent dashboard | Jaeger / Monitor / the shop
fi
echo "Side by side: http://127.0.0.1:$PORT/scenes/uc2_watch.html   (agent dashboard alone: http://127.0.0.1:$PORT/scenes/uc2_live.html)"
echo "Shop: http://localhost:8080   Engine service: http://127.0.0.1:${UC2_ENGINE_PORT:-8770}/ (keeps running; scripts/uc2_engine.sh stop)"
echo "Traces (if the shop was started with --traces): $JAEGER"
echo "  In Jaeger press Find Traces to refresh; failed payments only: add error=true under Attributes."
"$PY" -u -m agent.uc2 "${ARGS[@]}"
echo "Done. Outputs in outputs/uc2/. The dashboard keeps showing the final state until you close this script (Ctrl-C)."
[ -t 0 ] && read -r -p "Press Enter to stop the dashboard server. " _ || true
