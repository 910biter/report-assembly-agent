"""Planner Agent: two-stage planning.

Stage 1 plans analysis dimensions and evidence needs before Evidence/Analysis.
Stage 2 freezes the final report structure after actual Facts/Inferences exist.
This keeps content structure driven by evidence while templates control
presentation unless explicitly marked as hard structure.
"""

import json
from copy import deepcopy

from app.agents.base import BaseAgent
from app.context_budget import ContextSection, build_prompt_from_sections
from app.db import session_scope
from app.infrastructure.orm import ORMPlan
from sqlalchemy import select, update
from app.models import ReportPlan
from app.document_shape import normalize_document_shape, normalize_composition_mode
from app.planning.scale import normalize_chapter_budgets, reconcile_scale_budget
from app.planning.structure import normalize_contract
from app.runtime_profiles import stage_input_budget_tokens
from app.observability.core_chain import collect_assigned_ids, extract_tagged_ids
from app.token_monitor import update_call_funnel
from app.planning.evidence_context import bind_plan_evidence

_SYSTEM = """你是情报报告分析规划师。根据用户主题、材料摘要与机构风格,只制定分析问题与证据提取方向,不要冻结最终报告章节。
严格输出 JSON,不要任何解释:
{
  "title": "报告标题",
  "objective": "报告目的(解决什么问题、支撑什么决策)",
  "audience": "报告受众",
  "report_type": "报告类型(与机构风格变体对应)",
  "core_question": "报告要回答的核心问题(一句话)",
  "core_judgment": "报告的核心判断/主线结论(一句话)",
  "narrative_logic": "初步分析假设:后续应重点验证哪些关系,可被 Evidence/Analysis 推翻,不要写成最终目录",
  "report_budget": {
    "target_words": 全文目标字数,
    "soft_max_words": 建议最大字数,
    "hard_max_words": 绝对最大字数,
    "summary_budget": 摘要字数预算
  },
  "dimensions": ["优先分析维度1", "优先分析维度2", ...],
  "evidence_needs": [
    {
      "need": "需要证实的信息需求(子问题,一句话可查证)",
      "dimension": "归属分析维度",
      "priority": "high/medium/low"
    }
  ],
  "required_facts": ["证据提取应优先寻找的信息类别"]
}
本阶段只制定 Evidence 与 Analysis 的问题清单，最终目录留待事实和推论形成后再定。
1. 从用户目标和已理解的材料中识别可查证的独立子问题；每个 evidence_needs.need 用一句话说明要证实或证伪什么，并按对核心问题的作用标注 priority。复杂任务可从材料呈现的不同视角展开，数量随任务而定。
2. dimensions 与 required_facts 表达检索优先级。材料中新出现且与用户目标相关的高价值信息仍可进入后续提取，不受初步类别限制。
3. core_judgment 与 narrative_logic 是待验证的分析假设，可由后续证据修正。模板提供文体和组织参考；仅当用户明确约定硬结构时才将其目录视为结构约束。
4. report_budget 保留用户明确的篇幅要求；未明确时根据当前任务范围给出暂定建议，待 Evidence 与 Analysis 完成后由 Final Planner 依据真实证据重新估算。只返回上述 JSON 字段。"""

_FINAL_SYSTEM = """你是情报报告结构总规划师。现在 Evidence 与 Analysis 已完成,请基于真实事实、推断、用户目标和模板策略生成最终 ReportPlan。
严格输出 JSON,不要任何解释:
{
  "title": "报告标题",
  "objective": "报告目的",
  "audience": "报告受众",
  "report_type": "报告类型",
  "core_question": "报告要回答的核心问题",
  "core_judgment": "报告核心判断/主线结论",
  "narrative_logic": "最终叙事逻辑:章节如何递进",
  "document_shape": {
    "kind": "模型根据用户目标、材料和文风概括的开放文档形态标签；无法确定时写 unknown",
    "raw_kind": "模型的原始形态判断，必须保留，不得为符合枚举改写",
    "heading_policy": "numbered/plain/none",
    "section_policy": "required/optional/hidden",
    "subheading_policy": "numbered/plain/hidden",
    "render_base": "selected_template/default_structured/blank_article",
    "opening": "title_only/lead/summary",
    "closing": "natural/conclusion/signature",
    "rationale": "本次为何采用此文档形态"
  },
  "composition_mode": "chaptered/article_beats",
  "report_budget": {
    "target_words": 全文最终目标字数,
    "min_words": 证据充分时可接受的最低字数,
    "max_words": 禁止重复扩写前提下的安全上限,
    "evidence_status": "sufficient/limited/insufficient",
    "underfill_reason": "证据不足时允许低于目标的原因,否则为空"
  },
  "chapters": [
    {
      "title": "章节标题",
      "core_question": "本章要回答的问题",
      "core_message": "本章核心信息",
      "questions": ["本章要回答的问题"],
      "judgment": "本章核心判断/写作目的",
      "relation_to_prev": "与上一章关系",
      "required_facts": ["本章需要覆盖的事实主题"],
      "required_inferences": ["本章需要覆盖的分析判断"],
      "handles_missing_information": true,
      "missing_information": ["本章需要明确说明的证据缺口"],
      "primary_fact_ids": [1, 2],
      "primary_inference_ids": [3],
      "subsections": [
        {
          "title": "二级小标题",
          "purpose": "该小节要解决的问题",
          "primary_fact_ids": [1],
          "primary_inference_ids": [3],
          "detail_level": "expand/brief/reference",
          "target_words": 小节目标字数
        }
      ],
      "exclude": ["避免重复展开的内容"],
      "next_bridge": "承接下一章的逻辑",
      "evidence_requirements": ["本章必须有证据支撑的内容"],
      "completion_criteria": ["完成条件"],
      "target_words": 章节目标字数,
      "importance": "high/medium/low",
      "evidence_density": "high/medium/low"
    }
  ]
}
本阶段以真实 Facts/Inferences、用户目标和模板用途确定最终内容结构。
1. 先选择文档形态和 composition_mode：正式报告、综述可按章组织；连续文章、推送或新闻稿可用 article_beats 组织内部证据弧线。heading_policy 与 section_policy 分别控制标题呈现；隐藏结构的文章仍需在 chapters 中保留证据归属。
2. FORMAT_ONLY 只影响版式，SOFT_STRUCTURE 提供组织参考，HARD_STRUCTURE 执行用户明确的硬目录。render_base 仅选择导出母版：需要正式分级标题而参考文档没有可用层级时用 default_structured，纯文章可用 blank_article，其余适用时选 selected_template。
3. 按核心问题安排递进的章节和必要的小节，每个重要事实在最合适的位置充分展开，其余位置作简要承接。初步分析主线可以随真实证据修正；证据薄弱的话题可融入边界说明，不必单独成节。
4. 每章的 core_message、required_facts、primary_fact_ids 与 primary_inference_ids 应相互对应。数值、变化、比较和状态由直接支持它们的事实承载；使用推论时同步规划其 based_fact_ids。对照同一对象和可比口径后再安排变化判断，保留来源归属、时间、条件及预计/已发生等语气。
5. report_budget 先承接用户明确要求，再按证据广度、信息丰富度、论证深度和结构任务确定可承载的 min/target/max。仅在整体证据确实不足时填写 underfill_reason；已有事实及其适用边界仍可展开。对局部材料未提供的信息保留来源范围，并核对其它来源。只返回上述 JSON 字段。"""

_LOCAL_RESTRUCTURE_SYSTEM = """你负责按用户确认的新标题调整受影响章节的局部契约。严格输出 JSON：{"chapters":[章节契约]}。
工作方法：保持用户确认的标题与顺序；从输入的原章节中重组已有问题、事实主题、推论主题和小节。拆分时按语义分配，合并时整合重复内容，并保留可追溯的 primary_fact_ids、primary_inference_ids 与规模预算。未变化章节沿用原契约，本阶段只返回受影响部分。"""


def _normalize_needs(raw) -> list[dict]:
    """归一化 Evidence Needs(结构化待证实需求):容错模型输出格式。

    [{need, dimension, priority}] 或 [{question/信息需求, ...}] 或扁平字符串列表。
    """
    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in raw or []:
        if isinstance(item, str):
            entry = {"need": item.strip(), "dimension": "核心事实发现", "priority": "medium"}
        elif isinstance(item, dict):
            text = str(item.get("need") or item.get("question") or item.get("信息需求") or "").strip()
            if not text:
                continue
            entry = {
                "need": text,
                "dimension": str(item.get("dimension") or "核心事实发现").strip(),
                "priority": str(item.get("priority") or "medium").strip(),
            }
        else:
            continue
        if not entry["need"] or entry["need"] in seen:
            continue
        seen.add(entry["need"])
        cleaned.append(entry)
    return cleaned


class PlannerAgent(BaseAgent):
    name = "planner"
    role = _SYSTEM

    def plan(self, context_block: str, user_requirements: str = "",
             revision_instruction: str = "", required_dimensions: list[str] | None = None,
             required_dimension_count: int = 0) -> ReportPlan:
        """Create an analysis plan before evidence extraction."""
        dimensions = list(dict.fromkeys(
            str(item).strip() for item in required_dimensions or [] if str(item).strip()
        ))
        constraint = ""
        if dimensions:
            constraint = (
                f"用户已确认分析维度为 {len(dimensions)} 个："
                + " | ".join(dimensions)
                + "。dimensions 必须完整保持名称和顺序；evidence_needs 应覆盖这些维度，但仍允许发现高价值新事实。"
            )
        elif int(required_dimension_count or 0) > 0:
            constraint = f"用户明确要求规划 {int(required_dimension_count)} 个分析维度，输出数量必须准确。"
        prompt, _audit = build_prompt_from_sections("planner", [
            ContextSection("instruction", [
                "请输出分析规划 JSON。",
                f"本轮经用户批准的调整：{revision_instruction}" if revision_instruction else "",
                constraint,
            ], weight=5, required_items=1),
            ContextSection("task_context", context_block.splitlines(), weight=3, required_items=1),
        ], stage_input_budget_tokens("planner"))
        payload = self.generate_json(prompt)
        generated_dimensions = [str(item).strip() for item in payload.get("dimensions") or [] if str(item).strip()]
        if dimensions:
            payload["dimensions"] = dimensions
        elif required_dimension_count and len(generated_dimensions) != int(required_dimension_count):
            raise ValueError(
                f"ANALYSIS_DIMENSION_COUNT_MISMATCH: expected={int(required_dimension_count)}, "
                f"actual={len(generated_dimensions)}"
            )
        plan = ReportPlan(
            title=str(payload.get("title", "")),
            objective=str(payload.get("objective", "")),
            audience=str(payload.get("audience", "")),
            report_type=str(payload.get("report_type", "")),
            core_question=str(payload.get("core_question", "")),
            core_judgment=str(payload.get("core_judgment", "")),
            narrative_logic=str(payload.get("narrative_logic", "")),
            structure=[],
            dimensions=[str(item) for item in payload.get("dimensions", [])],
            evidence_needs=_normalize_needs(payload.get("evidence_needs")),
            required_facts=[str(item) for item in payload.get("required_facts", [])],
            budget=reconcile_scale_budget(
                user_requirements,
                payload.get("report_budget") if isinstance(payload.get("report_budget"), dict) else {},
                {},
            ),
            chapter_plans=[],
            user_requirements=user_requirements or "",
            plan_stage="analysis",
        )
        plan.analysis_plan_json = _plan_snapshot(plan, "analysis")
        return save_plan(plan)

    def revise_final_plan_structure(self, plan_id: int, required_structure: list[str],
                                    instruction: str = "", refine: bool = True) -> ReportPlan:
        """Apply an approved structure diff without regenerating unchanged chapters."""
        from app.config import settings
        from app.rendering.headings import strip_heading_prefix

        titles = [
            strip_heading_prefix(str(item)) for item in required_structure
            if strip_heading_prefix(str(item))
        ]
        if not titles or len(set(titles)) != len(titles):
            raise ValueError("INVALID_REQUIRED_STRUCTURE")
        with session_scope() as s:
            row = s.execute(select(ORMPlan).where(ORMPlan.c.id == plan_id)).mappings().first()
        if row is None:
            raise ValueError(f"PLAN_NOT_FOUND: {plan_id}")
        old_chapters = json.loads(row["chapter_plans"] or "[]")
        old_final = json.loads(row["final_plan_json"] or "{}")
        old_by_title = {str(item.get("title") or ""): dict(item) for item in old_chapters}
        affected_old = [item for item in old_chapters if str(item.get("title") or "") not in set(titles)]
        affected_titles = [title for title in titles if title not in old_by_title]
        changed_chapters = _fallback_local_restructure(affected_old, affected_titles)

        if affected_titles and refine:
            prompt = (
                f"用户修改要求：{instruction or '按用户确认目录调整局部章节'}\n"
                f"需要输出的新章节标题：{json.dumps(affected_titles, ensure_ascii=False)}\n"
                f"被替换的原章节契约：{json.dumps(affected_old, ensure_ascii=False)}"
            )
            try:
                payload = self.generate_json(
                    prompt,
                    system=_LOCAL_RESTRUCTURE_SYSTEM,
                    max_tokens=min(4096, int(settings.final_planner_output_tokens)),
                )
                candidate = payload.get("chapters") if isinstance(payload.get("chapters"), list) else []
                candidate_titles = [str(item.get("title") or "") for item in candidate if isinstance(item, dict)]
                if candidate_titles == affected_titles:
                    changed_chapters = [dict(item) for item in candidate]
            except Exception:
                # The approved structure remains executable even when the compact
                # semantic refinement fails; Narrative Plan will refine each chapter.
                pass

        changed_by_title = {str(item.get("title") or ""): item for item in changed_chapters}
        chapters = [
            deepcopy(old_by_title[title]) if title in old_by_title
            else deepcopy(changed_by_title.get(title) or {"title": title})
            for title in titles
        ]
        for title, chapter in zip(titles, chapters):
            chapter["title"] = title
        budget = json.loads(row["budget"] or "{}")
        chapters = normalize_chapter_budgets(chapters, int(budget.get("target_words") or 0))
        plan = ReportPlan(
            id=plan_id,
            title=str(row["title"] or ""),
            objective=str(row["objective"] or ""),
            audience=str(row["audience"] or ""),
            report_type=str(row["report_type"] or ""),
            core_question=str(row["core_question"] or ""),
            core_judgment=str(row["core_judgment"] or ""),
            narrative_logic=str(row["narrative_logic"] or ""),
            structure=titles,
            dimensions=json.loads(row["dimensions"] or "[]"),
            evidence_needs=json.loads(row["evidence_needs"] or "[]"),
            required_facts=json.loads(row["required_facts"] or "[]"),
            budget=budget,
            document_shape=normalize_document_shape(old_final.get("document_shape")),
            composition_mode=normalize_composition_mode(
                old_final.get("composition_mode"), shape=old_final.get("document_shape"),
            ),
            chapter_plans=chapters,
            user_requirements=str(row["user_requirements"] or ""),
            plan_stage="final",
        )
        normalized = normalize_contract({"chapter_plans": chapters})
        plan.chapter_plans = normalized["chapter_plans"]
        plan.structure = [str(item.get("title") or "") for item in plan.chapter_plans]
        plan.final_plan_json = _plan_snapshot(plan, "final")
        return update_plan(plan)

    def finalize_report_plan(self, plan_id: int, context_block: str,
                             required_structure: list[str] | None = None,
                             required_chapter_count: int = 0,
                             document_shape_hint: dict | None = None,
                             facts: list[dict] | None = None,
                             inferences: list[dict] | None = None) -> ReportPlan:
        """Freeze the final report structure after Evidence + Analysis."""
        with session_scope() as s:
            existing = s.execute(
                select(ORMPlan.c.budget, ORMPlan.c.user_requirements)
                .where(ORMPlan.c.id == plan_id)
            ).mappings().first()
        previous_budget = json.loads(existing["budget"] or "{}") if existing else {}
        previous_requirements = str(existing["user_requirements"] or "") if existing else ""
        from app.config import settings

        safe_unit_words = max(
            500,
            int(settings.writer_output_tokens * settings.writer_visible_word_token_ratio),
        )
        structure = []
        if required_structure:
            from app.rendering.headings import strip_heading_prefix
            structure = [strip_heading_prefix(str(item)) for item in required_structure if strip_heading_prefix(str(item))]
        structure_instruction = ""
        if structure:
            structure_instruction = (
                f"\n用户已明确确认最终目录为 {len(structure)} 章，章节标题和顺序如下：\n"
                + "\n".join(f"{index + 1}. {title}" for index, title in enumerate(structure))
                + "\n请在该目录约束内完成每章问题、证据、推论、小节与篇幅规划，不要合并、删减或改名。"
            )
        elif int(required_chapter_count or 0) > 0:
            structure_instruction = (
                f"\n用户明确要求最终报告为 {int(required_chapter_count)} 章。"
                "请自主规划恰好对应数量的章节，不得擅自减少或增加。"
            )
        instruction = (
            f"执行资源边界:单个小节一次成文的安全容量约 {safe_unit_words} 字。"
            "章节与小节数量仍由内容逻辑决定，但任何小节的 target_words 不得超过该容量；"
            "较长内容应在规划阶段拆成多个各自有明确研究问题的语义小节，不得依赖 Writer 续写或事后补写。\n"
            "请输出字段完整、闭合的最终报告结构 JSON。"
            + structure_instruction
        )
        prompt, _audit = build_prompt_from_sections("final_planning", [
            ContextSection("instruction", [instruction], weight=5, required_items=1),
            ContextSection("planning_context", context_block.splitlines(), weight=3, required_items=1),
        ], stage_input_budget_tokens("final_planner"))
        input_fact_ids = extract_tagged_ids(prompt, "fact_id")
        input_inference_ids = extract_tagged_ids(prompt, "inference_id")
        try:
            payload = self.generate_json(
                prompt,
                system=_FINAL_SYSTEM,
                max_tokens=settings.final_planner_output_tokens,
            )
        except Exception as exc:
            update_call_funnel(
                self.last_call_id,
                final_plan_input_fact_ids=input_fact_ids,
                final_plan_input_inference_ids=input_inference_ids,
                final_plan_generation_error=str(exc)[:300],
            )
            raise
        call_id = self.last_call_id
        chapters = payload.get("chapters") if isinstance(payload.get("chapters"), list) else []
        update_call_funnel(
            call_id,
            final_plan_input_fact_ids=input_fact_ids,
            final_plan_input_inference_ids=input_inference_ids,
            final_plan_candidate_fact_ids=collect_assigned_ids(chapters, "fact"),
            final_plan_candidate_inference_ids=collect_assigned_ids(chapters, "inference"),
            final_plan_candidate_chapter_count=len(chapters),
        )
        if required_chapter_count and len(chapters) != int(required_chapter_count):
            raise ValueError(
                f"FINAL_PLAN_CHAPTER_COUNT_MISMATCH: expected={int(required_chapter_count)}, "
                f"actual={len(chapters)}"
            )
        if not chapters:
            chapters = [{"title": "综合分析", "questions": [], "judgment": payload.get("core_judgment", "")}]
        final_budget = reconcile_scale_budget(
            previous_requirements,
            previous_budget,
            payload.get("report_budget") if isinstance(payload.get("report_budget"), dict) else {},
        )
        document_shape = normalize_document_shape(
            payload.get("document_shape"), fallback=document_shape_hint,
        )
        composition_mode = normalize_composition_mode(
            payload.get("composition_mode"), shape=document_shape,
        )
        chapters = normalize_chapter_budgets(chapters, int(final_budget.get("target_words") or 0))
        plan = ReportPlan(
            id=plan_id,
            title=str(payload.get("title", "")),
            objective=str(payload.get("objective", "")),
            audience=str(payload.get("audience", "")),
            report_type=str(payload.get("report_type", "")),
            core_question=str(payload.get("core_question", "")),
            core_judgment=str(payload.get("core_judgment", "")),
            narrative_logic=str(payload.get("narrative_logic", "")),
            structure=[str(c.get("title", "")) for c in chapters if c.get("title")],
            dimensions=[],
            required_facts=[],
            # A malformed Final Planner response must not erase the user target
            # already captured by the preliminary AnalysisPlan.
            budget=final_budget,
            document_shape=document_shape,
            composition_mode=composition_mode,
            chapter_plans=chapters,
            user_requirements=previous_requirements,
            plan_stage="final",
        )
        normalized = normalize_contract({"chapter_plans": chapters})
        bind_plan_evidence(normalized, facts or [], inferences or [])
        plan.chapter_plans = normalized["chapter_plans"]
        plan.structure = [c.get("title", "") for c in plan.chapter_plans]
        update_call_funnel(
            call_id,
            final_plan_assigned_fact_ids=collect_assigned_ids(plan.chapter_plans, "fact"),
            final_plan_assigned_inference_ids=collect_assigned_ids(plan.chapter_plans, "inference"),
        )
        plan.final_plan_json = _plan_snapshot(plan, "final")
        return update_plan(plan)


def _fallback_local_restructure(old_chapters: list[dict], new_titles: list[str]) -> list[dict]:
    """Reshape existing contracts by order; values stay domain-neutral and evidence-bound."""
    if not new_titles:
        return []
    sources = [deepcopy(item) for item in old_chapters if isinstance(item, dict)]
    if not sources:
        return [{
            "title": title,
            "core_question": f"本章围绕“{title}”需要回答什么问题",
            "questions": [f"围绕“{title}”组织已有事实与分析判断"],
            "judgment": "按用户确认的章节边界组织已有证据",
            "subsections": [],
            "target_words": 0,
        } for title in new_titles]
    if len(new_titles) == 1 and len(sources) > 1:
        merged = deepcopy(sources[0])
        for key in (
            "questions", "required_facts", "required_inferences", "primary_fact_ids",
            "primary_inference_ids", "subsections", "exclude", "evidence_requirements",
            "completion_criteria",
        ):
            values = []
            for source in sources:
                values.extend(source.get(key) or [])
            merged[key] = list(dict.fromkeys(
                json.dumps(item, ensure_ascii=False, sort_keys=True) for item in values
            ))
            merged[key] = [json.loads(item) for item in merged[key]]
        merged["target_words"] = sum(int(item.get("target_words") or 0) for item in sources)
        merged["title"] = new_titles[0]
        return [merged]

    result = []
    for index, title in enumerate(new_titles):
        source = deepcopy(sources[min(index, len(sources) - 1)])
        source["title"] = title
        if len(sources) == 1 and len(new_titles) > 1:
            subsections = list(sources[0].get("subsections") or [])
            assigned = [item for position, item in enumerate(subsections) if position % len(new_titles) == index]
            source["subsections"] = assigned
            if assigned:
                source["primary_fact_ids"] = _ordered_ids(assigned, "primary_fact_ids")
                source["primary_inference_ids"] = _ordered_ids(assigned, "primary_inference_ids")
                purposes = [str(item.get("purpose") or "") for item in assigned if str(item.get("purpose") or "")]
                if purposes:
                    source["questions"] = purposes
                    source["core_question"] = purposes[0]
            original_target = int(sources[0].get("target_words") or 0)
            source["target_words"] = original_target // len(new_titles) if original_target else 0
        result.append(source)
    return result


def _ordered_ids(items: list[dict], key: str) -> list[int]:
    values = []
    for item in items:
        for value in item.get(key) or []:
            if str(value).isdigit() and int(value) not in values:
                values.append(int(value))
    return values


def _plan_snapshot(plan: ReportPlan, stage: str) -> dict:
    return {
        "stage": stage,
        "title": plan.title,
        "objective": plan.objective,
        "audience": plan.audience,
        "report_type": plan.report_type,
        "core_question": plan.core_question,
        "core_judgment": plan.core_judgment,
        "narrative_logic": plan.narrative_logic,
        "structure": plan.structure,
        "dimensions": plan.dimensions,
        "evidence_needs": plan.evidence_needs,
        "required_facts": plan.required_facts,
        "chapter_plans": plan.chapter_plans,
        "budget": plan.budget,
        "document_shape": plan.document_shape,
        "composition_mode": plan.composition_mode,
        "user_requirements": plan.user_requirements,
        "plan_version": plan.plan_version,
    }


def save_plan(plan: ReportPlan) -> ReportPlan:
    normalized = normalize_contract({"chapter_plans": plan.chapter_plans})
    plan.chapter_plans = normalized["chapter_plans"]
    plan.structure = [c.get("title", "") for c in plan.chapter_plans]
    with session_scope() as s:
        result = s.execute(
            ORMPlan.insert().values(
                title=plan.title, objective=plan.objective, audience=plan.audience,
                report_type=plan.report_type, core_question=plan.core_question,
                core_judgment=plan.core_judgment, narrative_logic=plan.narrative_logic,
                structure=json.dumps(plan.structure, ensure_ascii=False),
                dimensions=json.dumps(plan.dimensions, ensure_ascii=False),
                evidence_needs=json.dumps(plan.evidence_needs, ensure_ascii=False),
                required_facts=json.dumps(plan.required_facts, ensure_ascii=False),
                chapter_plans=json.dumps(plan.chapter_plans, ensure_ascii=False),
                budget=json.dumps(plan.budget, ensure_ascii=False),
                user_requirements=plan.user_requirements,
                plan_stage=plan.plan_stage,
                plan_version=int(plan.plan_version or 1),
                analysis_plan_json=json.dumps(plan.analysis_plan_json or _plan_snapshot(plan, "analysis"), ensure_ascii=False),
                final_plan_json=json.dumps(plan.final_plan_json or {}, ensure_ascii=False),
            )
        )
        plan.id = int(result.inserted_primary_key[0])
    return plan


def update_plan(plan: ReportPlan) -> ReportPlan:
    import time as _time
    normalized = normalize_contract({"chapter_plans": plan.chapter_plans})
    plan.chapter_plans = normalized["chapter_plans"]
    plan.structure = [c.get("title", "") for c in plan.chapter_plans]
    with session_scope() as s:
        # plan_version 自增 + finalized_at(原 SQL 语义)
        row = s.execute(
            select(ORMPlan.c.plan_version)
            .where(ORMPlan.c.id == plan.id)
            .with_for_update()
        ).first()
        next_version = int(row[0] or 1) + 1 if row else 1
        plan.plan_version = next_version
        plan.plan_stage = "final"
        final_payload = dict(plan.final_plan_json or _plan_snapshot(plan, "final"))
        final_payload.update({
            "stage": "final",
            "plan_version": next_version,
            "structure": plan.structure,
            "chapter_plans": plan.chapter_plans,
            "budget": plan.budget,
        })
        plan.final_plan_json = final_payload
        s.execute(
            update(ORMPlan)
            .where(ORMPlan.c.id == plan.id)
            .values(
                title=plan.title, objective=plan.objective, audience=plan.audience,
                report_type=plan.report_type, core_question=plan.core_question,
                core_judgment=plan.core_judgment, narrative_logic=plan.narrative_logic,
                structure=json.dumps(plan.structure, ensure_ascii=False),
                chapter_plans=json.dumps(plan.chapter_plans, ensure_ascii=False),
                budget=json.dumps(plan.budget, ensure_ascii=False),
                plan_stage="final",
                plan_version=next_version,
                final_plan_json=json.dumps(final_payload, ensure_ascii=False),
                finalized_at=_time.strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
    return plan

__all__ = ['PlannerAgent', 'save_plan', 'update_plan']
