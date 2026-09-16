# 部署指南

生产部署只有一套运行架构：PostgreSQL 保存业务真源，Docling 在 CPU 解析材料，本地 Transformers 在 CPU 生成向量，Qdrant 提供检索，vLLM 通过 OpenAI-compatible API 提供生成，FastAPI 提供应用服务。系统不支持 SQLite、旧 Parser 或 Ollama 回退。

## 1. 环境

- Ubuntu 22.04+
- Python 3.12
- PostgreSQL 15+
- NVIDIA GPU 与匹配的 CUDA 驱动
- 生成模型、Embedding 模型和 Docling 模型已放入本地磁盘或 Hugging Face 缓存

```bash
python3 -m venv .venv
TMPDIR=/home/nas511/zhangruqi/.pip_tmp .venv/bin/pip install -r requirements.txt
cp deploy/env.example .env
```

至少配置：

```dotenv
IRA_DB_URL=postgresql+psycopg://ira:ira@127.0.0.1:5432/ira
IRA_GENERATION_URL=http://127.0.0.1:8100/v1
IRA_GENERATION_MODEL=qwen3.6-27b
IRA_GENERATION_TOKENIZER_PATH=/path/to/generation-model
IRA_EMBEDDING_MODEL_PATH=/path/to/Qwen3-Embedding-0.6B
IRA_QDRANT_URL=http://127.0.0.1:6333
IRA_VECTOR_BACKEND=qdrant
```

## 2. PostgreSQL

创建 `.env` 指向的数据库和账号。应用启动时以 `app/infrastructure/orm.py` 为唯一 Schema 真源执行建表及当前版本的增量列迁移。

## 3. Qdrant

```bash
ulimit -n 1048576
./qdrant --config-path qdrant_config.yaml
curl -fsS http://127.0.0.1:6333/healthz
```

## 4. vLLM

推荐安装并启用常驻服务：

```bash
sudo deploy/install-vllm-service.sh "$PWD"
sudo systemctl enable --now ira-vllm
curl -fsS http://127.0.0.1:8100/v1/models
```

具体模型路径、上下文窗口、并发和 GPU 利用率配置见 `deploy/vllm.env.example`。`IRA_MODEL_CONTEXT_WINDOW_TOKENS` 必须与 vLLM 的 `--max-model-len` 一致。

## 5. 应用

```bash
deploy/start_services.sh "$PWD"
curl -fsS http://127.0.0.1:8000/api/health
```

`start_services.sh` 会检查 Qdrant 和 vLLM，只重启应用进程；常驻 vLLM 的生命周期优先由 systemd 管理。

## 6. 知识图谱

知识图谱能力默认可用，但不会自动阻塞主流程。用户在任务的“关系网络”栏点击构建，任务级状态完成后，Analysis、增量对比和助手按状态选择性使用；Neo4j 仅作为可重建查询投影，不是第二事实库。

## 7. 验证

- `/api/health`：生成模型、本地 Embedding、队列和上下文 tokenizer 正常。
- `/api/tasks`：返回 200。
- 上传 DOCX 模板：产生 `template_schema`，导出只使用任务绑定模板。
- 上传材料：Docling 解析成功并生成 Units，Qdrant 集合增长。
- 完整任务：Planner、Evidence、Analysis、Narrative、Writer、QA 均有调用记录和阶段产物。
