# syntax=docker/dockerfile:1
# 报告整编 Agent 应用镜像(离线部署)
# 架构:app(本镜像)+ qdrant(向量)+ ollama(推理/embedding)三容器
FROM python:3.14-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    IRA_HOST=0.0.0.0

# 系统依赖:
#  - libreoffice-writer:Docling 解析 .doc 老格式的后端
#  - fonts-noto-cjk:中文渲染(导出 docx/LibreOffice 转换必需)
#  - fontconfig:字体注册
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        fonts-noto-cjk \
        fontconfig \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# torch CPU 版单独装(避免拉 GPU 版;离线部署可改为本地 wheel 缓存)
# 有 GPU 时替换为: --index-url https://download.pytorch.org/whl/cu121
RUN pip install --no-cache-dir \
    "torch" --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY . .

# 数据卷:runtime(库/报告/模板)、materials(上传材料)、HF 模型缓存(Docling)
VOLUME ["/app/runtime", "/app/materials", "/root/.cache/huggingface"]

EXPOSE 8000

CMD ["python", "run.py"]
