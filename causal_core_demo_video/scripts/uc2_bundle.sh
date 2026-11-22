#!/usr/bin/env bash
# Package everything video 2 needs into one folder + zip, so someone else can run it end to end on their own Mac.
#
#   scripts/uc2_bundle.sh                 → dist/uc2_bundle/ and dist/uc2_bundle.zip  (~110 MB, mostly Olist artifacts)
#   scripts/uc2_bundle.sh /some/folder    → /some/folder/uc2_bundle/ (+ .zip next to it)
#
# Contents: the causal core (package + adapters), marketplace-recon's emulator (mkt/, mkt.toml, artifacts/, the collector
# redaction layer), this demo's agent/scenes/scripts/deploy files, the narration, a 2017 USD/BRL series (public BCB data),
# and setup.sh. NOT included (setup.sh fetches them): the OpenTelemetry demo repo, its container images, Python packages.
# Licences: Olist-derived artifacts are CC BY-NC-SA 4.0 (attribution, non-commercial, share-alike); marketplace-recon is a
# private repository. Share the bundle internally only, with its owner's agreement.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CORE="${UC2_CORE:-$(cd "$HERE/../causal_core" && pwd)}"
if [ -n "${UC2_MKT:-}" ]; then MKT="$UC2_MKT"; elif [ -d "$HERE/../../marketplace-recon" ]; then MKT="$(cd "$HERE/../../marketplace-recon" && pwd)"; else MKT="$(cd "$HERE/../marketplace-recon" && pwd)"; fi
DEST_PARENT="${1:-$HERE/dist}"
B="$DEST_PARENT/uc2_bundle"
rm -rf "$B" "$DEST_PARENT/uc2_bundle.zip"
mkdir -p "$B"
RS=(rsync -a --exclude __pycache__ --exclude .DS_Store --exclude "*.pyc")

# 1. the causal core: the package and the adapters it is used through (read-only)
mkdir -p "$B/causal_core"
"${RS[@]}" "$CORE/causal_core" "$CORE/adapters" "$B/causal_core/"
# 2. marketplace-recon: emulator package, config, shared artifacts, the collector redaction layer
mkdir -p "$B/marketplace-recon/deploy/m0"
"${RS[@]}" "$MKT/mkt" "$MKT/mkt.toml" "$MKT/artifacts" "$B/marketplace-recon/"
cp "$MKT/deploy/m0/otelcol-redact.yml" "$B/marketplace-recon/deploy/m0/"
(cd "$MKT" && git log -1 --format='marketplace-recon %H (%ad)' --date=short) > "$B/marketplace-recon/VERSION.txt" 2>/dev/null || true
# 3. the demo
mkdir -p "$B/demo/agent" "$B/demo/scenes" "$B/demo/scripts" "$B/demo/deploy" "$B/demo/data"
cp "$HERE"/agent/{__init__,sandbox,uc2,engine_service}.py "$B/demo/agent/"
cp "$HERE"/scenes/{uc2_live.html,uc2_watch.html,uc2_pitch.html,uc2_prompter.html,uc2_narration.json,capture_live.mjs,record.mjs,shots.mjs,build_data.py,cut_uc2.py,voice_server.py} "$B/demo/scenes/"
cp "$HERE"/scripts/{uc2_shop.sh,uc2_engine.sh,uc2_watch.sh,uc2_record.sh,uc2_voice.sh,uc2_assemble.sh,uc2_images.sh} "$B/demo/scripts/"
cp "$HERE"/deploy/{compose.uc2.yaml,compose.uc2.traces.yaml,otelcol-uc2.yml,otelcol-uc2-traces.yml} "$B/demo/deploy/"
cp "$HERE"/{PITCH.md,SOLUTION.md} "$B/"
[ -f "$HERE/NARRATION_UC2.md" ] && cp "$HERE/NARRATION_UC2.md" "$B/demo/"
cp "$HOME/data/mkt/raw/bcb/usd_brl.json" "$B/demo/data/bcb_usd_brl_2017.json"
(cd "$CORE/.." && git log -1 --format='causal_core %H on %D (%ad)' --date=short) > "$B/causal_core/VERSION.txt" 2>/dev/null || true

# 4. setup for the recipient
cat > "$B/setup.sh" <<'SETUP'
#!/usr/bin/env bash
# One-time setup on a new Mac (Docker Desktop, Python 3.11+, Node 22+, ffmpeg and Google Chrome must be installed).
set -euo pipefail
B="$(cd "$(dirname "$0")" && pwd)"
python3 -m venv "$HOME/.venvs/mkt-emulator"
"$HOME/.venvs/mkt-emulator/bin/python" -m pip install -q --index-url https://pypi.org/simple duckdb polars "pydantic>=2.7" numpy scipy pyarrow fastexcel
DEMO="$HOME/.cache/otel-demo-3.1.0"
if [ ! -d "$DEMO/.git" ]; then
  git clone -q --depth 1 https://github.com/open-telemetry/opentelemetry-demo.git "$DEMO"
fi
git -C "$DEMO" fetch -q --depth 1 origin 7a88536cf02f7a6ba5f4ee2b3a2b242a8c56c74c
git -C "$DEMO" -c advice.detachedHead=false checkout -q 7a88536cf02f7a6ba5f4ee2b3a2b242a8c56c74c
mkdir -p "$HOME/data/mkt/astronomy/flags" "$HOME/data/mkt/astronomy/live" "$HOME/data/mkt/raw/bcb"
chmod 777 "$HOME/data/mkt/astronomy/live"
cp "$DEMO/src/flagd/demo.flagd.json" "$HOME/data/mkt/astronomy/flags/demo.flagd.json"
cp "$B/demo/data/bcb_usd_brl_2017.json" "$HOME/data/mkt/raw/bcb/usd_brl.json"
chmod +x "$B"/demo/scripts/*.sh
echo "Setup done. Next: start Docker Desktop, then:"
echo "  cd $B/demo && scripts/uc2_shop.sh up --traces && scripts/uc2_watch.sh"
SETUP
chmod +x "$B/setup.sh"

cat > "$B/README.md" <<'README'
# Video 2: marketplace watcher on the Astronomy Shop (runnable bundle)

An autonomous agent watches a marketplace that runs through the real OpenTelemetry Astronomy Shop. Shoppers come from
an emulator fitted on real Olist orders. A payment fault is injected; the agent detects it from telemetry, rules out a
demand drop, proposes a fix with an expected range, and applies it only after approval, only to the local shop's flags.

Needs: macOS with Docker Desktop (4 GB+ memory for the shop), Python 3.11+, Node 22+, ffmpeg, Google Chrome, ~6 GB disk.

```bash
./setup.sh                                   # once: Python venv, OpenTelemetry demo @7a88536, data folders
cd demo
scripts/uc2_shop.sh up --traces              # start the shop + Jaeger (first time pulls ~3-5 GB of images)
scripts/uc2_engine.sh start                  # the causal engine as its own local service (watch/record also start it)
scripts/uc2_watch.sh                         # WATCH IT LIVE (no recording): one page, side by side: the agent
                                             #   dashboard | Jaeger traces, failed requests, Monitor rates, the shop
scripts/uc2_record.sh --open                 # optional: run at watchable speed and record the dashboard → videos/uc2_screen.mp4
scripts/uc2_assemble.sh                      # silent cut with subtitles → videos/uc2_silent.mp4 (≤ 3 min)
scripts/uc2_voice.sh                         # plays the silent cut and records your voice → videos/uc2_voice.m4a
scripts/uc2_assemble.sh videos/uc2_voice.m4a # final video with your voice → videos/uc2.mp4
scripts/uc2_shop.sh down                     # stop the shop
```

Watching what happens: the agent dashboard (left: shoppers going through the shop, what the agent sees, its console;
right: each engine step with its measured time, the policy decision, the action card). In Jaeger, pick service
"checkout" or "payment" to see real requests flow through the services; during the fault, failed payments show as
errors. Container logs: `docker logs -f --since 1s payment`. Live resource use: `docker stats`.

What is real and what is simulated: the shop, its services and their telemetry are real (local containers); shoppers
are simulated by the emulator (fitted on 2017 Olist orders); the payment fault is injected by the scenario; approvals
are scripted ("on-call marketplace lead"). Nothing outside this machine is changed.

Licences: Olist-derived files under marketplace-recon/artifacts are CC BY-NC-SA 4.0. marketplace-recon is a private
repository; this copy is for internal use with its owner's agreement.
README
cd "$DEST_PARENT" && zip -qr uc2_bundle.zip uc2_bundle
echo "Bundle: $B  ($(du -sh "$B" | cut -f1))   zip: $DEST_PARENT/uc2_bundle.zip ($(du -sh "$DEST_PARENT/uc2_bundle.zip" | cut -f1))"
