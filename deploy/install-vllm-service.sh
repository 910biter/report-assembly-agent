#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/home/nas511/zhangruqi/agent-235}"
UNIT_SOURCE="$PROJECT_ROOT/deploy/systemd/ira-vllm.service"
UNIT_TARGET="/etc/systemd/system/ira-vllm.service"
ENV_TARGET="$PROJECT_ROOT/deploy/vllm.env"

if [[ ! -f "$ENV_TARGET" ]]; then
  cp "$PROJECT_ROOT/deploy/vllm.env.example" "$ENV_TARGET"
fi

install -m 0644 "$UNIT_SOURCE" "$UNIT_TARGET"
systemctl daemon-reload
systemctl enable ira-vllm.service

echo "Installed ira-vllm.service."
echo "Review $ENV_TARGET, then run: sudo systemctl start ira-vllm"
