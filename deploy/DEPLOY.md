# 部署迁移指南(report-assembly-agent)

完整迁移到新机器(如 100.120.119.161)的步骤与脚本。所有脚本不带业务数据,纯流程;

业务材料/模型/缓存按需从源机(108)或备份同步。

## 一、前置条件

- 目标机:Ubuntu 22.04+/24.04+(GPU 机优先,DeepSeek 27.8B 建议 24G 显存)
- Python 3.12(sudo apt install -y python3.12-venv python3.12-dev)
- 磁盘:代码+venv ~7G、模型 ~20G、材料 ~100M
- SSH 免密或 sshpass(脚本统一用 `SSHPASS` 环境变量)
- 源机(可选):旧部署机器(108),用于搬运 site-packages/模型/HF 缓存

## 二、代码同步

```bash
cd report-assembly-agent
export SSHPASS='<密码>'
rsync -az --delete \
  --exclude '.venv' --exclude '.git' --exclude '__pycache__' \
  --exclude 'runtime' --exclude '.env' --exclude '*.pyc' --exclude 'report.db' \
  -e "ssh -o StrictHostKeyChecking=no" ./ \
  zhangruqi@<目标机IP>:/home/nas511/zhangruqi/agent-server/
```

## 三、环境安装(二选一)

### A. pip 直装(目标机能上网)

```bash
ssh <目标机>
cd /home/nas511/zhangruqi/agent-server
python3 -m venv .venv
TMPDIR=/home/nas511/zhangruqi/.pip_tmp .venv/bin/pip install -r requirements.txt
```

注意:目标机 /tmp 常被大文件占满(pip 会报 Errno 28)。TMPDIR 必须指向大盘。

### B. 从源机搬运 site-packages(目标机无法访问 pypi)

```bash
# 源机(108)执行,~5.8G
export SSHPASS='<密码>'
rsync -az --exclude '__pycache__' \
  /home/nas511/zhangruqi/agent/.venv/lib/python3.12/site-packages/ \
  zhangruqi@<目标机IP>:/home/nas511/zhangruqi/agent-server/.venv/lib/python3.12/site-packages/
# 新增依赖(源机没有的)单独装:sqlalchemy 等
pip download sqlalchemy==2.0.52 --platform manylinux2014_x86_64 --python-version 312 \
  --only-binary=:all: -d /tmp/wheels/   # 在能上网的机器上
scp /tmp/wheels/*.whl <目标机>:/home/nas511/zhangruqi/agent-server/wheels/
ssh <目标机> "cd .../agent-server && .venv/bin/pip install --no-index --find-links=wheels/ sqlalchemy"
```

## 四、ollama 模型

```bash
# 源机(108)执行:模型在服务用户目录(/home/cs928/.ollama),19G
export SSHPASS='<密码>'
scp -r /home/cs928/.ollama/models zhangruqi@<目标机IP>:/home/nas511/zhangruqi/ollama_models/

# 目标机放置(需 sudo;sudo 密码=登录密码,用 askpass 方式)
ssh <目标机>
cat > /tmp/askpass.sh <<'EOF'
#!/bin/sh
echo '<sudo密码>'
EOF
chmod +x /tmp/askpass.sh
export SUDO_ASKPASS=/tmp/askpass.sh
sudo -A mkdir -p /usr/share/ollama/.ollama
sudo -A cp -r /home/nas511/zhangruqi/ollama_models/models /usr/share/ollama/.ollama/models
sudo -A rm -rf /usr/share/ollama/.ollama/models/models      # 去重嵌套
sudo -A chown -R ollama:ollama /usr/share/ollama/.ollama
sudo -A systemctl restart ollama
ollama list   # 应有 qwen-agent/qwen-embed/glm-ocr/qwen3.6/qwen3-embedding
```

## 五、HF 缓存(docling 解析模型,离线必需)

```bash
# 源机(108):/home/zhangruqi/.cache/huggingface,506M
scp -r /home/zhangruqi/.cache/huggingface zhangruqi@<目标机IP>:/home/zhangruqi/.cache/
```

## 六、qdrant

```bash
# 二进制 + 配置从源机拷(静态单文件)
scp /home/nas511/zhangruqi/qdrant /home/nas511/zhangruqi/qdrant_config.yaml <目标机>:<agent-server>/ 

# 目标机启动(ulimit 必须,否则 fd 耗尽)
cd <agent-server>
(setsid bash -c 'ulimit -n 1048576; exec ./qdrant --config-path qdrant_config.yaml' > /tmp/qdrant.log 2>&1 < /dev/null &)
curl -s http://127.0.0.1:6333/healthz   # healthz check passed
```

## 七、.env(env.example 为模板)

关键项:
```
IRA_GENERATION_MODEL=qwen-agent:latest
IRA_EMBEDDING_MODEL=qwen-embed:latest
IRA_OLLAMA_URL=http://127.0.0.1:11434
IRA_QDRANT_URL=http://127.0.0.1:6333
IRA_VECTOR_BACKEND=qdrant
HF_HUB_OFFLINE=1        # 离线用缓存,必须已拷贝 HF 缓存
IRA_TORCH_COMPILE=true
IRA_GPU_MEMORY_TIGHT=true
IRA_GPU_LAYERS=999
```

### 知识图谱 Neo4j

Neo4j 只保存 PostgreSQL 中已验证关系的可重建投影，不保存材料正文或替代事实库。

```bash
cd <agent-server>
cp deploy/neo4j.env.example .neo4j.env
# 编辑 .neo4j.env，设置强密码；不要提交该文件
set -a; . ./.neo4j.env; set +a
NEO4J_PASSWORD="$NEO4J_PASSWORD" docker compose \
  --env-file .neo4j.env -f deploy/neo4j-compose.yml up -d

cat >> .env <<'EOF'
IRA_NEO4J_URI=bolt://127.0.0.1:7687
IRA_NEO4J_USER=neo4j
IRA_NEO4J_PASSWORD=<same-password>
IRA_NEO4J_DATABASE=neo4j
IRA_GRAPH_MODE=shadow
EOF
```

先以 `shadow` 跑真实任务，检查 `/api/tasks/<task_id>/graph` 的实体、关系和 Fact 绑定；确认质量后改为 `IRA_GRAPH_MODE=active`。Neo4j 不可用时，系统自动退回 Hybrid RAG，报告生成不受阻塞。

## 八、启动应用

```bash
ssh <目标机> "cd <agent-server> && nohup .venv/bin/python run.py > /tmp/app.log 2>&1 < /dev/null &"
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/tasks   # 200
```

诊断:启动即 ImportError sqlalchemy = site-packages 正在被 rsync 覆盖(等 rsync 完再启动)。

## 九、材料 + 任务

```bash
# 材料(61 份)与模板先放目标机 runtime/materials/ 与 runtime/templates/
# 模板分析(生成变体 id):
curl -s -X POST http://127.0.0.1:8000/api/style/analyze -F 'files=@<模板.docx>'
# 建任务(61 份全选):
cd <agent-server>/runtime/materials
CMD="curl -s -X POST http://127.0.0.1:8000/api/tasks -F 'theme=...' -F 'variant_id=1'"
for f in *; do CMD="$CMD -F 'files=@$f'"; done
eval "$CMD"   # {"task_id":"...","material_count":61}
curl -s -X POST http://127.0.0.1:8000/api/tasks/<id>/run
```

## 十、监控

```bash
# 本地部署 cron(每 10 分钟):monitor.sh 需在 ~/.hermes/scripts/ 下
# 输出:stage / parse / evidence / 模型放置 / API
```

## 十一、故障排查(已踩坑)

| 现象 | 根因 | 修复 |
|------|------|------|
| 解析 40/61 失败,错误 RapidOCR 不支持 ch_sim | docling 新版 onnxruntime 语言码为 'ch' | docling_adapter 按后端分流(lang=['ch','en']) |
| LocalEntryNotFoundError(HF 离线) | docling 布局模型未缓存 | 拷贝 108 的 ~/.cache/huggingface(506M) |
| pip Errno 28 | /tmp 被大文件塞满 | TMPDIR 指大盘;模型别拷 /tmp |
| 后台启动即 ImportError sqlalchemy | site-packages rsync 覆盖中 | 等 rsync 完再启动;rsync 不带 --delete 安全 |
| setsid 后台启动间歇失败 | 同上前提(覆盖窗口) | 重试即可,或用 nohup |
| qdrant 版本不匹配警告(qdrant_client 1.19 vs server 1.12) | client 新 server 旧 | 功能正常,忽略或对齐版本 |

## 十二、上线后验证清单

- [ ] ollama list 5 个模型
- [ ] curl qdrant healthz
- [ ] API /api/tasks = 200
- [ ] /api/style/analyze 模板变体 id=1
- [ ] 解析 61/61 成功(UI 或 file_parse_profiles 全 success)
- [ ] llm_call_logs 有 material_analyzer/planner/evidence 调用
- [ ] facts/clusters/fact_relations 增长(evidence → intelligence)
