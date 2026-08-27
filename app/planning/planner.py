"""Planner Agent: two-stage planning.

Stage 1 plans analysis dimensions and evidence needs before Evidence/Analysis.
Stage 2 freezes the final report structure after actual Facts/Inferences exist.
This keeps content structure driven by evidence while templates control
presentation unless explicitly marked as hard structure.
"""

import json

from app.agents.base import BaseAgent
from app.context_budget import ContextSection, build_prompt_from_sections
from app.db import session_scope
from app.infrastructure.orm import ORMPlan
from sqlalchemy import select, update
from app.models import ReportPlan
from app.planning.scale import normalize_chapter_budgets, reconcile_scale_budget
from app.planning.structure import normalize_contract
from app.runtime_profiles import stage_input_budget_tokens

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
要求:
1. 只规划 Evidence/Analysis 需要回答的问题和事实类别;不要输出 chapters,不要沿用模板目录
2. 模板主要用于格式、标题层级、编号和版式;除非模板策略明确 HARD_STRUCTURE,不得把模板目录当成最终报告目录
3. dimensions/required_facts 只是优先检索与重点发现方向,不是事实边界;Evidence 必须允许材料中出现的高价值新事实进入
4. 初步分析假设不是最终叙事逻辑,后续 Final Planner 不需要继承,应以真实 Facts/Inferences 为准
5. 规模预算依据:用户明确字数要求(如有)、报告类型档位
   (简要约2000-4000字/标准约5000-10000字/深度约10000-20000字)、材料信息量、章节数与重要性。
6. 最终报告结构将在 Evidence + Analysis 后另行生成,届时由真实 Fact/Inference 和核心结论决定。
7. 【证据需求拆分——最重要的要求】evidence_needs 应覆盖完成用户目标所需的可查证问题:
   - 根据当前主题和材料实时识别关键方面,每个独立且可查证的方面形成一个 need；
     数量由任务复杂度和材料信息量决定,不使用固定下限或领域维度清单
   - 每个 need 必须是"可被材料证实/证伪的一句话子问题",避免宽泛无法查证的表述
   - 按对核心判断的支撑度标注 priority(支撑主线结论的为 high)
8. 面向决策的全面性:证据检索的遗漏比松弛更危险——宁可多列可查证子问题,不要因低估主题而少列。
9. 【视角发现按需】先判断主题复杂度；简单任务直接按问题拆分，复杂任务从材料呈现出的多个有效视角
   探索证据需求。视角是运行时发现结果,不是预置领域分类。"""

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
要求:
1. 内容决定结构:章节必须围绕已抽取 Facts/Inferences 和核心结论组织,不得机械沿用模板目录
2. 模板结构类型:
   FORMAT_ONLY: 只控制字体、标题层级、编号、版式和导出呈现
   SOFT_STRUCTURE: 目录只作参考,可借鉴但不得压过材料逻辑
   HARD_STRUCTURE: 只有用户或模板明确要求时才严格遵守
3. 除 HARD_STRUCTURE 外,不要把模板示例目录当成最终目录;模板主要决定呈现,不决定内容逻辑
4. 章节应形成递进关系,不是事实清单分类;章节数量完全由用户目标、证据容量和叙事逻辑决定
5. 小标题也必须由内容需要决定:只有当本章内部确实存在多个相对独立的话题层次时才规划 subsections;小标题不是装饰,不得把一句正文改成标题
6. 初步分析主线只是假设与关注方向;如果 Facts/Inferences 指向不同逻辑,必须调整,不得为继承早期规划而牺牲内容合理性
7. 每个重要事实原则上只在最合适章节完整展开一次,其他章节只做必要承接
8. 证据不足的主题不得硬设独立章节或小节凑结构,应合并为边界、风险或待补充说明。
9. report_budget 是 Analysis 后冻结的全文唯一规模预算。它应综合用户目标、Facts、Inferences 与章节结构;
   下游不得再次静默缩减。证据不足时允许 underfill,但必须给出 underfill_reason,禁止为写满而重复或虚构。"""


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

    def plan(self, context_block: str, user_requirements: str = "") -> ReportPlan:
        """Create an analysis plan before evidence extraction."""
        prompt, _audit = build_prompt_from_sections("planner", [
            ContextSection("instruction", ["请输出分析规划 JSON。"], weight=5, required_items=1),
            ContextSection("task_context", context_block.splitlines(), weight=3, required_items=1),
        ], stage_input_budget_tokens("planner"))
        payload = self.generate_json(prompt)
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

    def finalize_report_plan(self, plan_id: int, context_block: str,
                             required_structure: list[str] | None = None) -> ReportPlan:
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
        payload = self.generate_json(
            prompt,
            system=_FINAL_SYSTEM,
            max_tokens=settings.final_planner_output_tokens,
        )
        chapters = payload.get("chapters") if isinstance(payload.get("chapters"), list) else []
        if not chapters:
            chapters = [{"title": "综合分析", "questions": [], "judgment": payload.get("core_judgment", "")}]
        final_budget = reconcile_scale_budget(
            previous_requirements,
            previous_budget,
            payload.get("report_budget") if isinstance(payload.get("report_budget"), dict) else {},
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
            chapter_plans=chapters,
            user_requirements=previous_requirements,
            plan_stage="final",
        )
        normalized = normalize_contract({"chapter_plans": chapters})
        plan.chapter_plans = normalized["chapter_plans"]
        plan.structure = [c.get("title", "") for c in plan.chapter_plans]
        plan.final_plan_json = _plan_snapshot(plan, "final")
        return update_plan(plan)


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
        "user_requirements": plan.user_requirements,
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
        row = s.execute(select(ORMPlan.c.plan_version).where(ORMPlan.c.id == plan.id)).first()
        next_version = int(row[0] or 1) + 1 if row else 1
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
                final_plan_json=json.dumps(plan.final_plan_json or _plan_snapshot(plan, "final"), ensure_ascii=False),
                finalized_at=_time.strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
    return plan

__all__ = ['PlannerAgent', 'save_plan', 'update_plan']
