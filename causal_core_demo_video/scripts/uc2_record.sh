#!/usr/bin/env bash
# Video 2: run the scenario against the real shop and record the live dashboard while it happens.
#
#   scripts/uc2_shop.sh up            # once: start the local Astronomy Shop (Docker Desktop must be running)
#   scripts/uc2_record.sh             # run + record → videos/uc2_screen.mp4, outputs/uc2/ (timeline.json for editing)
#   scripts/uc2_record.sh --open      # also open the dashboard in your browser to watch it live
#   scripts/uc2_record.sh --mic 0     # also record your voice live from audio device 0 → videos/uc2_voice_live.m4a
#                                     #   (list devices: ffmpeg -f avfoundation -list_devices true -i "")
# Pacing (what the video shows at watchable speed): the last normal day and the fault day. Override with
#   PACED_DAYS=2018-02-15,2018-02-16 PACE=1 EVENT_PAUSE=7 scripts/uc2_record.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
PY="${PY:-$HOME/.venvs/mkt-emulator/bin/python}"
PORT="${PORT:-8765}"
PACED_DAYS="${PACED_DAYS:-2018-02-15,2018-02-16}"; PACE="${PACE:-1}"; EVENT_PAUSE="${EVENT_PAUSE:-7}"
OPEN=0; MIC=""
while [ $# -gt 0 ]; do case "$1" in --open) OPEN=1; shift ;; --mic) MIC="$2"; shift 2 ;; *) echo "unknown option $1"; exit 2 ;; esac; done

curl -sf -o /dev/null http://localhost:8080/ || { echo "The shop is not running: scripts/uc2_shop.sh up"; exit 1; }
scripts/uc2_engine.sh start                 # the causal engine runs as its own service; the agent calls it
[ -x "$PY" ] || { echo "Missing venv: python3 -m venv ~/.venvs/mkt-emulator && ~/.venvs/mkt-emulator/bin/pip install duckdb polars 'pydantic>=2.7' numpy scipy pyarrow fastexcel"; exit 1; }
mkdir -p videos outputs/uc2
STOP="$(mktemp -u /tmp/uc2-stop.XXXX)"
cleanup() { touch "$STOP"; [ -n "${SRV:-}" ] && kill "$SRV" 2>/dev/null || true; [ -n "${MICPID:-}" ] && kill -INT "$MICPID" 2>/dev/null || true; }
trap cleanup EXIT

python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 & SRV=$!
sleep 1
URL="http://127.0.0.1:$PORT/scenes/uc2_live.html"
echo "Dashboard: $URL"
[ "$OPEN" = 1 ] && open "$URL"
rm -f outputs/uc2/live.json
node scenes/capture_live.mjs "$URL" videos/uc2_screen.mp4 --fps 15 --stop-file "$STOP" --max-s 1500 & CAP=$!
if [ -n "$MIC" ]; then ffmpeg -y -loglevel error -f avfoundation -i ":$MIC" -c:a aac -b:a 160k videos/uc2_voice_live.m4a & MICPID=$!; fi
sleep 3
"$PY" -u -m agent.uc2 --paced-days "$PACED_DAYS" --pace "$PACE" --event-pause "$EVENT_PAUSE" | tee outputs/uc2_console.txt
sleep 3
touch "$STOP"; wait "$CAP" || true
[ -n "$MIC" ] && { kill -INT "$MICPID" 2>/dev/null || true; wait "$MICPID" 2>/dev/null || true; }
echo
echo "Recorded: videos/uc2_screen.mp4 (dashboard, 1920x1080)  ·  run outputs: outputs/uc2/  ·  edit points: outputs/uc2/timeline.json"
[ -n "$MIC" ] && echo "Voice: videos/uc2_voice_live.m4a"
echo "Next: record your voice over NARRATION_UC2.md (or use the live take), then: scripts/uc2_assemble.sh videos/<your_voice>.m4a"
