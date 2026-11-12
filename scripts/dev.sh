#!/usr/bin/env bash
# Linux development supervisor. Owns only its three child process groups.
set -Eeuo pipefail
set +m
umask 077
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
api_port=8000
web_port=5173
check_only=0
open_browser=1
startup_timeout=${ACA_DEV_STARTUP_TIMEOUT:-600}
shutdown_timeout=${ACA_DEV_SHUTDOWN_TIMEOUT:-600}
usage() {
  cat <<'HELP'
Usage: ./scripts/dev.sh [--check] [--no-browser] [--api-port PORT] [--port PORT]
Starts the API, job worker and frontend on localhost. Ctrl+C stops this run.
Logs: .dev/logs/<run>/   First startup may download the embedding model.
--check validates dependencies, browser login configuration and ports without starting services.
ACA_DEV_STARTUP_TIMEOUT / ACA_DEV_SHUTDOWN_TIMEOUT: seconds, default 600 each.
A second Ctrl+C forces this run to stop; interrupted mutations may need reconciliation.
HELP
}
while (( $# )); do
  case "$1" in
    --check) check_only=1; shift ;;
    --no-browser) open_browser=0; shift ;;
    --api-port|--port)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      if [[ $1 == --api-port ]]; then api_port=$2; else web_port=$2; fi
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
for value in "$api_port" "$web_port" "$startup_timeout" "$shutdown_timeout"; do
  [[ $value =~ ^[1-9][0-9]*$ && ${#value} -le 5 ]] || { echo 'Ports/timeouts must be positive integers (at most five digits).' >&2; exit 2; }
done
(( api_port <= 65535 && web_port <= 65535 && api_port != web_port )) || { echo 'Choose distinct valid ports.' >&2; exit 2; }
PYTHON="$ROOT/.venv/bin/python"
[[ -x $PYTHON ]] || { echo 'Run ./scripts/setup.sh first; .venv/bin/python is missing.' >&2; exit 1; }
for tool in node npm setsid flock curl tail; do
  command -v "$tool" >/dev/null || { echo "Missing $tool. This launcher requires Bash and Linux util-linux tools." >&2; exit 1; }
done
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (major < 22 || (major === 22 && minor < 12)) { console.error("Use Node 22.12 or newer (CI uses Node 22)."); process.exit(1) }'
(cd "$ROOT/backend" && "$PYTHON" "$ROOT/scripts/dev_support.py" check --api-port "$api_port" --port "$web_port")
if (( check_only )); then exit 0; fi
mkdir -p "$ROOT/.dev/logs"
exec 9>"$ROOT/.dev/dev.lock"
flock -n 9 || { echo 'Another dev.sh run owns this project. Stop that run first.' >&2; exit 1; }
LOG_DIR=$(mktemp -d "$ROOT/.dev/logs/run-XXXXXXXX")
URL="http://localhost:$web_port"
pids=()
tail_pid=''
cleaned=0
force_stop() {
  echo 'Forcing this run to stop; inspect Background Jobs for interrupted work.' >&2
  for pid in "${pids[@]}"; do kill -KILL -- "-$pid" 2>/dev/null || true; done
}
cleanup() {
  (( cleaned == 0 )) || return 0
  cleaned=1
  trap force_stop INT TERM
  if [[ -n $tail_pid ]]; then kill "$tail_pid" 2>/dev/null || true; fi
  for pid in "${pids[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || true; done
  local deadline=$((SECONDS + shutdown_timeout)) active pid
  if (( ${#pids[@]} )); then
    echo "Stopping this run; allowing up to ${shutdown_timeout}s for active work. Press Ctrl+C again to force stop."
  fi
  while :; do
    active=0
    for pid in "${pids[@]}"; do
      if kill -0 -- "-$pid" 2>/dev/null; then active=1; fi
    done
    (( active )) || break
    if (( SECONDS >= deadline )); then force_stop; break; fi
    sleep 0.2
done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  if [[ -n $tail_pid ]]; then wait "$tail_pid" 2>/dev/null || true; fi
  echo "Stopped. Logs retained in $LOG_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
start() {
  local name=$1 directory=$2
  shift 2
  (cd "$directory" && exec setsid "$@") >>"$LOG_DIR/$name.log" 2>&1 9>&- &
  pids+=("$!")
}
alive() {
  local pid
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "A service exited. Inspect logs in $LOG_DIR" >&2
      return 1
    fi
  done
}
wait_http() {
  local path=$1 deadline=$((SECONDS + startup_timeout))
  until curl --noproxy '*' --silent --fail --max-time 2 "$path" >/dev/null; do
    alive || return 1
    (( SECONDS < deadline )) || { echo "Startup timed out. Inspect $LOG_DIR" >&2; return 1; }
    sleep 0.5
  done
  alive
}
echo "Starting API; first-time knowledge setup may take several minutes. Logs: $LOG_DIR"
# Reload is intentionally off: the worker and API must use the same code revision.
start api "$ROOT/backend" "$PYTHON" -u -m uvicorn main:app --host 127.0.0.1 --port "$api_port"
wait_http "http://127.0.0.1:$api_port/ready"
echo 'Starting background worker…'
start worker "$ROOT/backend" "$PYTHON" -u -m services.job_worker
worker_deadline=$((SECONDS + startup_timeout))
until (cd "$ROOT/backend" && "$PYTHON" "$ROOT/scripts/dev_support.py" worker-ready); do
  alive || exit 1
  (( SECONDS < worker_deadline )) || { echo "Worker startup timed out. Inspect $LOG_DIR" >&2; exit 1; }
  sleep 0.5
done
start frontend "$ROOT/frontend" env ACA_DEV_API_PORT="$api_port" npm run dev -- --host 127.0.0.1 --port "$web_port" --strictPort
wait_http "http://127.0.0.1:$web_port/"
printf '\nReady: %s\nAPI: http://127.0.0.1:%s\nCtrl+C stops all three services.\n\n' "$URL" "$api_port"
if (( open_browser )) && [[ -n ${DISPLAY:-}${WAYLAND_DISPLAY:-} ]] && command -v xdg-open >/dev/null; then
  xdg-open "$URL" >/dev/null 2>&1 9>&- &
fi
tail -n 10 -F "$LOG_DIR/api.log" "$LOG_DIR/worker.log" "$LOG_DIR/frontend.log" 9>&- &
tail_pid=$!
status=0
wait -n "${pids[@]}" || status=$?
echo "A service stopped (exit $status). Stopping the remaining services; see $LOG_DIR" >&2
# Even a clean exit of a long-running service means this run is no longer healthy.
exit 1
