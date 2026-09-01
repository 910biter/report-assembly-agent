"""Pydantic AI agent for report requirements before a task is created."""
from __future__ import annotations

from dataclasses import dataclass, field
import json

from pydantic_ai import Agent, ModelRetry, RunContext, UsageLimits

from app.collaboration_model import build_collaboration_model


_SYSTEM_PROMPT = """你是报告整编系统的任务需求助手。你负责在任务提交前，与用户讨论并形成可执行的报告主题和完整报告要求。

工作原则：
1. 理解自然语言意图，不要求用户配置内部参数。用户说“你来想、帮我写、一般怎么写”时，应结合当前主题和对话主动完成专业判断。
2. 只有报告对象完全无法判断时才追问；受众、侧重点、篇幅、文风等常规信息可以先给出合理建议，交由用户审阅。
3. 用户要求代拟、整理、完善或修改需求时，必须调用 propose_task_draft，不能只声称“已拟定”而不提供正文。
4. 需求草案应按当前任务实际需要说明：报告目标与用途、核心问题和内容重点、范围与证据边界、预期篇幅与表达方式、真实性和溯源要求。不要机械套用固定行业目录。
5. 用户只是咨询时可以直接回答；讨论中的建议不等于修改草稿。
6. 工具只形成待用户确认的草案，不代表任务已经创建。调用工具后简要提示用户审阅，不重复粘贴整份草案。
7. 不展示 JSON、字段名、模型参数或内部工作流术语。
"""


@dataclass
class TaskDraftAgentDeps:
    current: dict
    user_message: str
    proposed: dict | None = None
    tool_trace: list[str] = field(default_factory=list)

    def prepare(self, theme: str, requirements: str) -> dict:
        theme = str(theme or "").strip()
        requirements = str(requirements or "").strip()
        if not theme:
            raise ModelRetry("需求草案必须包含明确的报告主题；请根据上下文归纳，确实无法判断时再向用户询问。")
        if len(requirements) < 40:
            raise ModelRetry("报告要求过于简略，请形成用户可以直接采用的完整需求正文。")
        self.proposed = {"theme": theme, "requirements": requirements}
        self.tool_trace.append("propose_task_draft")
        return {"prepared": True, "theme": theme, "requirements_ready": True}


@dataclass(frozen=True)
class TaskDraftAgentRunResult:
    reply: str
    proposal_after: dict | None
    usage: dict
    tool_trace: list[str]


def _build_agent() -> Agent[TaskDraftAgentDeps, str]:
    agent = Agent(
        build_collaboration_model(),
        deps_type=TaskDraftAgentDeps,
        output_type=str,
        system_prompt=_SYSTEM_PROMPT,
        retries=2,
        name="task_draft_agent",
    )

    @agent.tool
    def propose_task_draft(
        ctx: RunContext[TaskDraftAgentDeps],
        theme: str,
        requirements: str,
    ) -> dict:
        """形成完整任务需求草案，供用户在界面中审阅、接受或拒绝。"""
        return ctx.deps.prepare(theme, requirements)

    return agent


def run_task_draft_agent(
    current: dict,
    user_message: str,
    recent_history: list[dict[str, str]],
) -> TaskDraftAgentRunResult:
    deps = TaskDraftAgentDeps(current=dict(current or {}), user_message=user_message)
    history_text = "\n".join(
        f"{('用户' if item.get('role') == 'user' else '助手')}：{item.get('content', '')}"
        for item in recent_history
    )
    prompt = (
        f"当前任务草稿：{json.dumps(current or {}, ensure_ascii=False)}\n"
        f"最近对话：\n{history_text or '无'}\n\n"
        f"用户本轮消息：{user_message}\n"
        "请理解用户本轮真实意图，并决定直接回答或调用工具形成完整需求草案。"
    )
    result = _build_agent().run_sync(
        prompt,
        deps=deps,
        usage_limits=UsageLimits(request_limit=4, tool_calls_limit=2),
    )
    usage = result.usage
    return TaskDraftAgentRunResult(
        reply=str(result.output or "").strip(),
        proposal_after=dict(deps.proposed) if deps.proposed else None,
        usage={
            "requests": usage.requests,
            "tool_calls": usage.tool_calls,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
        },
        tool_trace=list(deps.tool_trace),
    )
