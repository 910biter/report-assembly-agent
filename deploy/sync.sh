#!/bin/bash
# 模块迁移:代码 / site-packages / Hugging Face 模型缓存 / Qdrant 的搬运脚本
# 用法:先 export SSHPASS='<ssh密码>'
#   sync.sh code    本地 → 目标机(代码,排除 venv/runtime)
#   sync.sh sp      源机 → 目标机(site-packages,离线部署用)
#   sync.sh hf      源机 → 目标机(Docling、Embedding 与 vLLM 模型缓存)
#   sync.sh qdrant  源机 → 目标机(qdrant 二进制+配置)
set -euo pipefail

SRC="${SRC:-zhangruqi@100.120.119.108}"            # 源机(旧部署)
DST="${DST:-zhangruqi@100.120.119.161}"            # 目标机(新部署)
DST_APP="${DST_APP:-/home/nas511/zhangruqi/agent-server}"
LOCAL="${LOCAL:-/home/biter/agent/report-assembly-agent}"

SSHOPTS="-o StrictHostKeyChecking=no"

cmd="${1:?用法: sync.sh <code|sp|hf|qdrant>}"

case "$cmd" in
  code)
    rsync -az --delete --exclude '.venv' --exclude '.git' --exclude '__pycache__' \
      --exclude 'runtime' --exclude '.env' --exclude '*.pyc' --exclude 'report.db' \
      -e "ssh $SSHOPTS" "$LOCAL/" "$DST:$DST_APP/"
    echo "代码已同步"
    ;;
  sp)
    # site-packages(源机 venv → 目标机);执行后目标机补装新增依赖(sqlalchemy 等)
    sshpass -e ssh $SSHOPTS "$SRC" "sshpass -e rsync -az --exclude '__pycache__' -e 'ssh $SSHOPTS' \
      /home/nas511/zhangruqi/agent/.venv/lib/python3.12/site-packages/ \
      $DST:$DST_APP/.venv/lib/python3.12/site-packages/"
    echo "site-packages 已同步(新增依赖:见 DEPLOY.md 三.B wheel 步骤)"
    ;;
  hf)
    sshpass -e ssh $SSHOPTS "$SRC" "sshpass -e scp $SSHOPTS -r /home/zhangruqi/.cache/huggingface \
      $DST:/home/zhangruqi/.cache/"
    echo "HF 缓存已拷贝"
    ;;
  qdrant)
    sshpass -e ssh $SSHOPTS "$SRC" "sshpass -e scp $SSHOPTS \
      /home/nas511/zhangruqi/qdrant /home/nas511/zhangruqi/qdrant_config.yaml $DST:$DST_APP/"
    echo "qdrant 已拷贝"
    ;;
  *)
    echo "未知命令: $cmd"; exit 1 ;;
esac
