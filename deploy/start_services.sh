#!/bin/bash
# 目标机启动全部服务:qdrant + vLLM generation + app(CPU embedding随应用加载)
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

GENERATION_BACKEND=$(sed -n 's/^IRA_GENERATION_BACKEND=//p' "$APP_DIR/.env" | tail -1 | tr -d '\r' || true)
GENERATION_BACKEND=${GENERATION_BACKEND:-vllm}
echo "==> [2/3] generation backend: $GENERATION_BACKEND"
if [ "$GENERATION_BACKEND" = "vllm" ]; then
  VLLM_URL=$(sed -n 's/^IRA_GENERATION_URL=//p' "$APP_DIR/.env" | tail -1 | tr -d '\r' || true)
  VLLM_URL=${VLLM_URL:-http://127.0.0.1:8100/v1}
  if curl -fsS --max-time 3 "$VLLM_URL/models" >/dev/null 2>&1; then
    echo "vLLM 已就绪"
  else
    if systemctl is-enabled ira-vllm.service >/dev/null 2>&1; then
      if ! systemctl is-active ira-vllm.service >/dev/null 2>&1; then
        echo "ira-vllm.service 未运行；请先执行: sudo systemctl start ira-vllm"
        exit 1
      fi
      echo "等待常驻 vLLM 服务完成模型加载"
    elif ! pgrep -f 'vllm serve.*Qwen3.6-27B-GPTQ-Int4' >/dev/null 2>&1; then
      nohup env HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
        "$APP_DIR/deploy/start-vllm-qwen36.sh" >/tmp/vllm-qwen36.log 2>&1 < /dev/null &
    fi
    for _ in $(seq 1 120); do
      curl -fsS --max-time 3 "$VLLM_URL/models" >/dev/null 2>&1 && break
      sleep 5
    done
    if ! curl -fsS --max-time 3 "$VLLM_URL/models" >/dev/null 2>&1; then
      echo "vLLM 启动失败"
      if systemctl is-enabled ira-vllm.service >/dev/null 2>&1; then
        journalctl -u ira-vllm.service -n 20 --no-pager 2>/dev/null || true
      else
        tail -20 /tmp/vllm-qwen36.log 2>/dev/null || true
      fi
      exit 1
    fi
    echo "vLLM 已就绪"
  fi
fi

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
