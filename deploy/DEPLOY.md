# 部署指南

生产运行架构：PostgreSQL 保存业务真源，Docling 在 CPU 解析材料，本地 Transformers 在 CPU 生成向量，Qdrant 提供检索，独立模型 API 提供生成，FastAPI 提供应用服务。系统不支持 SQLite、旧 Parser 或 Ollama 回退。

离线 Docker 交付优先使用 `deploy/docker/README.md`：应用镜像基于 Ubuntu 24.04，包含 CPU Python 依赖和 FFmpeg；PostgreSQL、Qdrant 分容器持久化。项目容器不要求 GPU 或目标机 Python。下面是原生部署说明；只有选择在同一机器自行部署 vLLM 时才需要 GPU/CUDA。

## 1. 环境

- Ubuntu 22.04+
- Python 3.12
- PostgreSQL 15+
- Embedding 模型和 Docling 模型已放入本地磁盘或 Hugging Face 缓存

```bash
python3 -m venv .venv
TMPDIR=/home/nas511/zhangruqi/.pip_tmp .venv/bin/pip install -r requirements.txt
cp deploy/env.example .env
```

至少配置：

```dotenv
IRA_DB_URL=postgresql+psycopg://ira:ira@127.0.0.1:5432/ira
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
.venv/bin/pip install 'vllm==0.27.1'
sudo deploy/install-vllm-service.sh "$PWD"
sudo systemctl enable --now ira-vllm
curl -fsS http://127.0.0.1:8100/v1/models
```

具体模型路径、上下文窗口、并发和 GPU 利用率配置见 `deploy/vllm.env.example`。应用模型接入统一在管理员系统设置中配置地址、模型名和 Key，不再从 `.env` 配置生成模型或 Tokenizer。上下文上限填 `0` 可读取 vLLM `/tokenize` 返回的实际 `max_model_len`；显式配置只能进一步收紧窗口。

音频材料使用 Docling 的原生 ASR 管线和 OpenAI Whisper，不调用 vLLM。部署主依赖后安装系统 `ffmpeg`，并预下载所选 Whisper 权重到运行用户的 `~/.cache/whisper/`；默认 `IRA_ASR_MODEL=medium`、CPU 执行、自动识别语种。已知全部为中文时可设置 `IRA_ASR_LANGUAGE=zh`。音频模型文件不提交到 Git；长录音的 CPU 转写耗时应单独评估。

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
