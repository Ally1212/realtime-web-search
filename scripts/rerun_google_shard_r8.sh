#!/usr/bin/env bash
# Recreate the lost r8 experiment: 4 shard / 1 hour, per-shard 2 rps, yield plan,
# private proxies, Whale enabled. Prereqs: private proxy egress reachable
# (verify: at least some endpoints in state/proxies/private.json accept TCP).
set -euo pipefail
cd "$(dirname "$0")/.."
KEY=r8-rerun-$(date +%Y%m%d)

# 1. Bring up openserp and proxy-relay. Docker Hub may be unreachable; if the
#    proxy-relay build fails on the python:3.14-slim pull, build from the local
#    python:3.12-slim instead and tag it for compose.
if ! docker compose up -d openserp proxy-relay; then
  sed '1s|.*|FROM python:3.12-slim|' Dockerfile.proxy-relay > /tmp/Dockerfile.proxy-relay.local
  docker build -f /tmp/Dockerfile.proxy-relay.local -t realtime-web-search-proxy-relay:latest .
  docker compose up -d openserp proxy-relay
fi

# 2. Experiment environment: .env plus compose-network overrides.
grep -E '^[A-Z_]+=' .env \
  | grep -vE '^(OPENSERP_|COLLECTOR_COMMAND|CONTINUOUS_|WHALE_SUPPORTED_TASK_TYPES|WHALE_MAX_CONCURRENCY|WHALE_CLAIM_LIMIT|WHALE_HEARTBEAT_SECONDS|WHALE_INGEST_BATCH_SIZE|ADAPTIVE_|PERSISTENT_|OUTBOX_|STATIC_PROXIES)' \
  > /tmp/$KEY.env
cat >> /tmp/$KEY.env <<'ENVEOF'
OPENSERP_URL=http://openserp:7000
OPENSERP_PROXY_RELAY_HOST=proxy-relay
DATABASE_URL=postgresql://realtime:realtime-local@postgres:5432/realtime
VALKEY_URL=redis://valkey:6379/0
WHALE_ENABLED=true
GOOGLE_FREE_PROVIDERS=openserp
PROCESS_ROLE=experiment
PYTHONPATH=/app
PYTHONUNBUFFERED=1
ENVEOF
awk -F= '!seen[$1]++' /tmp/$KEY.env > /tmp/$KEY.env.dedup && mv /tmp/$KEY.env.dedup /tmp/$KEY.env

# 3. Frozen code snapshot for the experiment manifest.
mkdir -p state/benchmarks/$KEY-code
cp realtime/*.py state/benchmarks/$KEY-code/
python3 - "$KEY" <<'PYEOF'
import hashlib, json, sys
from pathlib import Path
key = sys.argv[1]
snap = Path('state/benchmarks')/f'{key}-code'
hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(snap.glob('*.py'))}
Path('docs/research', f'{key}-code.json').write_text(json.dumps({
    'experiment': key,
    'note': 'rerun of lost r8: 4 shard x 1h, per-shard rps 2.0, yield plan, private proxies',
    'source_snapshot': str(snap), 'hashes': hashes}, indent=2)+'\n')
PYEOF

# 4. Launch one container per shard.
for i in 0 1 2 3; do
  docker rm -f realtime-google-r8-rerun-s$i >/dev/null 2>&1 || true
  docker create --name realtime-google-r8-rerun-s$i --restart on-failure:3 \
    --network realtime-web-search_default --entrypoint python \
    --env-file /tmp/$KEY.env \
    --mount type=bind,source=$PWD/state,target=/app/state \
    --mount type=volume,source=realtime-web-search_experiments-data,target=/app/state/experiments \
    --mount type=bind,source=$PWD/realtime,target=/app/realtime,readonly \
    realtime-web-search-web:latest \
    -m realtime.cli benchmark-google start \
    --directory /app/state/experiments/$KEY-s$i \
    --output /app/state/experiments/$KEY-s$i/export \
    --hours 1 --languages zh --executor pipeline --query-plan yield \
    --body-workers 24 --body-max-rss-mib 192 --search-workers 4 \
    --shard-count 4 --shard-index $i --search-rps 2.0 \
    --proxy-profile private --storage-budget-gib 10 --google-providers openserp \
    --whale --remote-only >/dev/null
  docker start realtime-google-r8-rerun-s$i >/dev/null
  echo "started shard $i"
done

echo "Watch: docker logs -f realtime-google-r8-rerun-s0"
echo "After 1h, summarize:"
echo "  docker run --rm -v realtime-web-search_experiments-data:/data alpine ls /data | grep $KEY"
