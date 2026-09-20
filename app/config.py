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
    generation_url: str = ""  # OpenAI-compatible base URL, e.g. http://127.0.0.1:8100/v1
    generation_api_key: str = ""
    generation_model: str = "qwen-agent:latest"
    generation_tokenizer_path: str = ""  # 本地生成模型 tokenizer 目录；用于精确上下文装箱
    context_tokenizer_local_only: bool = True  # 禁止上下文计量在运行时访问外网
    embedding_backend: str = "transformers-cpu"
    embedding_model: str = "Qwen3-Embedding-0.6B"
    embedding_model_path: str = "/home/nas511/zhangruqi/models/Qwen3-Embedding-0.6B"
    embedding_batch_size: int = 2
    embedding_max_length: int = 8192
    embedding_cpu_threads: int = 8
    embedding_query_instruction: str = "Retrieve relevant evidence passages for the current report analysis question"
    llm_concurrency: int = 1  # OpenAI-compatible serving continuous batching入口并发
    evidence_batch_concurrency: int = 1  # 独立 Evidence 批次并发；仅 vLLM 部署建议设为2
    # 同一物理模型上的交互控制通道。交互请求不进入长工作流的本地队列，
    # 但仍通过独立信号量限制并发，并由 vLLM priority scheduler 统一调度。
    interactive_concurrency: int = 1
    # The copilot must carry enough authoritative task state and conversation
    # history to resolve colloquial follow-ups without guessing workflow data.
    interactive_input_tokens: int = 8192
    interactive_history_tokens: int = 3072
    interactive_output_tokens: int = 2048
    interactive_request_limit: int = 15  # 协作助手最多进行的模型往返次数
    interactive_tool_calls_limit: int = 30  # 协作助手最多进行的工具调用次数
    comparison_output_tokens: int = 3200
    style_probe_output_tokens: int = 256
    style_profile_output_tokens: int = 4096
    material_analysis_input_tokens: int = 4000
    material_analysis_output_tokens: int = 2048
    planner_output_tokens: int = 3072
    analysis_output_tokens: int = 3072
    analysis_inference_token_budget: int = 384  # Analysis 单条推断的资源估算，不是领域规则
    analysis_facts_per_batch: int = 45  # 资源边界；语义分组仍由运行时维度与事实关系决定
    narrative_output_tokens: int = 4096
    narrative_qa_output_tokens: int = 2048
    qa_output_tokens: int = 2048
    conflict_output_tokens: int = 2048
    # Parser and multimodal extraction are Docling-only. The project no longer
    # maintains a separate OCR/ASR/Vision provider path.
    docling_device: str = "cpu"  # Docling layout/table/OCR/ASR device: cuda / cpu
    asr_device: str = "cpu"  # ASR device: cuda / cpu; 当前与 OCR 一样保持 CPU
    asr_model: str = "medium"  # ASR whisper 档位: tiny/base/small/medium/large(默认 medium:中文质量高且 CPU 可跑)
    asr_language: str = "zh"  # ASR 转写语言(默认中文;空=whisper 自动检测)
    ocr_languages: str = "ch,en"  # OCR 语言列表(逗号分隔,如 ch,en;空=后端默认)
    gateway_timeout_seconds: int = 900
    # 上下文容量配置(物理上限派生,非内容决策):
    # 单批可用 tokens = 窗口 - 输出预留 - 固定 prompt 开销 - 安全余量
    # RTX 3090 + 27B GPTQ baseline: 18,432 leaves activation headroom for
    # long planning requests. Keep this aligned with vLLM max-model-len.
    model_context_window_tokens: int = 18432
    generation_reserve_tokens: int = 8192  # 单次模型输出上限/预留，覆盖 Evidence 与小节成文
    structured_output_tokens: int = 3072  # Planner/Analysis/QA 等结构化阶段默认输出预算
    final_planner_output_tokens: int = 8192  # 完整章节契约优先保留输出空间；24K 窗口仍保留输入与安全余量
    writer_output_tokens: int = 3072  # 单个 Narrative subsection 的正文输出预算
    writer_visible_word_token_ratio: float = 0.30  # JSON+引用绑定后的保守可见正文容量
    evidence_output_tokens: int = 5120  # Evidence 高密度批次需要比普通结构化阶段更大的输出空间
    evidence_first_pass_input_tokens: int = 160000  # 首轮 Evidence 总输入资源边界
    evidence_gap_input_tokens: int = 60000  # 单轮缺口检索输入资源边界
    # Exact model tokenization is available in production. Keep a measured
    # guard band instead of reserving several thousand tokens twice: prompt
    # sections are already counted by the context packer.
    prompt_overhead_tokens: int = 1024  # chat template/system prompt allowance
    safety_margin_tokens: int = 1536  # tokenizer drift and gateway protection
    writer_min_budget_completion_ratio: float = 0.8  # 规模 QA 阈值,不授权虚构或重复补齐
    vector_backend: str = "auto"  # auto / qdrant / off
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection_units: str = "ira_units"
    qdrant_collection_materials: str = "ira_materials"
    qdrant_collection_facts: str = "ira_facts"

    # PostgreSQL stores the auditable canonical graph. Neo4j is an optional,
    # rebuildable query projection and must never become a second fact source.
    graph_workspace_id: str = "default"
    graph_build_before_analysis: bool = False
    graph_max_hops: int = 2
    graph_output_tokens: int = 6144
    graph_facts_per_batch: int = 12
    graph_batch_concurrency: int = 2
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
