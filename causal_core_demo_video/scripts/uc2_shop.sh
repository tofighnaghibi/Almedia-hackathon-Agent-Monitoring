#!/usr/bin/env bash
# Start / stop / check the local Astronomy Shop used by video 2 (storefront mode, demo sandbox only).
#   scripts/uc2_shop.sh up | down | status
#   scripts/uc2_shop.sh up --traces      # also Jaeger + Prometheus, to watch requests flow through the services:
#                                        #   http://localhost:8080/jaeger/ui/ (Search, and Monitor for per-service rates)
# Needs: Docker Desktop running, ~/.cache/otel-demo-3.1.0 (opentelemetry-demo @7a88536), ../../marketplace-recon.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
if [ -n "${UC2_MKT:-}" ]; then MKT="$UC2_MKT"
elif [ -d "$HERE/../../marketplace-recon" ]; then MKT="$(cd "$HERE/../../marketplace-recon" && pwd)"
else MKT="$(cd "$HERE/../marketplace-recon" && pwd)"; fi
DEMO="$HOME/.cache/otel-demo-3.1.0"
export DEMO_VERSION=3.1.0 OTEL_COLLECTOR_CONFIG_EXTRAS="$MKT/deploy/m0/otelcol-redact.yml" UC2_DEPLOY="$HERE/deploy"
DC=(docker compose -p m0 -f "$DEMO/compose.yaml" -f "$HERE/deploy/compose.uc2.yaml")
SERVICES=(flagd otel-collector valkey-cart astronomy-db product-catalog cart currency payment shipping quote email ad recommendation checkout image-provider frontend frontend-proxy)
if [ "${2:-}" = "--traces" ]; then
  DC+=(-f "$DEMO/compose.observability.yaml" -f "$HERE/deploy/compose.uc2.traces.yaml"); SERVICES=(jaeger prometheus "${SERVICES[@]}")
fi
case "${1:-status}" in
  up)
    mkdir -p "$HOME/data/mkt/astronomy/flags" "$HOME/data/mkt/astronomy/live" && chmod 777 "$HOME/data/mkt/astronomy/live"
    [ -f "$HOME/data/mkt/astronomy/flags/demo.flagd.json" ] || cp "$DEMO/src/flagd/demo.flagd.json" "$HOME/data/mkt/astronomy/flags/"
    (cd "$DEMO" && "${DC[@]}" up -d --no-deps --quiet-pull "${SERVICES[@]}")
    for i in $(seq 1 60); do curl -sf -o /dev/null http://localhost:8080/ && break; sleep 2; done
    curl -sf -o /dev/null http://localhost:8080/ && echo "shop up: http://localhost:8080" || { echo "shop did not come up"; exit 1; }
    [ "${2:-}" = "--traces" ] && echo "traces (Jaeger): http://localhost:8080/jaeger/ui/  (service: checkout or payment)" || true ;;
  down)
    (cd "$DEMO" && "${DC[@]}" -f "$DEMO/compose.observability.yaml" -f "$HERE/deploy/compose.uc2.traces.yaml" down --remove-orphans) ;;
  status)
    docker ps --filter label=com.docker.compose.project=m0 --format '{{.Names}}\t{{.Status}}' | sort
    curl -s -o /dev/null -w "storefront http %{http_code}\n" http://localhost:8080/ || true ;;
  *) echo "usage: $0 up|down|status"; exit 2 ;;
esac
