# Docker 离线部署

三容器架构:app(本仓库)+ qdrant(向量检索)+ ollama(推理/embedding)。

## 快速启动(在线环境)

```bash
# 1. 预装推理与 embedding 模型(首次,模型进 ollama_models 卷)
docker compose up -d qdrant ollama
docker exec ira-ollama ollama pull qwen-agent:latest
docker exec ira-ollama ollama pull qwen-embed:latest

# 2. 构建并启动应用
docker compose up -d --build app

# 3. 验证
curl http://localhost:8000/          # UI
curl http://localhost:8000/api/health
```

## 离线部署准备(在有网机器上完成,再迁移)

1. **pip 依赖**:`pip download -r requirements.txt -d wheels/`(torch 用 CPU 源:
   `pip download torch --index-url https://download.pytorch.org/whl/cpu -d wheels/`)
   Dockerfile 中 `pip install --no-cache-dir -r requirements.txt` 改为
   `pip install --no-cache-dir --find-links=/wheels -r requirements.txt`(wheels/ 随镜像或卷带入)。

2. **Docling 模型**(layout/table/OCR):有网机器先跑一次解析,缓存写入
   `~/.cache/huggingface`,整体打包;或构建时预下载。

3. **Ollama 模型**:`ollama pull qwen-agent:latest` 后卷 `ollama_models` 整体打包
   (模型文件在 `/root/.ollama/models`)。目标机导入:`docker run -v ollama_models:/root/.ollama ollama/ollama ollama list` 验证。

4. **镜像导出**:`docker save qdrant/qdrant ollama/ollama ira-app -o images.tar`
   目标机 `docker load < images.tar`。

5. **中文字体/系统依赖**:已在 Dockerfile 内置(libreoffice-writer + fonts-noto-cjk),
   离线构建需 apt 缓存或 base 镜像预装。

## GPU 加速(可选)

ollama 容器加:
```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: 1
          capabilities: [gpu]
```
torch(Docling)GPU:Dockerfile 的 torch 源换 cu121,并 `--gpus all` 运行 app 容器。

## 数据与迁移

- `runtime/`(report.db、报告、模板)与 `materials/`(材料)以卷挂载,迁移 = 拷贝目录。
- 环境变量前缀 `IRA_`(见 app/config.py),compose 已配置服务间地址。

## 说明

- 首次解析会触发 Docling 模型加载(2-5 分钟,一次性);进程内后续解析复用。
- 27.8B 推理模型需要足够显存(量化后 ~16GB+);无 GPU 时生成极慢,建议用小模型。
- run.py 默认 127.0.0.1,容器内由 IRA_HOST=0.0.0.0 覆盖。
