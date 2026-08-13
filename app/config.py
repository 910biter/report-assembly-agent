"""应用配置:从环境变量(IRA_ 前缀)或 .env 读取。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IRA_",
        env_file=str(_PROJECT_ROOT / ".env"),
        extra="ignore",
    )

    runtime_root: Path = _PROJECT_ROOT / "runtime"
    ollama_url: str = "http://100.120.119.108:11434"
    generation_model: str = "qwen-agent:latest"
    embedding_model: str = "qwen-embed:latest"
    ocr_model: str = "glm-ocr:latest"
    ocr_timeout_seconds: int = 60
    describe_timeout_seconds: int = 60
    gateway_timeout_seconds: int = 600
    max_context_chars: int = 12000  # 模型上下文物理上限(字符),用于动态截取,不预设内容决策
    vector_backend: str = "auto"  # auto / sqlite / qdrant
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection_units: str = "ira_units"
    qdrant_collection_materials: str = "ira_materials"
    qdrant_collection_facts: str = "ira_facts"

    @property
    def db_path(self) -> Path:
        return self.runtime_root / "report.db"

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
