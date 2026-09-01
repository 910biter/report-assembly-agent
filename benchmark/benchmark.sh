#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${BENCHMARK_ENV_FILE:-$ROOT/benchmark.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ROOT/benchmark.env.example" "$ENV_FILE"
fi
# Export the serving envelope so runner.py records it in result.json.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

ACTION="${1:-all}"
if [[ $# -gt 0 ]]; then shift; fi
if [[ $# -gt 0 ]]; then MODEL_PATH_OR_ID="$1"; fi

VENV_DIR="${VENV_DIR:-$ROOT/.venv}"
HOST="${VLLM_HOST:-127.0.0.1}"
PORT="${VLLM_PORT:-8100}"
BASE_URL="http://$HOST:$PORT/v1"
METRICS_URL="http://$HOST:$PORT/metrics"
PID_FILE="${VLLM_PID_FILE:-$ROOT/vllm.pid}"
LOG_FILE="${VLLM_LOG_FILE:-$ROOT/vllm.log}"
RESULT_FILE="${RESULT_FILE:-$ROOT/result.json}"
if [[ "$RESULT_FILE" != /* ]]; then RESULT_FILE="$ROOT/$RESULT_FILE"; fi

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

python_bin() {
  printf '%s' "$VENV_DIR/bin/python"
}

vllm_bin() {
  if [[ -n "${VLLM_BIN:-}" ]]; then
    printf '%s' "$VLLM_BIN"
  elif [[ -x "$VENV_DIR/bin/vllm" ]]; then
    printf '%s' "$VENV_DIR/bin/vllm"
  elif command -v vllm >/dev/null 2>&1; then
    command -v vllm
  else
    return 1
  fi
}

setup() {
  command -v "${PYTHON_BIN:-python3}" >/dev/null 2>&1 || die "Python 3 is required"
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    "${PYTHON_BIN:-python3}" -m venv "$VENV_DIR"
  fi
  "$(python_bin)" -m pip install --upgrade pip
  "$(python_bin)" -m pip install -r "$ROOT/requirements.txt"
  if ! vllm_bin >/dev/null 2>&1; then
    "$(python_bin)" -m pip install "${VLLM_INSTALL_SPEC:-vllm}"
  fi
  vllm_bin >/dev/null 2>&1 || die "vLLM installation did not provide a vllm executable"
}

healthy() {
  local probe_python
  if [[ -x "$(python_bin)" ]]; then
    probe_python="$(python_bin)"
  else
    probe_python="${PYTHON_BIN:-python3}"
  fi
  "$probe_python" - "$BASE_URL/models" <<'PY' >/dev/null 2>&1
import json, sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=3) as response:
    payload = json.load(response)
if not payload.get("data"):
    raise SystemExit(1)
PY
}

start() {
  [[ -n "${MODEL_PATH_OR_ID:-}" ]] || die "Set MODEL_PATH_OR_ID in benchmark.env or pass the model path/id as the second argument"
  if healthy; then
    printf 'vLLM is already healthy at %s\n' "$BASE_URL"
    return
  fi
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    die "vLLM process exists but is not healthy; inspect $LOG_FILE"
  fi
  local -a args=(
    serve "$MODEL_PATH_OR_ID"
    --host "$HOST" --port "$PORT"
    --served-model-name "$SERVED_MODEL_NAME"
    --trust-remote-code
    --max-model-len "$MAX_MODEL_LEN"
    --max-num-seqs "$MAX_NUM_SEQS"
    --max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS"
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
    --enable-prefix-caching
    --enable-chunked-prefill
    --scheduling-policy priority
  )
  [[ -z "${KV_CACHE_DTYPE:-}" ]] || args+=(--kv-cache-dtype "$KV_CACHE_DTYPE")
  [[ "${CALCULATE_KV_SCALES:-0}" != "1" ]] || args+=(--calculate-kv-scales)
  [[ -z "${CPU_OFFLOAD_GB:-}" ]] || args+=(--cpu-offload-gb "$CPU_OFFLOAD_GB")
  nohup "$(vllm_bin)" "${args[@]}" >"$LOG_FILE" 2>&1 < /dev/null &
  echo $! > "$PID_FILE"
  printf 'Starting vLLM (pid %s); log: %s\n' "$(cat "$PID_FILE")" "$LOG_FILE"
  local deadline=$((SECONDS + ${STARTUP_TIMEOUT_SECONDS:-1800}))
  until healthy; do
    if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      tail -n 80 "$LOG_FILE" >&2 || true
      die "vLLM exited during startup"
    fi
    (( SECONDS < deadline )) || die "vLLM startup timed out; inspect $LOG_FILE"
    sleep 5
  done
  printf 'vLLM is healthy at %s\n' "$BASE_URL"
}

run() {
  healthy || die "vLLM is not healthy; run '$0 start' first"
  "$(python_bin)" "$ROOT/runner.py" \
    --dataset "$ROOT/stage-replay.jsonl" \
    --endpoint "$BASE_URL" \
    --metrics-url "$METRICS_URL" \
    --model "$SERVED_MODEL_NAME" \
    --concurrency "${RUNNER_CONCURRENCY:-1}" \
    --repeat "${RUNNER_REPEAT:-3}" \
    --warmup "${RUNNER_WARMUP:-1}" \
    --timeout-seconds "${RUNNER_TIMEOUT_SECONDS:-900}" \
    --sample-interval-seconds "${SAMPLE_INTERVAL_SECONDS:-0.5}" \
    --output "$RESULT_FILE"
}

status() {
  if healthy; then
    printf 'healthy %s\n' "$BASE_URL"
  else
    printf 'unavailable %s\n' "$BASE_URL"
    return 1
  fi
}

stop() {
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    kill "$(cat "$PID_FILE")"
    printf 'Stopped vLLM pid %s\n' "$(cat "$PID_FILE")"
  fi
  rm -f "$PID_FILE"
}

case "$ACTION" in
  setup) setup ;;
  start) setup; start ;;
  run) setup; run ;;
  all) setup; start; run ;;
  status) status ;;
  stop) stop ;;
  *) die "Usage: $0 {setup|start|run|all|status|stop} [MODEL_PATH_OR_ID]" ;;
esac
