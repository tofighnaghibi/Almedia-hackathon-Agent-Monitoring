#!/usr/bin/env bash
# Review images for video 2 from the last run: the real shop pages, the dashboard at each key moment (from
# outputs/uc2/snapshots/), and the slides → videos/uc2_images/ (+ contact_sheet.png).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
OUT=videos/uc2_images; mkdir -p "$OUT"
PORT="${PORT:-8767}"
python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 & SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
sleep 1
if curl -sf -o /dev/null http://localhost:8080/; then
  PID=$(curl -s "http://localhost:8080/api/products?currencyCode=USD" | python3 -c "import sys,json; d=json.load(sys.stdin); m=[p for p in d if 'Marketplace offer' in p.get('description','')]; print(m[0]['id'] if m else d[0]['id'])")
  node scenes/shots.mjs "$OUT" shop_home=http://localhost:8080/ "shop_product=http://localhost:8080/product/$PID" --wait 3000
fi
S=outputs/uc2/snapshots
pick() { ls "$S" | grep -m1 "$1" || true; }
ARGS=()
for pair in "dash_1_normal_day:day_end_2018-02-15" "dash_2_alarm:_engine" "dash_3_proposal:proposal_card" "dash_4_fixed:_executed" "dash_5_recovered:done"; do
  name="${pair%%:*}"; f="$(pick "${pair#*:}")"
  [ -n "$f" ] && ARGS+=("$name=http://127.0.0.1:$PORT/scenes/uc2_live.html?snap=../$S/$f")
done
node scenes/shots.mjs "$OUT" "${ARGS[@]}" --wait 1200
python3 scenes/build_data.py uc2 >/dev/null
node scenes/record.mjs uc2_pitch --stills 24,62,92 >/dev/null
cp videos/stills/uc2_pitch_t24.png "$OUT/slide_1_problem.png"; cp videos/stills/uc2_pitch_t62.png "$OUT/slide_2_how_it_works.png"
cp videos/stills/uc2_pitch_t92.png "$OUT/slide_4_results.png"
ls "$OUT"/*.png | grep -v contact_sheet | head -12 > /tmp/uc2_imgs.txt
python3 - "$OUT" <<'PY'
import subprocess, sys
from pathlib import Path
out = Path(sys.argv[1]); imgs = [l.strip() for l in open("/tmp/uc2_imgs.txt") if l.strip()]
imgs += [imgs[-1]] * ((-len(imgs)) % 3)
inputs = sum((["-i", p] for p in imgs), [])
n = len(imgs); f = "".join(f"[{i}]scale=640:360[s{i}];" for i in range(n))
rows = [f"{''.join(f'[s{r*3+c}]' for c in range(3))}hstack=3[r{r}]" for r in range(n // 3)]
f += ";".join(rows) + ";" + "".join(f"[r{r}]" for r in range(n // 3)) + f"vstack={n // 3}"
subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", f, str(out / "contact_sheet.png")], check=True)
PY
echo "images in $OUT:"; ls "$OUT"
