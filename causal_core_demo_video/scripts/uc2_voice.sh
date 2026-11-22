#!/usr/bin/env bash
# Record your voice-over in sync with the silent cut: opens a prompter page (video + the narration lines in time) and
# saves the recording to videos/uc2_voice.webm (+ the measured start offset). Then: scripts/uc2_assemble.sh videos/uc2_voice.webm
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
[ -f videos/uc2_silent.mp4 ] || { echo "Run scripts/uc2_assemble.sh first (it makes videos/uc2_silent.mp4)"; exit 1; }
PORT="${PORT:-8766}"
python3 scenes/voice_server.py "$PORT" & SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
sleep 1
open "http://127.0.0.1:$PORT/scenes/uc2_prompter.html"
echo "Prompter open in your browser (allow the microphone for 127.0.0.1). Press Ctrl-C here when you are done."
wait $SRV
