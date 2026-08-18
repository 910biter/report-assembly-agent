#!/usr/bin/env bash
# 一键重置 108 部署环境:停服务 → 清空 PG → 清空 Qdrant → 重启服务。
# 用法: bash deploy/reset_108.sh [--keep-log]   (SSHPASS 环境变量需已导出,或交互输入密码)
set -euo pipefail

HOST="zhangruqi@100.120.119.108"
REMOTE_DIR="/home/nas511/zhangruqi/agent-server"
KEEP_LOG=0
if [[ "${1:-}" == "--keep-log" ]]; then KEEP_LOG=1; fi

if [[ -z "${SSHPASS:-}" ]]; then
  read -r -s -p "SSH 密码: " SSHPASS
  export SSHPASS
  echo
fi

SSH=(sshpass -e ssh -o StrictHostKeyChecking=no "$HOST")
SCP=(sshpass -e scp -o StrictHostKeyChecking=no)

echo "[1/4] 停应用服务..."
"${SSH[@]}" "pkill -f '\.venv/bin/python run\.py$' 2>/dev/null || true; sleep 2"

echo "[2/4] 清空 PostgreSQL(重建全部表)..."
"${SSH[@]}" "cd $REMOTE_DIR && timeout 60 .venv/bin/python -c \"
from app.db import init_db
from app.infrastructure.orm import Base
from app.db import _get_engine
from sqlalchemy import text
with _get_engine().connect() as c:
    names=', '.join(f'\\\"{t.name}\\\"' for t in reversed(Base.metadata.sorted_tables))
    c.execute(text(f'DROP TABLE IF EXISTS {names} CASCADE'))
    c.commit()
init_db()
print('PG cleared')
\""

echo "[3/4] 清空 Qdrant 集合..."
"${SSH[@]}" "cd $REMOTE_DIR && timeout 30 .venv/bin/python -c \"
from qdrant_client import QdrantClient
c=QdrantClient(url='http://127.0.0.1:6333', timeout=10)
for n in ('ira_units','ira_materials','ira_facts'):
    try: c.delete_collection(n); print('deleted', n)
    except Exception: pass
\""

if [[ $KEEP_LOG -eq 0 ]]; then
  "${SSH[@]}" "rm -f /tmp/app.log"
fi

echo "[4/4] 启动服务..."
"${SSH[@]}" "cd $REMOTE_DIR && nohup .venv/bin/python run.py >/tmp/app.log 2>&1 </dev/null & echo started"
sleep 15

echo "[验证] API 健康检查..."
"${SSH[@]}" "curl -s -o /dev/null -w 'API:%{http_code}\n' http://127.0.0.1:8000/api/health; curl -s http://127.0.0.1:8000/api/tasks; echo"

echo "重置完成:PG 已重建、Qdrant 已清空、服务运行中。"
