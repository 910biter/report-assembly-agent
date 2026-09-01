"""Shared model configuration for user-facing collaboration agents."""
from __future__ import annotations

from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from app.config import settings
from app.llm_queue import current_llm_priority


def build_collaboration_model() -> OpenAIChatModel:
    """Build the interactive model without coupling it to a workflow stage."""
    provider = OpenAIProvider(
        base_url=(settings.generation_url or "http://127.0.0.1:8100/v1").rstrip("/"),
        api_key=settings.generation_api_key or "local-vllm",
    )
    return OpenAIChatModel(
        settings.generation_model,
        provider=provider,
        settings=OpenAIChatModelSettings(
            max_tokens=int(settings.interactive_output_tokens),
            temperature=0.2,
            parallel_tool_calls=False,
            extra_body={
                "priority": current_llm_priority(),
                "chat_template_kwargs": {
                    "enable_thinking": False,
                    "preserve_thinking": False,
                },
            },
        ),
    )
