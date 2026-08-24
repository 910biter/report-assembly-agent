"""应用配置:从环境变量(IRA_ 前缀)或 .env 读取。"""
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IRA_",
        env_file=str(_PROJECT_ROOT / ".env"),
        extra="ignore",
    )

    hf_offline: bool = True  # HF 离线模式(IRA_HF_OFFLINE):模型全本地化,禁止联网检查/下载
    torch_compile: bool = False  # torch.compile 编译(IRA_TORCH_COMPILE):缺 python3-dev 时编译必败,默认禁用走 eager
    gpu_memory_tight: bool = False  # 显存紧张模式(IRA_GPU_MEMORY_TIGHT):重资源阶段(解析)自动卸载推理模型
    gpu_layers: int = -1  # Ollama num_gpu(IRA_GPU_LAYERS):-1=auto, 0=CPU, >0=显式 GPU 层数(统一模型放置策略)
    db_url: str = ""  # PG 连接串(IRA_DB_URL, postgresql+psycopg://...);空 = 默认本地 PG(ira/ira@127.0.0.1:5432/ira)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # transformers/huggingface_hub 读 OS 环境变量:启动即注入,避免远端 HF 不可达挂起
        if self.hf_offline:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        # HF_HOME 非 IRA_ 前缀,pydantic 不读;手动从 .env 读取并注入 os.environ,
        # 供 docling/transformers 定位离线模型缓存(否则落到默认路径 → LocalEntryNotFoundError)。
        env_file = _PROJECT_ROOT / ".env"
        if os.environ.get("HF_HOME") is None and env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("HF_HOME="):
                    val = line.split("=", 1)[1].strip().strip("'\"")
                    if val:
                        os.environ["HF_HOME"] = val
                    break
        # torch.compile 依赖系统 python3-dev(gcc 编译 Python.h);缺失时 InductorError 反复重试卡死
        if not self.torch_compile:
            os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")
            os.environ.setdefault("TORCHINDUCTOR_CACHE_DISABLE", "1")

    runtime_root: Path = _PROJECT_ROOT / "runtime"
    host: str = "127.0.0.1"  # 服务监听地址(IRA_HOST,支持 .env;容器/远端部署设 0.0.0.0)
    port: int = 8000  # 服务监听端口(IRA_PORT)
    ollama_url: str = "http://100.120.119.108:11434"
    generation_model: str = "qwen-agent:latest"
    embedding_model: str = "qwen-embed:latest"
    # Parser and multimodal extraction are Docling-only. The project no longer
    # maintains a separate OCR/ASR/Vision provider path.
    docling_ocr_engine: str = "rapidocr"  # rapidocr(PP-OCRv6,满血)/ auto / easyocr / tesseract
    docling_ocr_model: str = "medium"  # rapidocr 模型档位: tiny/small/medium(满血默认 medium)
    docling_device: str = "cpu"  # Docling layout/table/OCR/ASR device: cuda / cpu
    docling_ocr_cuda: bool = False  # OCR 使用 CPU，避免依赖 CUDAExecutionProvider
    asr_device: str = "cpu"  # ASR device: cuda / cpu; 当前与 OCR 一样保持 CPU
    asr_model: str = "medium"  # ASR whisper 档位: tiny/base/small/medium/large(默认 medium:中文质量高且 CPU 可跑)
    asr_language: str = "zh"  # ASR 转写语言(默认中文;空=whisper 自动检测)
    gateway_timeout_seconds: int = 900
    max_context_chars: int = 12000  # 模型上下文物理上限(字符),用于动态截取,不预设内容决策
    # 上下文容量配置(物理上限派生,非内容决策):
    # 单批可用 tokens = 窗口 - 输出预留 - 固定 prompt 开销 - 安全余量
    model_context_window_tokens: int = 65536  # 远端模型上下文窗口
    generation_reserve_tokens: int = 8192  # 单次模型输出上限/预留，覆盖 Evidence 与小节成文
    prompt_overhead_tokens: int = 3000  # 固定 prompt(系统提示+维度+insights 头)
    safety_margin_tokens: int = 2048  # 安全余量(防估算偏差)
    writer_min_budget_completion_ratio: float = 0.8  # 规模 QA 阈值,不授权虚构或重复补齐
    vector_backend: str = "auto"  # auto / qdrant / off
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection_units: str = "ira_units"
    qdrant_collection_materials: str = "ira_materials"
    qdrant_collection_facts: str = "ira_facts"

    # PostgreSQL stores the auditable canonical graph. Neo4j is an optional,
    # rebuildable query projection and must never become a second fact source.
    graph_mode: str = "off"  # off / shadow / active
    graph_workspace_id: str = "default"
    graph_build_before_analysis: bool = True
    graph_max_hops: int = 2
    neo4j_uri: str = ""  # e.g. bolt://127.0.0.1:7687
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    @property
    def materials_dir(self) -> Path:
        return self.runtime_root / "materials"

    @property
    def reports_dir(self) -> Path:
        return self.runtime_root / "reports"

    @property
    def templates_dir(self) -> Path:
        return self.runtime_root / "templates"

    def ensure_dirs(self) -> None:
        for path in (self.runtime_root, self.materials_dir, self.reports_dir, self.templates_dir):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
