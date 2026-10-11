"""Pydantic AI agent for report requirements before a task is created."""
from __future__ import annotations

from dataclasses import dataclass, field
import json

from pydantic_ai import Agent, ModelRetry, RunContext, UsageLimits

from app.collaboration_model import build_collaboration_model


_SYSTEM_PROMPT = """你是报告整编系统的任务需求助手，帮助用户在提交任务前形成可执行的主题和报告要求。

工作方法：
1. 先理解本轮意图。咨询时直接解释；要求代拟、整理或修改需求时调用 propose_task_draft，提供完整草案供用户确认。用户将常规选择交给你时，结合已知内容主动提出受众、重点、篇幅和文风建议；只有报告对象确实无法确定时才追问。
2. 涉及当前配置、材料或模板时，先调用 read_current_setup。需要模板内容时调用 read_selected_template，需要材料清单时调用 read_selected_materials。工具结果是当前选择的依据；只有文件名的待上传材料，应如实说明尚无正文可供判断。
3. 草案围绕本次任务的目标、核心问题、内容重点、证据范围、篇幅和表达方式组织；遵守用户明确约束，并让真实性和溯源要求可执行。结构由具体材料与用途决定。
4. propose_task_draft 只准备待确认草案。调用后简要引导用户审阅；平常回答采用用户可读的语言，不展示内部字段或协议。"""


@dataclass
class TaskDraftAgentDeps:
    current: dict
    user_message: str
    owner_user_id: int | None = None
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

    def read_setup(self) -> dict:
        self.tool_trace.append("read_current_setup")
        selection = self.current.get("template") if isinstance(self.current.get("template"), dict) else {}
        materials = self.current.get("materials") if isinstance(self.current.get("materials"), list) else []
        return {
            "theme": str(self.current.get("theme") or ""),
            "requirements": str(self.current.get("requirements") or ""),
            "workflow_mode": str(self.current.get("workflowMode") or "automatic"),
            "template_selected": bool(selection.get("id")),
            "template_name": str(selection.get("name") or ""),
            "using_default_template": selection.get("mode") == "default" or not selection.get("id"),
            "materials": _material_setup_rows(materials),
        }

    def read_template(self) -> dict:
        self.tool_trace.append("read_selected_template")
        return _selected_template_profile(self.current, owner_user_id=self.owner_user_id)

    def read_materials(self) -> dict:
        self.tool_trace.append("read_selected_materials")
        rows = _material_setup_rows(self.current.get("materials"))
        return {
            "materials": rows,
            "notice": "待上传文件尚未进入解析流程，当前只能确认文件名；材料库文件将在创建任务后按所选任务读取。",
        }


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
    def read_current_setup(ctx: RunContext[TaskDraftAgentDeps]) -> dict:
        """读取用户当前选择的主题、要求、工作方式、模板与材料清单。"""
        return ctx.deps.read_setup()

    @agent.tool
    def read_selected_template(ctx: RunContext[TaskDraftAgentDeps]) -> dict:
        """读取当前所选模板的真实文档形态、文体、组织和材料表达画像。"""
        return ctx.deps.read_template()

    @agent.tool
    def read_selected_materials(ctx: RunContext[TaskDraftAgentDeps]) -> dict:
        """读取当前已选材料清单及其是否已经可供解析的状态。"""
        return ctx.deps.read_materials()

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
    owner_user_id: int | None = None,
) -> TaskDraftAgentRunResult:
    draft_current = dict(current or {})
    deps = TaskDraftAgentDeps(current=draft_current, user_message=user_message,
                              owner_user_id=owner_user_id)
    history_text = "\n".join(
        f"{('用户' if item.get('role') == 'user' else '助手')}：{item.get('content', '')}"
        for item in recent_history
    )
    prompt = (
        "当前草稿仅提供主题、报告要求和工作方式。模板与材料详情不在提示中，"
        "需要时必须调用对应工具读取。\n"
        f"主题：{str(draft_current.get('theme') or '').strip() or '尚未填写'}\n"
        f"报告要求：{str(draft_current.get('requirements') or '').strip() or '尚未填写'}\n"
        f"工作方式：{str(draft_current.get('workflowMode') or 'automatic')}\n"
        f"最近对话：\n{history_text or '无'}\n\n"
        f"用户本轮消息：{user_message}\n"
        "请理解用户本轮真实意图，并决定直接回答或调用工具形成完整需求草案。"
    )
    result = _build_agent().run_sync(
        prompt,
        deps=deps,
        usage_limits=UsageLimits(request_limit=5, tool_calls_limit=4),
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


def _selected_template_profile(current: dict | None,
                               owner_user_id: int | None = None) -> dict:
    """Read a compact authoritative profile only when the agent asks for it."""
    selection = current.get("template") if isinstance(current, dict) and isinstance(current.get("template"), dict) else {}
    try:
        variant_id = int(selection.get("id") or 0)
    except (TypeError, ValueError):
        variant_id = 0
    if not variant_id:
        return {"selected": False, "message": "当前未选择特定模板，任务将使用系统默认模板。"}
    from app.memory import style
    from app.config import settings

    if settings.multiuser_enabled and owner_user_id is None:
        raise ValueError("TEMPLATE_OWNER_REQUIRED")

    variant = (style.get_variant(variant_id, owner_user_id=owner_user_id)
               if owner_user_id is not None else style.get_variant(variant_id))
    if variant is None:
        return {"selected": False, "message": "所选模板已不可用，请重新选择。"}
    structure = variant.structure if isinstance(variant.structure, dict) else {}
    return {
        "selected": True,
        "name": variant.name,
        "document_shape": structure.get("document_shape") or {},
        "asset_roles": structure.get("asset_roles") or {},
        "writing_style": variant.writing_style or {},
        "writing_patterns": variant.writing_patterns or {},
        "material_realization": (
            variant.writing_patterns.get("material_realization")
            if isinstance(variant.writing_patterns, dict)
            else {}
        ) or {},
    }


def _material_setup_rows(value) -> list[dict]:
    result: list[dict] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "")
        result.append({
            "filename": str(item.get("filename") or ""),
            "source": source,
            "status": "等待上传解析" if source == "待上传文件" else "已从材料库选择",
        })
    return result
