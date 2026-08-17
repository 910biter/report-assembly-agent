#!/bin/bash
# 材料 + 模板 + 任务导入(在目标机执行,或 ssh 远程执行)
# 用法:ssh <user>@<host> 'bash -s' < import_materials.sh <app_dir> <theme> <variant>
set -e
APP_DIR="${1:-/home/nas511/zhangruqi/agent-server}"
META="${2:-可信执行环境远程证明机制研究(学术综述,15000-20000字)}"
VARIANT="${3:-1}"

cd "$APP_DIR/runtime/materials"
[ -z "$(ls -A)" ] && { echo "材料目录为空: $APP_DIR/runtime/materials"; exit 1; }

echo "==> [1/2] 建任务(自动带上全部材料)"
CMD="curl -s -X POST http://127.0.0.1:8000/api/tasks -F 'theme=$META' -F 'variant_id=$VARIANT'"
for f in *; do CMD="$CMD -F 'files=@$f'"; done
RESP=$(eval "$CMD")
echo "$RESP"
TASK_ID=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("task_id",""))')

echo "==> [2/2] 触发运行"
curl -s -X POST "http://127.0.0.1:8000/api/tasks/$TASK_ID/run"
echo
echo "任务 $TASK_ID 已启动(监控: monitor.sh)"