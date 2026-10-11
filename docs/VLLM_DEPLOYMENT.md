# vLLM 生成服务

项目只通过 OpenAI-compatible API 调用生成模型。vLLM 应作为常驻服务运行，应用重启不得重复加载模型。

## 配置关系

- vLLM `--served-model-name` = `IRA_GENERATION_MODEL`
- vLLM `--max-model-len` = `IRA_MODEL_CONTEXT_WINDOW_TOKENS`
- vLLM 地址 = `IRA_GENERATION_URL`
- `IRA_LLM_CONCURRENCY` 控制工作流并发入口，vLLM 执行 continuous batching
- `IRA_INTERACTIVE_CONCURRENCY` 为用户协作请求保留独立入口，服务端通过 priority 调度

## 启动

```bash
cp deploy/vllm.env.example .vllm.env
sudo deploy/install-vllm-service.sh "$PWD"
sudo systemctl enable --now ira-vllm
curl -fsS http://127.0.0.1:8100/v1/models
```

应用健康接口：

```bash
curl -fsS http://127.0.0.1:8000/api/health
```

Embedding 不经过 vLLM，由应用内本地 Transformers CPU runtime 提供；材料解析由 Docling CPU pipeline 提供。
