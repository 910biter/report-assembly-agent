"""Single administrator-managed OpenAI-compatible connection, stored in PG."""
from dataclasses import dataclass
import json
import hashlib
import threading
import time
import tempfile
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db import session_scope
from app.infrastructure.orm import Base

TABLE = Base.metadata.tables["model_connection"]
_cache_lock = threading.RLock()
_cached: tuple[float, "Connection"] | None = None


@dataclass(frozen=True)
class Connection:
    base_url: str
    model_name: str
    api_key: str = ""
    service_kind: str = "openai"
    context_tokens: int = 18432
    output_tokens: int = 8192
    output_parameter: str = "max_tokens"
    send_temperature: bool = True
    vllm_priority: bool = False
    send_json_response_format: bool = False

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def extra_body(self, think: bool | None = None) -> dict:
        if self.service_kind != "vllm":
            return {}
        from app.llm_queue import current_llm_priority
        body = {"priority": current_llm_priority()} if self.vllm_priority else {}
        if think is not None:
            body["chat_template_kwargs"] = {"enable_thinking": bool(think), "preserve_thinking": False}
        return body

    @property
    def identity(self) -> str:
        # No key or credential hash is exposed in cache/audit identities.
        return hashlib.sha256(json.dumps(self.options() | {"base_url": self.base_url, "model_name": self.model_name}, sort_keys=True).encode()).hexdigest()[:20]

    def options(self) -> dict:
        return {name: getattr(self, name) for name in (
            "service_kind", "context_tokens", "output_tokens",
            "output_parameter", "send_temperature", "vllm_priority", "send_json_response_format")}


def _cipher(create: bool = True) -> Fernet:
    # Encryption key is deployment state, not a model configuration file.
    path = Path(settings.runtime_root) / "secrets" / "model_connection.key"
    if not create and not path.is_file():
        raise RuntimeError("MODEL_ENCRYPTION_KEY_MISSING")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        fd, temporary = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(Fernet.generate_key())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            os.unlink(temporary)
    return Fernet(path.read_bytes())


def load_connection() -> Connection:
    global _cached
    with _cache_lock:
        if _cached is not None and time.monotonic() - _cached[0] < 1:
            return _cached[1]
        value = _read_connection()
        _cached = (time.monotonic(), value)
        return value


def _read_connection() -> Connection:
    with session_scope() as session:
        row = session.execute(select(TABLE).where(TABLE.c.id == 1)).mappings().first()
    if row is None:
        raise ValueError("MODEL_CONNECTION_NOT_CONFIGURED")
    encrypted = row["encrypted_api_key"]
    key = _cipher(create=False).decrypt(encrypted.encode()).decode() if encrypted else ""
    options = json.loads(row["options_json"])
    options.pop("tokenizer_path", None)  # Remove the retired setting when reading existing records.
    return Connection(row["base_url"], row["model_name"], key, **options)


def public_connection() -> dict:
    try:
        connection = load_connection()
    except ValueError:
        return {"configured": False, "base_url": "", "model_name": "", "has_api_key": False, **Connection("", "").options()}
    return {"configured": True, "base_url": connection.base_url,
            "model_name": connection.model_name, "has_api_key": bool(connection.api_key), **connection.options()}


def model_name() -> str:
    return public_connection()["model_name"]


def connection_identity() -> str:
    try:
        return load_connection().identity
    except ValueError:
        return "unconfigured"


def capacity() -> tuple[int, int]:
    try:
        connection = load_connection()
    except ValueError:
        return int(settings.model_context_window_tokens), int(settings.generation_reserve_tokens)
    from app.vllm_capacity import window
    return window(connection), connection.output_tokens


def connection_from_payload(payload: dict) -> Connection:
    url = str(payload.get("base_url") or "").strip().rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("接入地址必须是无账号、查询参数的 HTTP(S) 地址")
    name = str(payload.get("model_name") or "").strip()
    if not name or len(name) > 256:
        raise ValueError("请填写有效的模型名称")
    key = payload.get("api_key")
    if key is None:
        try:
            key = load_connection().api_key
        except ValueError:
            key = ""
    kind = str(payload.get("service_kind") or "vllm")
    parameter = str(payload.get("output_parameter") or "max_tokens")
    if kind not in {"openai", "vllm"} or parameter not in {"max_tokens", "max_completion_tokens"}:
        raise ValueError("不支持的服务类型或输出参数")
    try:
        window = int(payload.get("context_tokens", 0))
        output = int(payload.get("output_tokens", 8192))
    except (ValueError, TypeError):
        raise ValueError("上下文和输出上限必须为整数") from None
    if not (window == 0 or 4096 <= window <= 2_000_000) or not 256 <= output <= 2_000_000 or (window and output >= window - 2048):
        raise ValueError("容量不合理：上下文为0（自动）或至少4096，输出至少256且需预留输入空间")
    return Connection(url, name, str(key).strip(), kind, window, output,
                      parameter, bool(payload.get("send_temperature", True)),
                      bool(payload.get("vllm_priority", False)), bool(payload.get("send_json_response_format", False)))


def save_connection(connection: Connection) -> None:
    global _cached
    encrypted = _cipher().encrypt(connection.api_key.encode()).decode() if connection.api_key else ""
    values = dict(id=1, base_url=connection.base_url, model_name=connection.model_name,
                  encrypted_api_key=encrypted, options_json=json.dumps(connection.options()))
    with session_scope() as session:
        # Serialize against the persistent main-workflow claim transaction.
        from sqlalchemy import func
        from app.workflow.queue_store import _CLAIM_LOCK
        session.execute(select(func.pg_advisory_xact_lock(_CLAIM_LOCK)))
        queue = Base.metadata.tables["task_queue_entries"]
        if session.execute(select(queue.c.id).where(queue.c.status == "running").limit(1)).first():
            raise ValueError("任务正在运行，不能切换模型接入")
        graph_jobs = Base.metadata.tables["graph_jobs"]
        if session.execute(select(graph_jobs.c.job_id).where(graph_jobs.c.status == "running").limit(1)).first():
            raise ValueError("图谱正在构建，不能切换模型接入")
        session.execute(insert(TABLE).values(**values).on_conflict_do_update(
            index_elements=[TABLE.c.id], set_={k: v for k, v in values.items() if k != "id"}))
    with _cache_lock:
        _cached = (time.monotonic(), connection)
    from app.context_budget import token_counter
    token_counter.reset()
    from app.vllm_capacity import reset
    reset()


def test_connection(connection: Connection) -> dict:
    # Verify generation, not just /models, which some compatible services omit.
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        response = client.post(f"{connection.base_url}/chat/completions", headers=connection.headers(), json={
            "model": connection.model_name, "messages": [{"role": "user", "content": "Reply OK."}],
            connection.output_parameter: 32, **connection.extra_body(False),
        })
        response.raise_for_status()
        body = response.json()
        if not body.get("choices"):
            raise ValueError("接口未返回标准 Chat Completions 响应")
    from app.vllm_capacity import status
    return {"connected": True, "model_available": True, "usage_available": bool(body.get("usage")), **status(connection)}
