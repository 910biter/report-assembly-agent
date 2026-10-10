"""Shared model configuration for user-facing collaboration agents."""
from __future__ import annotations
import json
import httpx

from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from app.config import settings
from app.model_connection import load_connection


async def _legacy_output_parameter(request: httpx.Request) -> None:
    """Pydantic AI emits max_completion_tokens; adapt only the configured wire name."""
    if request.url.path.endswith("/chat/completions"):
        body = json.loads(request.content)
        if "max_completion_tokens" in body:
            body["max_tokens"] = body.pop("max_completion_tokens")
            encoded = json.dumps(body).encode()
            request.stream = httpx.ByteStream(encoded)
            request.headers["Content-Length"] = str(len(encoded))


def build_collaboration_model() -> OpenAIChatModel:
    """Build the interactive model without coupling it to a workflow stage."""
    connection = load_connection()
    provider = OpenAIProvider(
        base_url=connection.base_url,
        api_key=connection.api_key or "local-vllm",
        http_client=httpx.AsyncClient(
            timeout=settings.gateway_timeout_seconds,
            event_hooks={"request": [_legacy_output_parameter]} if connection.output_parameter == "max_tokens" else {},
        ),
    )
    return OpenAIChatModel(
        connection.model_name,
        provider=provider,
        settings=OpenAIChatModelSettings(
            max_tokens=min(int(settings.interactive_output_tokens), connection.output_tokens),
            temperature=0.2 if connection.send_temperature else None,
            parallel_tool_calls=False,
            extra_body=connection.extra_body(False),
        ),
    )
