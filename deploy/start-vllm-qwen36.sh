#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/home/nas511/zhangruqi/agent-235}"
MODEL_PATH="${MODEL_PATH:-/home/nas511/zhangruqi/models/Qwen3.6-27B-GPTQ-Int4}"
HOST="${IRA_VLLM_HOST:-127.0.0.1}"
PORT="${IRA_VLLM_PORT:-8100}"

# The host has a modern NVIDIA driver but a legacy CUDA 10.2 toolkit. vLLM's
# PyTorch sampler is prebuilt and avoids FlashInfer's incompatible local JIT.
export VLLM_USE_FLASHINFER_SAMPLER=0

args=(
  serve "$MODEL_PATH"
  --host "$HOST"
  --port "$PORT"
  --served-model-name qwen3.6-27b
  --trust-remote-code
  --max-model-len "${IRA_VLLM_MAX_MODEL_LEN:-24576}"
  --max-num-seqs "${IRA_VLLM_MAX_NUM_SEQS:-2}"
  --max-num-batched-tokens "${IRA_VLLM_MAX_BATCHED_TOKENS:-8192}"
  --gpu-memory-utilization "${IRA_VLLM_GPU_MEMORY_UTILIZATION:-0.98}"
  --limit-mm-per-prompt '{"image":0,"video":0}'
  --enable-prefix-caching
  --enable-chunked-prefill
  --scheduling-policy priority
)

if [[ -n "${IRA_VLLM_KV_CACHE_DTYPE:-}" ]]; then
  args+=(--kv-cache-dtype "$IRA_VLLM_KV_CACHE_DTYPE")
fi
if [[ "${IRA_VLLM_CALCULATE_KV_SCALES:-0}" == "1" ]]; then
  args+=(--calculate-kv-scales)
fi
if [[ -n "${IRA_VLLM_CPU_OFFLOAD_GB:-}" ]]; then
  args+=(--cpu-offload-gb "$IRA_VLLM_CPU_OFFLOAD_GB")
fi

exec "$PROJECT_ROOT/.venv/bin/vllm" "${args[@]}"
