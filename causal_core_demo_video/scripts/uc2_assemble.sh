#!/usr/bin/env bash
# Assemble video 2 (≤ 3:00): pitch (0:00-0:32) + how it works (0:32-1:36) + the recorded live run, cut to 70 s
# (1:36-2:46) + close (2:46-3:00).
#
#   scripts/uc2_assemble.sh                        → videos/uc2_silent.mp4 (+ subtitles track; for the voice step)
#   scripts/uc2_assemble.sh videos/uc2_voice.webm  → videos/uc2.mp4 (your voice; .webm/.m4a/.wav all work)
# Needs: outputs/uc2/ and videos/uc2_screen.mp4 from scripts/uc2_record.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
VOICE="${1:-}"
[ -f videos/uc2_screen.mp4 ] || { echo "Missing videos/uc2_screen.mp4: run scripts/uc2_record.sh first"; exit 1; }
python3 scenes/build_data.py uc2
node scenes/record.mjs uc2_pitch                         # → videos/uc2_pitch.mp4 (110 s of slides)
python3 scenes/cut_uc2.py                               # → videos/uc2_demo_cut.mp4, narration timings, NARRATION_UC2.md
ffmpeg -y -loglevel error -i videos/uc2_pitch.mp4 -i videos/uc2_demo_cut.mp4 -i videos/uc2_narration.srt -filter_complex \
  "[0:v]trim=0:96,setpts=PTS-STARTPTS[a];[1:v]setpts=PTS-STARTPTS[b];[0:v]trim=96:110,setpts=PTS-STARTPTS[c];[a][b][c]concat=n=3:v=1:a=0[v]" \
  -map "[v]" -map 2:s -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -c:s mov_text -metadata:s:s:0 language=eng \
  -movflags +faststart videos/uc2_silent.mp4
echo "wrote videos/uc2_silent.mp4 ($(ffprobe -v error -show_entries format=duration -of csv=p=0 videos/uc2_silent.mp4 | cut -d. -f1) s)"
if [ -n "$VOICE" ]; then
  OFF="0"; [ -f "${VOICE%.*}.offset" ] && OFF="$(cat "${VOICE%.*}.offset")"      # prompter: video started OFF s after recording
  ffmpeg -y -loglevel error -i videos/uc2_silent.mp4 -ss "$OFF" -i "$VOICE" -map 0:v -map 1:a -map 0:s? -c:v copy -c:s copy \
    -c:a aac -b:a 192k -af "apad" -t "$(ffprobe -v error -show_entries format=duration -of csv=p=0 videos/uc2_silent.mp4)" \
    -movflags +faststart videos/uc2.mp4
  echo "wrote videos/uc2.mp4 with your voice (voice offset ${OFF}s)"
fi
