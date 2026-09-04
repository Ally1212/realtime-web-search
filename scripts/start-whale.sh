#!/usr/bin/env bash
# Start the services required for Whale to dispatch collection tasks.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop is not running. Start Docker Desktop, then run this command again." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "Missing .env. Copy .env.example to .env and configure Whale credentials first." >&2
  exit 1
fi

required_vars=(WHALE_ENABLED WHALE_COLLECTOR_API_KEY WHALE_DATASET_ID)
for name in "${required_vars[@]}"; do
  value="$(awk -F= -v key="$name" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' .env)"
  if [[ -z "$value" ]]; then
    echo "Missing $name in .env." >&2
    exit 1
  fi
done

if [[ "$(awk -F= '$1 == "WHALE_ENABLED" { print $2; exit }' .env)" != "true" ]]; then
  echo "Set WHALE_ENABLED=true in .env before starting." >&2
  exit 1
fi

collector_command="$(awk -F= '$1 == "COLLECTOR_COMMAND" { print $2; exit }' .env)"
if [[ "$collector_command" == "continuous-whale" ]]; then
  if [[ "$(awk -F= '$1 == "CONTINUOUS_WHALE_ENABLED" { print $2; exit }' .env)" != "true" ]]; then
    echo "Set CONTINUOUS_WHALE_ENABLED=true in .env before starting continuous-whale." >&2
    exit 1
  fi
  if [[ -z "$(awk -F= '$1 == "CONTINUOUS_AI_KEYWORDS" { sub(/^[^=]*=/, ""); print; exit }' .env)" ]]; then
    echo "Missing CONTINUOUS_AI_KEYWORDS in .env." >&2
    exit 1
  fi
fi

docker compose up -d --build --wait --remove-orphans \
  postgres opensearch searxng valkey web collector

curl -fsS --max-time 10 http://127.0.0.1:8091/healthz >/dev/null
if [[ "$(docker compose ps --status running -q collector)" == "" ]]; then
  echo "Collector did not remain running. Check: docker compose logs collector" >&2
  exit 1
fi

echo "Ready: dashboard http://127.0.0.1:8091"
echo "Collector is online and can now receive matching platform tasks."
