#!/usr/bin/env bash
# Start disposable loopback services, run every canonical test, clean up only
# these child processes. No Docker, production database or browser is used.
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TASK_OUTPUT="${1:-$TASK_ROOT/state/regression-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$TASK_OUTPUT"
TASK_OUTPUT="$(cd "$TASK_OUTPUT" && pwd)"
TASK_PYTHON="$TASK_ROOT/.venv/bin/python"
for TASK_BINARY in initdb pg_ctl redis-server; do
  command -v "$TASK_BINARY" >/dev/null || { echo "Missing executable: $TASK_BINARY" >&2; exit 1; }
done
[[ ! -e "$TASK_OUTPUT/pg" ]] || { echo 'Choose a new output directory' >&2; exit 1; }
TASK_PG_STARTED=0
TASK_REDIS_PID=''
TASK_SOCKET_DIR="$(mktemp -d /tmp/searchflow-qa.XXXXXX)"
cleanup() {
  TASK_EXIT=$?
  trap - EXIT INT TERM
  if [[ -n "$TASK_REDIS_PID" ]]; then
    kill "$TASK_REDIS_PID" 2>/dev/null || true
    wait "$TASK_REDIS_PID" 2>/dev/null || true
  fi
  if [[ "$TASK_PG_STARTED" == 1 ]]; then
    pg_ctl -D "$TASK_OUTPUT/pg" -m fast stop > "$TASK_OUTPUT/cleanup.log" 2>&1 || TASK_EXIT=1
  fi
  rmdir "$TASK_SOCKET_DIR" 2>/dev/null || true
  exit "$TASK_EXIT"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
TASK_PORTS="$($TASK_PYTHON - <<'PY'
import socket
with socket.socket() as a, socket.socket() as b:
    a.bind(('127.0.0.1', 0)); b.bind(('127.0.0.1', 0))
    print(a.getsockname()[1], b.getsockname()[1])
PY
)"
read -r TASK_PG_PORT TASK_REDIS_PORT <<< "$TASK_PORTS"
initdb -D "$TASK_OUTPUT/pg" -A trust -U qa > "$TASK_OUTPUT/initdb.log"
pg_ctl -D "$TASK_OUTPUT/pg" -l "$TASK_OUTPUT/postgres.log" \
  -o "-p $TASK_PG_PORT -h 127.0.0.1 -k $TASK_SOCKET_DIR" start
TASK_PG_STARTED=1
redis-server --bind 127.0.0.1 --port "$TASK_REDIS_PORT" --save '' --appendonly no \
  > "$TASK_OUTPUT/redis.log" 2>&1 &
TASK_REDIS_PID=$!
"$TASK_PYTHON" - "$TASK_REDIS_PORT" <<'PY'
import socket,sys,time
for _ in range(100):
    try:
        with socket.create_connection(('127.0.0.1',int(sys.argv[1])), timeout=.1): break
    except OSError: time.sleep(.05)
else: raise SystemExit('Disposable Redis failed to start')
PY
kill -0 "$TASK_REDIS_PID"
TASK_TEST_ARGS=("$TASK_ROOT/tests")
if (( $# > 1 )); then TASK_TEST_ARGS=("${@:2}"); fi
cd "$TASK_ROOT"
if [[ -n "${SEARCHFLOW_TEST_PACKAGE:-}" ]]; then
  export PYTHONPATH="$SEARCHFLOW_TEST_PACKAGE:$TASK_ROOT"
  cd /tmp
fi
DATABASE_URL="postgresql://qa@127.0.0.1:$TASK_PG_PORT/postgres" \
TEST_DATABASE_URL="postgresql://qa@127.0.0.1:$TASK_PG_PORT/postgres" \
TEST_REDIS_URL="redis://127.0.0.1:$TASK_REDIS_PORT/15" NO_PROXY=127.0.0.1,localhost \
  "$TASK_PYTHON" -m pytest -q "${TASK_TEST_ARGS[@]}" --junitxml="$TASK_OUTPUT/pytest.xml" > "$TASK_OUTPUT/pytest.log" 2>&1
tail -n 2 "$TASK_OUTPUT/pytest.log"
