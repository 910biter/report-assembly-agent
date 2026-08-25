# vLLM generation baseline

The report workflow can switch generation between Ollama and an
OpenAI-compatible vLLM service. Embeddings remain on the configured Ollama
endpoint, so the migration does not change retrieval vectors.

Install the GPU serving profile with:

```bash
.venv/bin/pip install -r requirements-vllm.txt
```

## Runtime profile

- Model: `Qwen3.6-27B-GPTQ-Int4`
- GPU: one RTX 3090 24 GB
- Initial context: 15,360 tokens (measured safe limit on the 24 GB card)
- Active sequences: 2
- Continuous batching: provided by vLLM
- Prefix caching and chunked prefill: enabled
- Scheduling: priority, with interactive requests ahead of workflow/background
- Structured agents: explicit non-thinking mode; required rationale remains in
  the returned schema instead of consuming an unbounded hidden-token budget
- Sampling: PyTorch sampler (the target host's CUDA 10.2 toolkit cannot JIT
  FlashInfer sampling kernels; attention and GPTQ Marlin kernels remain enabled)

The application must use:

```dotenv
IRA_GENERATION_BACKEND=vllm

## Keep vLLM resident

Production deployments should run vLLM as an independent system service. This
keeps model weights resident when the Web application restarts and automatically
recovers the inference server after a host reboot or process failure.

```bash
sudo PROJECT_ROOT=/home/nas511/zhangruqi/agent-235 \
  ./deploy/install-vllm-service.sh
sudo systemctl start ira-vllm
systemctl status ira-vllm
```

Runtime parameters live in `deploy/vllm.env`. Changing that file requires a
maintenance-window restart of `ira-vllm`; ordinary application restarts do not.
IRA_GENERATION_URL=http://127.0.0.1:8100/v1
IRA_GENERATION_MODEL=qwen3.6-27b
IRA_MODEL_CONTEXT_WINDOW_TOKENS=15360
IRA_LLM_CONCURRENCY=2
IRA_EVIDENCE_BATCH_CONCURRENCY=2
```

Do not add a semantic response cache for generated facts, analysis, or prose.
vLLM prefix caching only reuses identical prompt-prefix computation and does
not reuse another task's answer.

Evidence material batches are the only workflow calls enabled for bounded
in-task concurrency. Planner, Analysis, Narrative Plan, and Writer retain their
dependency order. Batch results are merged in input order, while fact
check-and-insert remains serialized to protect deduplication and provenance.
