#!/usr/bin/env bash
# One-time setup for the live demo (macOS or Linux; needs git and Docker). Safe to run again.
#   - the OpenTelemetry demo (the Astronomy Shop) at the pinned revision → ~/.cache/otel-demo-3.1.0
#   - data folders the shop and the run use → ~/data/mkt/
#   - the 2017 USD/BRL exchange rate the shop's catalog prices need (public data, Banco Central do Brasil)
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
DEMO="$HOME/.cache/otel-demo-3.1.0"
if [ ! -d "$DEMO/.git" ]; then git clone -q --depth 1 https://github.com/open-telemetry/opentelemetry-demo.git "$DEMO"; fi
git -C "$DEMO" fetch -q --depth 1 origin 7a88536cf02f7a6ba5f4ee2b3a2b242a8c56c74c
git -C "$DEMO" -c advice.detachedHead=false checkout -q 7a88536cf02f7a6ba5f4ee2b3a2b242a8c56c74c
mkdir -p "$HOME/data/mkt/astronomy/flags" "$HOME/data/mkt/astronomy/live" "$HOME/data/mkt/raw/bcb"
chmod 777 "$HOME/data/mkt/astronomy/live"
cp "$DEMO/src/flagd/demo.flagd.json" "$HOME/data/mkt/astronomy/flags/demo.flagd.json"
cp "$HERE/deploy/bcb_usd_brl_2017.json" "$HOME/data/mkt/raw/bcb/usd_brl.json"
chmod +x "$HERE"/scripts/*.sh
echo "Setup done. Next: scripts/uc2_shop.sh up --traces && scripts/uc2_watch.sh"
