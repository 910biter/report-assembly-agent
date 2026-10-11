FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/ira \
    HF_HOME=/opt/models/hf

RUN sed -i 's|http://archive.ubuntu.com/ubuntu/|http://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
      /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates ffmpeg libgomp1 python3.12 python3.12-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3.12 -m venv /opt/venv

WORKDIR /opt/app
COPY requirements.txt deploy/docker/cpu-constraints.txt ./
ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN PIP_INDEX_URL="$PYPI_INDEX_URL" python -m pip install --upgrade pip \
    && python -m pip install --index-url "$PYPI_INDEX_URL" \
       --find-links https://mirrors.aliyun.com/pytorch-wheels/cpu/ \
       'torch==2.14.0+cpu' 'torchvision==0.29.0+cpu' \
    && PIP_INDEX_URL="$PYPI_INDEX_URL" python -m pip install -c cpu-constraints.txt -r requirements.txt \
    && python -m pip check \
    && python -c 'import docling, whisper, torch, onnxruntime, rapidocr, fastapi, psycopg, qdrant_client'

COPY app ./app
COPY run.py ./run.py
COPY web ./web
COPY deploy/docker/entrypoint.sh ./entrypoint.sh

ARG EMBEDDING_REPO=Qwen/Qwen3-Embedding-0.6B
ARG EMBEDDING_REVISION=main
ARG ASR_MODEL=medium
RUN mkdir -p /opt/models/embedding /opt/models/docling /home/ira/.cache/whisper \
    && EMBEDDING_REPO="$EMBEDDING_REPO" EMBEDDING_REVISION="$EMBEDDING_REVISION" ASR_MODEL="$ASR_MODEL" \
       python -c 'import os; from pathlib import Path; from huggingface_hub import snapshot_download; from docling.utils.model_downloader import download_models; import whisper; snapshot_download(repo_id=os.environ["EMBEDDING_REPO"], revision=os.environ["EMBEDDING_REVISION"], local_dir="/opt/models/embedding"); download_models(output_dir=Path("/opt/models/docling"), with_code_formula=False, with_picture_classifier=False, rapidocr_models=["onnxruntime:ch"]); whisper.load_model(os.environ["ASR_MODEL"], device="cpu", download_root="/home/ira/.cache/whisper")' \
    && test -f /opt/models/embedding/config.json \
    && test -n "$(find /opt/models/docling -type f -print -quit)" \
    && test -n "$(find /home/ira/.cache/whisper -type f -print -quit)" \
    && mkdir -p /home/ira/.cache/docling \
    && ln -s /opt/models/docling /home/ira/.cache/docling/models

RUN useradd --uid 10001 --home-dir /home/ira ira \
    && mkdir -p /opt/app/runtime \
    && chown -R ira:ira /opt/app/runtime /home/ira

USER ira
EXPOSE 8000
CMD ["bash", "/opt/app/entrypoint.sh"]
