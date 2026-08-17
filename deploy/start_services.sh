#!/bin/bash
# 目标机启动全部服务:qdrant + app(ollama 为系统服务,已由部署放好模型)
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

echo "==> [2/3] ollama 状态"
ollama list 2>/dev/null | head -3 || echo "ollama 异常"

echo "==> [3/3] app 服务"
pkill -f 'run.py' 2>/dev/null || true
sleep 2
cd "$APP_DIR"
nohup .venv/bin/python run.py > /tmp/app.log 2>&1 < /dev/null &
sleep 10
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/api/tasks 2>/dev/null || echo 000)
echo "API: $CODE"
if [ "$CODE" != "200" ]; then echo "启动失败,见 /tmp/app.log"; tail -5 /tmp/app.log; exit 1; fi
echo "全部服务就绪"