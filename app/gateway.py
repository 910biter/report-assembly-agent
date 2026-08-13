"""模型网关:业务代码访问模型的唯一入口。

当前实现为 Ollama 兼容协议客户端,指向远端模型服务
(settings.ollama_url)。远端接口文件如有变化,只改本文件。
"""
import base64
import json
import re
import threading
from typing import Protocol

import httpx

from app.config import settings

_GENERATION_STATS_LOCK = threading.Lock()
_GENERATION_STATS = {
    "chat_calls": 0,
    "prompt_tokens": 0,
    "output_tokens": 0,
    "prompt_eval_seconds": 0.0,
    "output_eval_seconds": 0.0,
    "total_seconds": 0.0,
}
_LAST_GENERATION_META = {
    "returned_chars": 0,
    "valid_json_chars": 0,
}


class ModelGateway(Protocol):
    def generate(self, prompt: str, system: str | None = None) -> str: ...
    def generate_json(self, prompt: str, system: str | None = None) -> dict: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def ocr(self, image_bytes: bytes) -> str: ...
    def describe_image(self, image_bytes: bytes, prompt: str) -> str: ...
    def health(self) -> dict: ...


class OllamaGateway:
    def __init__(self, base_url: str = settings.ollama_url) -> None:
        self.base_url = base_url.rstrip("/")

    def generate(self, prompt: str, system: str | None = None) -> str:
        return self._chat(prompt, system=system, json_mode=False)

    def _chat(self, prompt: str, system: str | None = None, json_mode: bool = False) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {"model": settings.generation_model, "messages": messages, "stream": False}
        if json_mode:
            payload["format"] = "json"
        response = httpx.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=settings.gateway_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        _record_generation_stats(payload)
        content = payload["message"]["content"].strip()
        _record_generation_meta(returned_chars=len(content), valid_json_chars=0)
        return content

    def generate_json(self, prompt: str, system: str | None = None) -> dict:
        # Do not enable Ollama's global JSON format by default. In real runs it
        # reduced output tokens but made structured stages much slower; this
        # project already gets high JSON compliance from prompts, and we repair
        # common truncation glitches below.
        content = self._chat(prompt, system, json_mode=False)
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
        if fenced:
            content = fenced.group(1)
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end < start:
            raise ValueError("MODEL_JSON_NOT_FOUND")
        json_text = content[start : end + 1]
        try:
            value = json.loads(json_text)
        except json.JSONDecodeError:
            repaired = _repair_json_text(json_text)
            value = json.loads(repaired)
            json_text = repaired
        if not isinstance(value, dict):
            raise TypeError("MODEL_JSON_OBJECT_REQUIRED")
        meta = last_generation_meta()
        _record_generation_meta(
            returned_chars=int(meta.get("returned_chars") or 0),
            valid_json_chars=len(json_text),
        )
        return value

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = httpx.post(
            f"{self.base_url}/api/embed",
            json={"model": settings.embedding_model, "input": texts},
            timeout=180,
        )
        response.raise_for_status()
        embeddings = response.json()["embeddings"]
        if len(embeddings) != len(texts):
            raise RuntimeError("EMBEDDING_COUNT_MISMATCH")
        return embeddings

    def ocr(self, image_bytes: bytes) -> str:
        response = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": settings.ocr_model,
                "messages": [{
                    "role": "user",
                    "content": "识别页面中的全部文字,按阅读顺序输出纯文本,不要解释。",
                    "images": [base64.b64encode(image_bytes).decode("ascii")],
                }],
                "stream": False,
                "options": {"temperature": 0, "num_gpu": 0},
            },
            timeout=settings.ocr_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()["message"]["content"].strip()

    def describe_image(self, image_bytes: bytes, prompt: str) -> str:
        response = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": settings.generation_model,
                "messages": [{
                    "role": "user",
                    "content": prompt,
                    "images": [base64.b64encode(image_bytes).decode("ascii")],
                }],
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=settings.describe_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()["message"]["content"].strip()

    def health(self) -> dict:
        version_response = httpx.get(f"{self.base_url}/api/version", timeout=2)
        tags_response = httpx.get(f"{self.base_url}/api/tags", timeout=5)
        version_response.raise_for_status()
        tags_response.raise_for_status()
        available = {item["name"] for item in tags_response.json().get("models", [])}
        required = [settings.generation_model, settings.embedding_model, settings.ocr_model]
        return {
            "version": version_response.json().get("version"),
            "models": {name: name in available for name in required},
        }


model_gateway: ModelGateway = OllamaGateway()


def _repair_json_text(text: str) -> str:
    """Best-effort repair for common non-streaming local-model JSON glitches."""
    value = (text or "").strip()
    value = re.sub(r",\s*([}\]])", r"\1", value)
    stack = []
    in_string = False
    escaped = False
    for ch in value:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()
    if in_string:
        value += '"'
    while stack:
        value += "}" if stack.pop() == "{" else "]"
    return value


def reset_generation_stats() -> None:
    with _GENERATION_STATS_LOCK:
        for key in _GENERATION_STATS:
            _GENERATION_STATS[key] = 0 if key.endswith("calls") or key.endswith("tokens") else 0.0


def generation_stats() -> dict:
    with _GENERATION_STATS_LOCK:
        stats = dict(_GENERATION_STATS)
    output_seconds = float(stats.get("output_eval_seconds") or 0.0)
    prompt_seconds = float(stats.get("prompt_eval_seconds") or 0.0)
    total_seconds = float(stats.get("total_seconds") or 0.0)
    output_tokens = int(stats.get("output_tokens") or 0)
    prompt_tokens = int(stats.get("prompt_tokens") or 0)
    stats["output_tokens_per_second"] = round(output_tokens / output_seconds, 2) if output_seconds else 0.0
    stats["prompt_tokens_per_second"] = round(prompt_tokens / prompt_seconds, 2) if prompt_seconds else 0.0
    stats["overall_tokens_per_second"] = (
        round((prompt_tokens + output_tokens) / total_seconds, 2) if total_seconds else 0.0
    )
    return stats


def last_generation_meta() -> dict:
    with _GENERATION_STATS_LOCK:
        return dict(_LAST_GENERATION_META)


def _record_generation_meta(returned_chars: int, valid_json_chars: int = 0) -> None:
    with _GENERATION_STATS_LOCK:
        _LAST_GENERATION_META["returned_chars"] = int(returned_chars or 0)
        _LAST_GENERATION_META["valid_json_chars"] = int(valid_json_chars or 0)


def _record_generation_stats(payload: dict) -> None:
    """Collect Ollama token counters when available."""
    prompt_tokens = int(payload.get("prompt_eval_count") or 0)
    output_tokens = int(payload.get("eval_count") or 0)
    prompt_seconds = float(payload.get("prompt_eval_duration") or 0) / 1_000_000_000
    output_seconds = float(payload.get("eval_duration") or 0) / 1_000_000_000
    total_seconds = float(payload.get("total_duration") or 0) / 1_000_000_000
    with _GENERATION_STATS_LOCK:
        _GENERATION_STATS["chat_calls"] += 1
        _GENERATION_STATS["prompt_tokens"] += prompt_tokens
        _GENERATION_STATS["output_tokens"] += output_tokens
        _GENERATION_STATS["prompt_eval_seconds"] = round(
            float(_GENERATION_STATS["prompt_eval_seconds"]) + prompt_seconds, 3
        )
        _GENERATION_STATS["output_eval_seconds"] = round(
            float(_GENERATION_STATS["output_eval_seconds"]) + output_seconds, 3
        )
        _GENERATION_STATS["total_seconds"] = round(
            float(_GENERATION_STATS["total_seconds"]) + total_seconds, 3
        )
