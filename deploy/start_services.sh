#!/bin/bash
# Start application and local vector storage; model serving is independent.
# 用法:ssh <user>@<host> 'bash -s' < start_services.sh <app_dir>
set -e
APP_DIR="${1:-/home/nas511/zhangruqi/agent-server}"

echo "==> [1/3] qdrant(ulimit 1048576,fd 必须)"
if ! curl -s --max-time 3 http://127.0.0.1:6333/healthz >/dev/null 2>&1; then
  cd "$APP_DIR"
  (setsid bash -c 'ulimit -n 1048576; exec ./qdrant --config-path qdrant_config.yaml' \
    > /tmp/qdrant.log 2>&1 < /dev/null &)
  sleep 4
  curl -s --max-time 5 http://127.0.0.1:6333/healthz && echo " qdrant OK" || echo "qdrant 启动异常(见 /tmp/qdrant.log)"
else
  echo "qdrant 已在运行"
fi

echo "==> [2/3] 模型接入由系统设置管理；本脚本不启动或重启模型服务"

echo "==> [3/3] app 服务"
for PID in $(pgrep -f 'python.*run.py' || true); do
  if [ "$(readlink -f "/proc/$PID/cwd" 2>/dev/null)" = "$(realpath "$APP_DIR")" ]; then
    kill "$PID"
  fi
done
sleep 2
cd "$APP_DIR"
nohup .venv/bin/python run.py > /tmp/app.log 2>&1 < /dev/null &
CODE=000
for attempt in $(seq 1 60); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8000/api/health 2>/dev/null) || true
  [ "$CODE" = "200" ] && break
  sleep 2
done
echo "API: $CODE"
if [ "$CODE" != "200" ]; then echo "启动失败,见 /tmp/app.log"; tail -5 /tmp/app.log; exit 1; fi
echo "全部服务就绪"
