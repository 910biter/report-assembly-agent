"""Pydantic AI task copilot with audited, task-scoped workflow tools.

The agent may read persisted artifacts in a bounded tool loop. Mutating tools
only prepare an ``AgentToolCall``; the existing ChangeProposal approval and
candidate-version workflow remains the sole execution path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Literal

from pydantic_ai import Agent, ModelRetry, RunContext, UsageLimits

from app.collaboration_model import build_collaboration_model
from app.config import settings
from app.context_budget import count_tokens
from app.control_agent import (
    AgentToolCall,
    AgentToolName,
    TaskAgentContext,
    execute_read_tool,
    validate_agent_tool_call,
)
from app.rendering.headings import strip_heading_prefix


_SYSTEM_PROMPT = """你是报告整编系统的任务协作助手。根据用户本轮意图，读取权威产物，解释现状或准备待确认的修改。

工作方法：
1. 先区分咨询、依据核验、提出修改和确认继续。咨询时读取相关产物后直接回答；明确要求修改或确认上一条建议时调用相应 propose_* 工具。工具只准备提案，用户接受后才执行；prepared 后简要提示核对。
2. 按问题选择读取工具。任务全貌先看 read_task_map 或相应索引；模板用 read_task_template；材料概览用 read_materials。用户要求原文位置、直接引文或逐项核验时，继续调用 search_material_units 或 read_fact_evidence，依据实际返回的文件、页码和段落回答；若检索结果不能定位，就明确说明未找到可核对位置，而不是只引用摘要文件名。具体事实和句子的依据用 search_fact_evidence、read_fact_evidence。页面引用只用于定位，先用 read_current_focus 核对真实对象。工具结果截断时说明已覆盖范围并按需继续检索。
3. requirement_review_pending 阶段先读材料理解结果；用户委托拟定需求时，按真实材料形成完整草案并调用 propose_task_requirements。directory_review_pending 阶段读取最终目录并讨论结构；修改目录时形成完整可执行提案。用户要求在检查点确认并继续时调用 propose_checkpoint_continue。
4. 章节拆分、合并使用专用结构工具；批量修改章节标题使用 propose_section_titles 提交一张覆盖全部目标的提案。已有确认决定是持续约束；新指示与之不同，先说明变化，再按最新明确要求准备提案。
5. 事实、推论、目录、正文和质量判断以任务工具结果为依据；保持真实性、溯源与任务隔离。简单问题直接答，复杂问题清晰分点，用户界面只呈现可读结论与依据。
6. 材料理解摘要是阅读线索，不是逐条核验的原文证据。依据工具返回的 coverage/scope 说明已理解几份材料、当前检索了多少可用单元；不能把搜索未命中说成全部材料都没有。
7. coverage.completeness 为 unknown 时，必须明确说“阅读范围未记录，覆盖完整性无法确认”；不得表述为“全部读完”“无缺失”或“无受限材料”。limited_context 大于 0 时说明部分材料只分析了部分单元；只有 completeness 为 known 且没有未理解材料时，才可说明理解输入覆盖已记录的材料单元。"""


@dataclass
class TaskAgentDeps:
    context: TaskAgentContext
    user_message: str
    pending_action: AgentToolCall | None = None
    tool_trace: list[str] = field(default_factory=list)
    read_cache: dict[str, dict] = field(default_factory=dict)

    def read(self, name: AgentToolName, arguments: dict | None = None) -> dict:
        arguments = arguments or {}
        cache_key = f"{name.value}:{json.dumps(arguments, ensure_ascii=False, sort_keys=True)}"
        result = self.read_cache.get(cache_key)
        if result is None:
            result = execute_read_tool(name, arguments, self.context)
            self.read_cache[cache_key] = result
        self.tool_trace.append(name.value)
        return result

    def propose(self, call: AgentToolCall) -> dict:
        normalized = validate_agent_tool_call(call, self.context, self.user_message)
        if self.pending_action is not None and self.pending_action != normalized:
            raise ModelRetry("一次回复只能形成一个明确修改动作，请合并为单一提案。")
        self.pending_action = normalized
        self.tool_trace.append(normalized.tool_name.value)
        return {
            "prepared": True,
            "action": normalized.tool_name.value,
            "summary": _action_summary(normalized),
        }


@dataclass(frozen=True)
class TaskAgentRunResult:
    reply: str
    tool_call: AgentToolCall | None
    usage: dict
    tool_trace: list[str]


def _build_agent() -> Agent[TaskAgentDeps, str]:
    agent = Agent(
        build_collaboration_model(),
        deps_type=TaskAgentDeps,
        output_type=str,
        system_prompt=_SYSTEM_PROMPT,
        retries=2,
        name="task_collaboration_agent",
    )

    @agent.tool
    def read_task_overview(ctx: RunContext[TaskAgentDeps]) -> dict:
        """读取当前任务的真实状态、进度和产物数量。"""
        return ctx.deps.read(AgentToolName.GET_TASK_OVERVIEW)

    @agent.tool
    def read_task_map(ctx: RunContext[TaskAgentDeps]) -> dict:
        """读取任务全景：材料主题、规划摘要、事实和推论概览、报告结构及待处理事项。"""
        return ctx.deps.read(AgentToolName.GET_TASK_MAP)

    @agent.tool
    def read_task_template(ctx: RunContext[TaskAgentDeps]) -> dict:
        """读取当前任务实际使用模板的文体、结构策略和 Word 版式边界。"""
        return ctx.deps.read(AgentToolName.GET_TASK_TEMPLATE)

    @agent.tool
    def read_current_focus(ctx: RunContext[TaskAgentDeps]) -> dict:
        """按当前选中对象的 ID 重新读取任务内真实内容、引用和来源。"""
        return ctx.deps.read(AgentToolName.GET_FOCUSED_ARTIFACT)

    @agent.tool
    def read_final_report_plan(ctx: RunContext[TaskAgentDeps], include_details: bool = False) -> dict:
        """读取当前有效的最终报告目录；仅在需要逐项讨论章节契约时读取详情。"""
        return ctx.deps.read(AgentToolName.GET_FINAL_PLAN, {"include_details": include_details})

    @agent.tool
    def read_analysis_plan(ctx: RunContext[TaskAgentDeps], include_details: bool = False) -> dict:
        """读取当前有效的分析维度和分析规划；仅在需要逐项讨论时读取详情。"""
        return ctx.deps.read(AgentToolName.GET_ANALYSIS_PLAN, {"include_details": include_details})

    @agent.tool
    def read_report_outline(ctx: RunContext[TaskAgentDeps]) -> dict:
        """读取报告章节、段落、事实引用和推论引用概览。"""
        return ctx.deps.read(AgentToolName.GET_REPORT_OUTLINE)

    @agent.tool
    def read_report_section(ctx: RunContext[TaskAgentDeps], chapter_title: str,
                            offset: int = 0, limit: int = 60) -> dict:
        """按真实章节标题读取正文段落、句子 ID 和对应事实/推论引用。"""
        return ctx.deps.read(AgentToolName.GET_REPORT_SECTION, {
            "chapter_title": chapter_title, "offset": offset, "limit": limit,
        })

    @agent.tool
    def read_report_sentence(ctx: RunContext[TaskAgentDeps], sentence_id: int) -> dict:
        """读取某一报告句子的正文、所在段落和事实/推论引用。"""
        return ctx.deps.read(AgentToolName.GET_REPORT_SENTENCE, {"sentence_id": sentence_id})

    @agent.tool
    def read_fact_evidence(ctx: RunContext[TaskAgentDeps], fact_id: int) -> dict:
        """读取当前任务内指定事实及其原始材料证据。"""
        return ctx.deps.read(AgentToolName.GET_FACT_EVIDENCE, {"fact_id": fact_id})

    @agent.tool
    def browse_facts(ctx: RunContext[TaskAgentDeps], query: str = "", dimension: str = "",
                     offset: int = 0, limit: int = 12) -> dict:
        """浏览任务事实摘要。先用此工具找到相关事实，再调用 read_fact_evidence 核验原始依据。"""
        return ctx.deps.read(AgentToolName.LIST_FACTS, {
            "query": query, "dimension": dimension, "offset": offset, "limit": limit,
        })

    @agent.tool
    def read_inference(ctx: RunContext[TaskAgentDeps], inference_id: int) -> dict:
        """读取指定推论、置信度和依据事实。"""
        return ctx.deps.read(AgentToolName.GET_INFERENCE, {"inference_id": inference_id})

    @agent.tool
    def browse_inferences(ctx: RunContext[TaskAgentDeps], query: str = "", dimension: str = "",
                          confidence: str = "", offset: int = 0, limit: int = 12) -> dict:
        """浏览任务分析判断摘要及其依据事实。"""
        return ctx.deps.read(AgentToolName.LIST_INFERENCES, {
            "query": query, "dimension": dimension, "confidence": confidence,
            "offset": offset, "limit": limit,
        })

    @agent.tool
    def read_conflicts(ctx: RunContext[TaskAgentDeps]) -> dict:
        """读取当前任务的冲突双方、来源和核验状态。"""
        return ctx.deps.read(AgentToolName.GET_CONFLICTS)

    @agent.tool
    def read_quality_issues(ctx: RunContext[TaskAgentDeps], offset: int = 0, limit: int = 12) -> dict:
        """读取当前报告质量问题及其正文定位。"""
        return ctx.deps.read(AgentToolName.GET_QUALITY_ISSUES, {"offset": offset, "limit": limit})

    @agent.tool
    def locate_quality_issue(ctx: RunContext[TaskAgentDeps], issue_index: int) -> dict:
        """定位质量问题在在线报告中的对应句子或章节。序号从 0 开始。"""
        return ctx.deps.read(AgentToolName.LOCATE_QUALITY_ISSUE, {"issue_index": issue_index})

    @agent.tool
    def read_materials(ctx: RunContext[TaskAgentDeps]) -> dict:
        """读取任务材料及已落库的材料理解摘要、关键点和缺失信息。"""
        return ctx.deps.read(AgentToolName.GET_MATERIALS)

    @agent.tool
    def search_material_units(ctx: RunContext[TaskAgentDeps], query: str,
                              material_id: int = 0, limit: int = 6) -> dict:
        """按问题检索当前任务材料的原始片段，返回文件、页码、段落与正文摘录。"""
        return ctx.deps.read(AgentToolName.SEARCH_MATERIAL_UNITS, {
            "query": query, "material_id": material_id, "limit": limit,
        })

    @agent.tool
    def search_fact_evidence(ctx: RunContext[TaskAgentDeps], query: str, limit: int = 6) -> dict:
        """按问题混合检索任务事实及其原始证据，用于回答“依据是什么、是否支持”。"""
        return ctx.deps.read(AgentToolName.SEARCH_FACT_EVIDENCE, {"query": query, "limit": limit})

    @agent.tool
    def propose_split_chapter(
        ctx: RunContext[TaskAgentDeps],
        chapter_reference: str,
        replacement_titles: list[str],
    ) -> dict:
        """将一个现有章节拆分为多个新章节，其他章节由程序原样保留。"""
        titles = _current_titles(ctx.deps)
        index = _resolve_chapter_index(titles, chapter_reference)
        replacements = _clean_titles(replacement_titles)
        if len(replacements) < 2:
            raise ModelRetry("拆分章节至少需要两个明确的新章节标题。")
        structure = [*titles[:index], *replacements, *titles[index + 1:]]
        return ctx.deps.propose(AgentToolCall(
            tool_name=AgentToolName.RERUN_FINAL_PLAN,
            arguments={
                "instruction": ctx.deps.user_message,
                "new_structure": structure,
                "required_chapter_count": len(structure),
            },
            confirmation_required=True,
            reason=f"将“{titles[index]}”拆分为 {len(replacements)} 章，其他章节保持不变",
        ))

    @agent.tool
    def propose_merge_chapters(
        ctx: RunContext[TaskAgentDeps],
        chapter_references: list[str],
        merged_title: str,
    ) -> dict:
        """合并连续的现有章节，未涉及章节由程序原样保留。"""
        titles = _current_titles(ctx.deps)
        indices = sorted({_resolve_chapter_index(titles, item) for item in chapter_references})
        if len(indices) < 2 or indices != list(range(indices[0], indices[-1] + 1)):
            raise ModelRetry("只能合并两个或以上连续章节，请明确目标章节。")
        title = strip_heading_prefix(str(merged_title)).strip()
        if not title:
            raise ModelRetry("合并后的章节标题不能为空。")
        structure = [*titles[:indices[0]], title, *titles[indices[-1] + 1:]]
        return ctx.deps.propose(AgentToolCall(
            tool_name=AgentToolName.RERUN_FINAL_PLAN,
            arguments={
                "instruction": ctx.deps.user_message,
                "new_structure": structure,
                "required_chapter_count": len(structure),
            },
            confirmation_required=True,
            reason=f"合并 {len(indices)} 个连续章节，其他章节保持不变",
        ))

    @agent.tool
    def propose_replace_report_structure(
        ctx: RunContext[TaskAgentDeps],
        new_structure: list[str],
    ) -> dict:
        """仅当用户明确确认完整新目录时，提议整体替换报告结构。"""
        structure = _clean_titles(new_structure)
        if not structure:
            raise ModelRetry("用户尚未确认完整目录，不能整体替换报告结构。")
        return ctx.deps.propose(AgentToolCall(
            tool_name=AgentToolName.RERUN_FINAL_PLAN,
            arguments={
                "instruction": ctx.deps.user_message,
                "new_structure": structure,
                "required_chapter_count": len(structure),
            },
            confirmation_required=True,
            reason="按用户明确确认的完整目录重组报告",
        ))

    @agent.tool
    def propose_replan_report_structure(
        ctx: RunContext[TaskAgentDeps], required_chapter_count: int = 0,
    ) -> dict:
        """用户只确认结构目标或章节数量时，调用 Final Planner 重新规划目录。"""
        return ctx.deps.propose(AgentToolCall(
            tool_name=AgentToolName.RERUN_FINAL_PLAN,
            arguments={
                "instruction": ctx.deps.user_message,
                "new_structure": [],
                "required_chapter_count": max(0, int(required_chapter_count or 0)),
            },
            confirmation_required=True,
            reason="按用户确认的结构目标调用 Final Planner 重新规划目录",
        ))

    @agent.tool
    def propose_chapter_rewrite(
        ctx: RunContext[TaskAgentDeps], chapter_reference: str,
    ) -> dict:
        """调用 Narrative Plan 与 Writer 重写一个真实存在的章节。"""
        titles = _current_titles(ctx.deps)
        index = _resolve_chapter_index(titles, chapter_reference)
        return ctx.deps.propose(AgentToolCall(
            tool_name=AgentToolName.REGENERATE_CHAPTER,
            arguments={"chapter_title": titles[index], "instruction": ctx.deps.user_message},
            confirmation_required=True,
            reason=f"按要求重写“{titles[index]}”",
        ))

    @agent.tool
    def propose_task_requirements(
        ctx: RunContext[TaskAgentDeps], updated_theme: str,
        updated_requirements: str,
    ) -> dict:
        """更新完整任务主题和报告要求，并从分析规划开始重算。"""
        return ctx.deps.propose(AgentToolCall(
            tool_name=AgentToolName.REVISE_TASK_REQUIREMENTS,
            arguments={
                "updated_theme": updated_theme,
                "updated_requirements": updated_requirements,
                "instruction": ctx.deps.user_message,
            },
            confirmation_required=True,
            reason="更新任务目标与完整报告要求",
        ))

    @agent.tool
    def propose_analysis_plan(
        ctx: RunContext[TaskAgentDeps],
        required_dimensions: list[str] | None = None,
        required_dimension_count: int = 0,
    ) -> dict:
        """调用 Planner 调整分析问题、维度和证据需求。"""
        return ctx.deps.propose(AgentToolCall(
            tool_name=AgentToolName.REVISE_ANALYSIS_PLAN,
            arguments={
                "instruction": ctx.deps.user_message,
                "required_dimensions": required_dimensions or [],
                "required_dimension_count": required_dimension_count,
            },
            confirmation_required=True,
            reason="调整分析规划及证据方向",
        ))

    @agent.tool
    def propose_fact_recheck(ctx: RunContext[TaskAgentDeps], fact_id: int) -> dict:
        """回到 Evidence 复核指定事实和原始证据。"""
        return ctx.deps.propose(AgentToolCall(
            tool_name=AgentToolName.RECHECK_FACT,
            arguments={"fact_id": fact_id, "instruction": ctx.deps.user_message},
            confirmation_required=True,
            reason=f"复核事实 {fact_id} 及其证据",
        ))

    @agent.tool
    def propose_inference_recheck(
        ctx: RunContext[TaskAgentDeps], inference_id: int,
    ) -> dict:
        """回到 Analysis 复核指定推论和依据事实。"""
        return ctx.deps.propose(AgentToolCall(
            tool_name=AgentToolName.RECHECK_INFERENCE,
            arguments={"inference_id": inference_id, "instruction": ctx.deps.user_message},
            confirmation_required=True,
            reason=f"复核推论 {inference_id} 及其依据",
        ))

    @agent.tool
    def propose_focused_rewrite(ctx: RunContext[TaskAgentDeps]) -> dict:
        """对用户在报告中选中的句子或段落形成定点 Writer 修改提案。"""
        focus = ctx.deps.context.current_focus
        if focus.artifact_type == "sentence":
            name = AgentToolName.REWRITE_SENTENCE
            arguments = {"sentence_id": focus.object_id, "instruction": ctx.deps.user_message}
        elif focus.artifact_type == "paragraph":
            current = focus.current or {}
            name = AgentToolName.REWRITE_PARAGRAPH
            arguments = {
                "chapter_title": current.get("section"),
                "paragraph": current.get("paragraph"),
                "sentence_ids": current.get("sentence_ids") or [],
                "instruction": ctx.deps.user_message,
            }
        else:
            raise ModelRetry("请先在报告中选择需要修改的句子或段落。")
        return ctx.deps.propose(AgentToolCall(
            tool_name=name,
            arguments=arguments,
            confirmation_required=True,
            reason="定点修改当前选中的正文内容",
        ))

    @agent.tool
    def propose_report_title(
        ctx: RunContext[TaskAgentDeps], new_title: str,
    ) -> dict:
        """修改报告主标题；只形成待确认提案。"""
        return ctx.deps.propose(AgentToolCall(
            tool_name=AgentToolName.UPDATE_REPORT_TITLE,
            arguments={"new_title": new_title, "instruction": ctx.deps.user_message},
            confirmation_required=True,
            reason="修改报告主标题",
        ))

    @agent.tool
    def propose_section_title(
        ctx: RunContext[TaskAgentDeps], chapter_reference: str, new_title: str,
    ) -> dict:
        """修改一个现有章节标题并同步目录；不改写章节正文。"""
        titles = _current_titles(ctx.deps)
        index = _resolve_chapter_index(titles, chapter_reference)
        title = strip_heading_prefix(str(new_title)).strip()
        if not title:
            raise ModelRetry("新的章节标题不能为空。")
        return ctx.deps.propose(AgentToolCall(
            tool_name=AgentToolName.UPDATE_SECTION_TITLE,
            arguments={
                "old_title": titles[index], "new_title": title,
                "instruction": ctx.deps.user_message,
            },
            confirmation_required=True,
            reason=f"将章节“{titles[index]}”改为“{title}”",
        ))

    @agent.tool
    def propose_section_titles(
        ctx: RunContext[TaskAgentDeps], changes: list[dict[str, str]],
    ) -> dict:
        """批量修改两个及以上现有章节标题并同步目录；每项均需包含 old_title 和 new_title。"""
        return ctx.deps.propose(AgentToolCall(
            tool_name=AgentToolName.UPDATE_SECTION_TITLES,
            arguments={"changes": changes, "instruction": ctx.deps.user_message},
            confirmation_required=True,
            reason=f"批量修改 {len(changes)} 个章节标题",
        ))

    @agent.tool
    def propose_task_control(
        ctx: RunContext[TaskAgentDeps], action: Literal["pause", "resume", "retry"],
    ) -> dict:
        """形成暂停、恢复或重试当前任务的待确认操作。"""
        mapping = {
            "pause": AgentToolName.PAUSE_TASK,
            "resume": AgentToolName.RESUME_TASK,
            "retry": AgentToolName.RETRY_TASK,
        }
        return ctx.deps.propose(AgentToolCall(
            tool_name=mapping[action], arguments={}, confirmation_required=True,
            reason={"pause": "暂停当前任务", "resume": "恢复当前任务", "retry": "重试当前任务"}[action],
        ))

    @agent.tool
    def propose_checkpoint_continue(
        ctx: RunContext[TaskAgentDeps], checkpoint: Literal["requirements", "directory"],
        feedback: str = "",
    ) -> dict:
        """确认当前需求或目录检查点并继续工作流；仍须由用户接受这张提案。"""
        context = ctx.deps.context
        if checkpoint == "requirements":
            if not context.requirement_review_pending:
                raise ModelRetry("当前不在需求确认阶段，不能确认需求并继续。")
            if not context.theme or not context.user_requirements:
                raise ModelRetry("当前尚未保存完整主题和报告要求，请先形成并接受需求提案。")
            tool_name, reason = AgentToolName.CONFIRM_REQUIREMENTS, "确认任务需求并继续分析规划"
        else:
            if not context.directory_review_pending:
                raise ModelRetry("当前不在目录确认阶段，不能确认目录并继续。")
            tool_name, reason = AgentToolName.CONFIRM_DIRECTORY, "确认当前目录并继续叙事组织和写作"
        return ctx.deps.propose(AgentToolCall(
            tool_name=tool_name,
            arguments={"feedback": str(feedback or "").strip()} if checkpoint == "directory" else {},
            confirmation_required=True,
            reason=reason,
        ))

    return agent


def run_task_collaboration_agent(
    context: TaskAgentContext,
    user_message: str,
    recent_history: list[dict[str, str]],
    decision_memory: dict | None = None,
) -> TaskAgentRunResult:
    deps = TaskAgentDeps(context=context, user_message=user_message)
    # The interaction lane has a smaller context envelope than the report
    # pipeline. Bound all non-tool prompt fields so a large selection or old
    # decision record cannot starve the agent's tool calls.
    history_text = "\n".join(
        f"{('用户' if item.get('role') == 'user' else '助手')}：{item.get('content', '')}"
        for item in recent_history
    )
    focus_payload = {
        "artifact_type": context.current_focus.artifact_type,
        "object_id": context.current_focus.object_id,
        "title": context.current_focus.title,
        "artifact_version": context.current_focus.artifact_version,
        "has_references": bool(context.current_focus.references),
    }
    memory_payload = decision_memory or {}
    input_budget = max(1024, int(settings.interactive_input_tokens))
    current_message_tokens = count_tokens(user_message)
    if current_message_tokens > input_budget - max(512, input_budget // 4):
        raise ValueError("USER_MESSAGE_EXCEEDS_INTERACTIVE_CONTEXT")
    background_budget = max(256, input_budget - current_message_tokens - 512)
    prompt = (
        f"当前任务主题：{_cap_prompt_text(context.theme, background_budget // 8)}\n"
        f"当前报告要求摘要：{_cap_prompt_text(context.user_requirements, background_budget // 3)}\n"
        f"可读取的任务产物：{_cap_prompt_json(context.available_artifacts, 192)}\n"
        f"当前页面焦点：{_cap_prompt_json(focus_payload, background_budget // 8)}\n"
        f"当前任务阶段：{context.stage_label}\n"
        f"结构化决策记忆：{_cap_prompt_json(memory_payload, background_budget // 5)}\n"
        f"最近对话：\n{_cap_prompt_text(history_text, background_budget // 5) or '无'}\n\n"
        f"用户本轮消息：{user_message}"
    )
    result = _run_agent(prompt, deps)
    tool_trace = list(deps.tool_trace)
    usage = result.usage
    return TaskAgentRunResult(
        reply=str(result.output or "").strip(),
        tool_call=deps.pending_action,
        usage={
            "requests": usage.requests,
            "tool_calls": usage.tool_calls,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
        },
        tool_trace=tool_trace,
    )


def _run_agent(prompt: str, deps: TaskAgentDeps):
    return _build_agent().run_sync(
        prompt,
        deps=deps,
        usage_limits=UsageLimits(
            request_limit=settings.interactive_request_limit,
            tool_calls_limit=settings.interactive_tool_calls_limit,
        ),
    )


def _cap_prompt_json(value: object, token_budget: int) -> str:
    return _cap_prompt_text(json.dumps(value, ensure_ascii=False), token_budget)


def _cap_prompt_text(value: object, token_budget: int) -> str:
    text = str(value or "")
    if token_budget <= 0 or count_tokens(text) <= token_budget:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if count_tokens(text[:middle]) <= token_budget:
            low = middle
        else:
            high = middle - 1
    return text[:low] + "\n[已截断，详情请通过读取工具获取]"


def _current_titles(deps: TaskAgentDeps) -> list[str]:
    payload = deps.read(AgentToolName.GET_FINAL_PLAN)
    titles = _clean_titles(payload.get("titles") or [])
    if not titles:
        raise ModelRetry("当前任务尚未形成最终目录，不能执行章节结构修改。")
    return titles


def _clean_titles(values: list[str]) -> list[str]:
    return list(dict.fromkeys(
        strip_heading_prefix(str(value)).strip() for value in values
        if strip_heading_prefix(str(value)).strip()
    ))


def _resolve_chapter_index(titles: list[str], reference: str) -> int:
    compact = re.sub(r"\s+", "", str(reference or ""))
    numeral_match = re.search(r"第?([一二三四五六七八九十\d]+)章", compact)
    if numeral_match:
        index = _number(numeral_match.group(1)) - 1
        if 0 <= index < len(titles):
            return index
    reference_key = strip_heading_prefix(str(reference)).strip()
    exact = [index for index, title in enumerate(titles) if title == reference_key]
    if len(exact) == 1:
        return exact[0]
    partial = [index for index, title in enumerate(titles) if reference_key and reference_key in title]
    if len(partial) == 1:
        return partial[0]
    raise ModelRetry("没有找到唯一对应的现有章节，请先读取目录并使用真实章节标题或序号。")


def _number(value: str) -> int:
    if str(value).isdigit():
        return int(value)
    mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
               "七": 7, "八": 8, "九": 9, "十": 10}
    raw = str(value)
    if raw in mapping:
        return mapping[raw]
    if raw.startswith("十"):
        return 10 + mapping.get(raw[1:], 0)
    if "十" in raw:
        left, right = raw.split("十", 1)
        return mapping.get(left, 0) * 10 + mapping.get(right, 0)
    return 0


def _action_summary(call: AgentToolCall) -> str:
    if call.tool_name == AgentToolName.RERUN_FINAL_PLAN:
        structure = call.arguments.get("new_structure") or []
        return f"目录调整为 {len(structure)} 章"
    if call.tool_name == AgentToolName.UPDATE_SECTION_TITLES:
        return f"批量修改 {len(call.arguments.get('changes') or [])} 个章节标题"
    return call.reason or call.tool_name.value
