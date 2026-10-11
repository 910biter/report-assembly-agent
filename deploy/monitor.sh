#!/bin/bash
# 任务监控(静默,无异常也输出状态行)
# 用法:本地执行(需 SSH 可达);配 cron 每 10 分钟:
#   hermes cron create --schedule 'every 10m' --script monitor.sh --deliver local
set -euo pipefail

DST="${DST:-zhangruqi@100.120.119.161}"
TASK_ID="${1:-}"    # 缺省取 API 最新任务
SSHOPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=15"

if [ -z "$TASK_ID" ]; then
  TASK_ID=$(sshpass -e ssh $SSHOPTS "$DST" \
    "curl -s --max-time 10 http://127.0.0.1:8000/api/tasks 2>/dev/null | python3 -c 'import json,sys; ts=json.load(sys.stdin); print(ts[0][\"task_id\"] if ts else \"\")'" 2>/dev/null)
fi

OUT=$(sshpass -e ssh $SSHOPTS "$DST" "curl -s --max-time 10 http://127.0.0.1:8000/api/tasks/$TASK_ID 2>/dev/null | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
    p=d.get(\"parse_progress\") or {}
    ev=d.get(\"evidence_progress\") or {}
    print(f\"stage={d.get(chr(115)+chr(116)+chr(97)+chr(103)+chr(101))} parse={p.get(chr(100)+chr(111)+chr(110)+chr(101),0)}/{p.get(chr(116)+chr(111)+chr(116)+chr(97)+chr(108),0)} evid={ev.get(chr(100)+chr(111)+chr(110)+chr(101),0)}/{ev.get(chr(116)+chr(111)+chr(116)+chr(97)+chr(108),0)}\")
except Exception:
    print(\"task_query_fail\")
'" 2>/dev/null)
MODEL=$(sshpass -e ssh $SSHOPTS "$DST" "curl -s --max-time 10 http://127.0.0.1:8100/v1/models 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get(\"data\") or [{}])[0].get(\"id\",\"unavailable\"))'" 2>/dev/null)
API=$(sshpass -e ssh $SSHOPTS "$DST" "curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8000/api/tasks 2>/dev/null" 2>/dev/null)
echo "综述任务: ${OUT:-无} | 生成模型: ${MODEL:-unavailable} | API: ${API:-000}"
