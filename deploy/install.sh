#!/bin/bash
# 目标机环境安装:venv + 依赖(脚本在目标机上执行)
# 用法:ssh <user>@<host> 'bash -s' < install.sh [auth=askpass]
set -e
APP_DIR="${1:-/home/nas511/zhangruqi/agent-server}"
PIP_TMP="${2:-/home/nas511/zhangruqi/.pip_tmp}"

echo "==> [1/3] 建 venv"
python3 -m venv "$APP_DIR/.venv"

echo "==> [2/3] 装依赖(TMPDIR=$PIP_TMP 避免 /tmp 占满)"
mkdir -p "$PIP_TMP"
cd "$APP_DIR"
TMPDIR="$PIP_TMP" .venv/bin/pip install --upgrade pip -q
TMPDIR="$PIP_TMP" .venv/bin/pip install -r requirements.txt

echo "==> [3/3] 验证"
.venv/bin/python -c 'import fastapi, sqlalchemy, qdrant_client, docling; print("DEPS_OK")'
echo "环境安装完成"