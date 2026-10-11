"""Shared model configuration for user-facing collaboration agents."""
from __future__ import annotations
import json
import httpx
import asyncio

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
            # httpx caches content separately from its transport stream.
            request._content = encoded
            request.headers["Content-Length"] = str(len(encoded))


def build_collaboration_model() -> OpenAIChatModel:
    """Build the interactive model without coupling it to a workflow stage."""
    connection = load_connection()
    async def verify(request: httpx.Request) -> None:
        if not request.url.path.endswith("/chat/completions"):
            return
        if connection.output_parameter == "max_tokens":
            await _legacy_output_parameter(request)
        from app.vllm_capacity import verify_request
        audit = await asyncio.to_thread(verify_request, connection, json.loads(request.content), int(settings.safety_margin_tokens))
        request.extensions["model_capacity"] = audit
    async def observe(response: httpx.Response) -> None:
        if not response.request.url.path.endswith("/chat/completions") or response.status_code != 200:
            return
        payload = json.loads(response.request.content)
        if payload.get("stream"):
            return  # Never consume a live SSE response in a transport hook.
        await response.aread()
        from app.vllm_capacity import observe_usage
        observe_usage(connection, payload, response.json().get("usage") or {})
    provider = OpenAIProvider(
        base_url=connection.base_url,
        api_key=connection.api_key or "local-vllm",
        http_client=httpx.AsyncClient(
            timeout=settings.gateway_timeout_seconds,
            event_hooks={"request": [verify], "response": [observe]},
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
