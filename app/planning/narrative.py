"""Narrative planning: organize facts before drafting prose."""
from __future__ import annotations

import json
import re

from app.agents.base import BaseAgent
from app.planning.structure import normalize_topic, serializable_memory
from app.rendering.headings import strip_heading_prefix
from app.task_artifacts import save_task_artifact
from app.context_budget import ContextSection, build_prompt_from_sections
from app.observability.core_chain import collect_assigned_ids
from app.token_monitor import update_call_funnel
from app.planning.evidence_context import planning_prompt, bind_plan_evidence


def _prompt_visible_ids(prompt: str, items: list[dict]) -> list[int]:
    """Match complete evidence records retained in a budgeted narrative prompt."""
    rendered = "\n" + str(prompt or "").strip() + "\n"
    visible = set()
    for item in items:
        if item.get("id") is None:
            continue
        record = f"{int(item['id'])}. {item.get('content', '')}"
        if record in rendered:
            visible.add(int(item["id"]))
    return sorted(visible)

_SYSTEM = """你是报告章节叙事规划师。你的任务不是写正文,而是在 Writer 写作前组织事实。
严格输出 JSON,不要任何解释:
{
  "chapter_title": "章节标题",
  "core_question": "本章要回答的核心问题(一句话,可被材料证实/证伪)",
  "core_message": "本章核心信息/主线判断(一句话)",
  "central_message": "本章中心意思",
  "evidence_status": "sufficient/limited/insufficient",
  "evidence_reason": "证据对本章目标的承载判断",
  "handles_missing_information": true,
  "missing_information": ["当前材料仍缺少什么"],
  "logic_order": ["话题1", "话题2", "..."],
  "subsections": [
    {
      "title": "小节标题",
      "purpose": "该小节解决什么问题",
      "core_message": "该小节需要传达的核心信息",
      "fact_ids": [1,2],
      "inference_ids": [3],
      "target_words": 1200,
      "evidence_status": "sufficient/limited/insufficient",
      "evidence_reason": "为什么能够或不能支撑目标篇幅",
      "handles_missing_information": true,
      "missing_information": ["该小节仍缺少什么"],
      "detail_level": "expand/brief/reference",
      "completion_criteria": ["表达什么才算完成,如:说明入口", "说明操作主体", "说明关键步骤"],
      "discourse_flow": [
        {"role": "background", "fact_ids": [1], "inference_ids": []},
        {"role": "analysis", "fact_ids": [2], "inference_ids": [3]}
      ],
      "paragraphs": [
        {"paragraph_id": "P1", "purpose": "本段承担的论证作用", "argument": "具体哪些证据形成什么关系、支持什么判断及其范围", "fact_ids": [1], "inference_ids": [], "target_words": 300}
      ],
      "writing_hint": "可留空，不预写正文措辞"
    }
  ],
  "background_fact_ids": [1],
  "must_not_claim": ["不能写成什么"],
  "transition_hint": "可留空，证据关系在段落 argument 中说明"
}
本阶段规划论证，不预写正文。按以下顺序组织：
1. 围绕本章核心问题，为主要事实、背景事实和限定事实安排不同作用；优先使用尚未在前文充分展开的事实。每个 fact_ids/inference_ids 均来自本次输入，推论所依据的事实也要能在相应小节定位。
2. 沿用并校准 Final ReportPlan 已有的 subsections；只有多个相对独立的话题需要分层时才新增。小节标题是结构名称。每节给出目的、主要证据、detail_level、目标篇幅和由材料决定的 completion_criteria。
3. discourse_flow 描述必要的论证顺序，role 可用 background/current_status/evidence/analysis/limitation/judgment。paragraphs 只在论证层次或阅读负担变化时分段；连续的引出和解释可同段。paragraph.target_words 是篇幅分配参考。
4. 每段 argument 要写清具体证据之间如何比较、解释或限定，哪些事实支撑哪些判断，以及仍不能确定的范围。按需纳入能补足机制、对比和边界的事实；小节 fact_ids 应覆盖各段及 discourse_flow 实际使用的事实。purpose、writing_hint 与 transition_hint 只作内部指令，句式和自然段由 Writer 决定。
5. evidence_status 衡量当前证据能否承载本节目的和篇幅。来源为计划、要求、估计、观点或已发生成果时，在规划中保留对应的时间、条件、归属、实现状态和范围；模板只提供组织与表达参考。证据有限时说明具体缺口，按现有依据安排可完成的论证。"""

_QA_SYSTEM = """你是章节叙事质量评审。根据 Narrative Plan 判断本章每个 Topic 是否按计划完成。
严格输出 JSON,不要任何解释:
{
  "topics": [
    {
      "topic_id": "T1",
      "status": "complete/partial/missing",
      "evidence_sufficient": true,
      "missing_aspects": ["还缺什么表达"],
      "action": "keep/rewrite/expand/replan",
      "target_paragraph": 3
    }
  ],
  "structure_note": "章节结构层面是否需要调整(空表示无)"
}
评审方法：逐一核对计划内 Topic 的 completion_criteria 与草稿表达，而非按字数判定。完整且准确用 keep；已有表达但需纠正用 rewrite；证据支持且尚可展开用 expand；结构性无法完成用 replan。证据不足且话题缺失时用 keep，交由规划层处理证据边界。target_paragraph 指向对应自然段的 1 起编号，无对应段落的 replan 用 0。本阶段只评估话题完成情况。"""

_DOCUMENT_SYSTEM = """你是连续文章的叙事总编。Facts/Inferences 是已提取的材料事实与分析判断，不代表所有判断已被证实；应保留各自的来源语义和证据边界。Final ReportPlan 的 chapters 是内部证据弧线，不是必须展示给读者的标题。
严格输出 JSON，不要正文、不要解释：
{
  "composition_mode": "article_beats",
  "central_thread": "全文如何从开篇事实推进到结尾判断",
  "beats": [
    {
      "beat_id": "B1",
      "purpose": "这一段论证承担什么作用",
      "fact_ids": [1, 2],
      "inference_ids": [3],
      "target_words": 260,
      "detail_level": "expand/brief/reference",
      "must_not_claim": ["不能超出哪些证据边界"]
    }
  ],
  "paragraphs": [
    {
      "paragraph_id": "P1",
      "beat_ids": ["B1"],
      "purpose": "本段如何推进全文主线",
      "argument": "所选证据之间的具体关系、支持的判断及其适用范围",
      "fact_ids": [1, 2],
      "inference_ids": [3],
      "target_words": 260
    }
  ],
  "closing": "结尾应完成的收束，不重复前文"
}
本阶段设计连续文章的证据推进，不输出展示标题或正文。
1. beat 是内部论证节拍，须覆盖 Final ReportPlan 的关键证据弧线；primary_fact_ids 是优先锚点，也可选入能解释规模、机制、比较和边界的其他已给事实。每项 fact_ids/inference_ids 均来自输入，推论的依据事实可回溯。
2. paragraphs 是实际段落设计，由论证关系决定数量。每段覆盖一个或多个直接相关的 beat，并说明具体要比较、解释或限定什么。purpose 概括作用；argument 说明所选证据如何支持判断、哪些范围仍未知，以及与相邻段的证据关系。
3. 数值、状态和比较需规划直接支持它们的事实 ID。材料中的计划、目标、估计和观点保留原有时间、条件及归属。限制说明放在它限定的判断附近；模板只指导开篇、推进、详略与收束的表达方式。
4. target_words 的总和接近全文目标，证据充分时用实质关系承载篇幅；证据有限时简述可支持的内容。closing 只约定尚未完成的收束；purpose、central_thread 和 transition 都是内部组织说明，正文措辞交给 Writer。"""


class NarrativeAgent(BaseAgent):
    name = "narrative"
    role = _SYSTEM
    max_retries = 1
    # Evidence and Analysis have already performed the open-ended reasoning.
    # This stage executes a bounded structure contract; hidden reasoning can
    # otherwise consume the entire output budget and return no JSON at all.
    thinking = False

    def plan_chapter(
        self,
        task_id: str,
        chapter_plan: dict,
        report_plan: dict,
        facts: list[dict],
        inferences: list[dict],
        business_block: str = "",
        report_memory: dict | None = None,
        run_id: str = "",
        style_block: str = "",
    ) -> dict:
        from app.config import settings

        fact_ids = {int(f["id"]) for f in facts if f.get("id") is not None}
        inference_ids = {int(i["id"]) for i in inferences if i.get("id") is not None}
        safe_unit_words = max(
            500,
            int(settings.writer_output_tokens * settings.writer_visible_word_token_ratio),
        )
        from app.runtime_profiles import stage_input_budget_tokens

        chapter_contract = f"当前章节规划:{json.dumps(chapter_plan, ensure_ascii=False)}"
        instructions = (
            f"全文标题:{report_plan.get('title','')}\n"
            f"全文核心判断:{report_plan.get('core_judgment','')}\n"
            f"全文叙事逻辑:{report_plan.get('narrative_logic','')}\n"
            f"本轮更新目标:{report_plan.get('update_instruction','')}\n"
            f"单个小节一次生成的建议安全规模:不超过约 {safe_unit_words} 字。"
            "若章节较长且存在多个真实语义层次,应据此规划多个有独立目的的小节;"
            "不得为了切分长度制造无意义小节。\n"
            "请输出本章 Narrative Plan JSON。"
        )
        prompt, context_audit = planning_prompt(
            "narrative",
            [
                ContextSection("章节契约", [chapter_contract], weight=5),
                ContextSection("任务要求", [instructions], weight=5),
                ContextSection("模板表达参考", [style_block] if style_block else [], weight=2),
                ContextSection("证据边界", [business_block], weight=4),
                ContextSection("前文记忆", [
                    f"前文记忆:{json.dumps(serializable_memory(report_memory or {}), ensure_ascii=False)}",
                ], weight=1),
            ], facts, inferences, stage_input_budget_tokens("narrative"),
            required_facts=collect_assigned_ids(chapter_plan, "fact"),
            required_inferences=collect_assigned_ids(chapter_plan, "inference"),
            strict_required=True,
        )
        generation_error = ""
        self.last_call_id = ""
        try:
            if chapter_contract not in prompt or instructions not in prompt:
                raise ValueError("NARRATIVE_CONTRACT_EXCEEDS_INPUT_BUDGET")
            payload = self.generate_json(prompt)
            if not isinstance(payload, dict) or not (payload.get("topics") or payload.get("subsections")):
                raise ValueError("EMPTY_NARRATIVE_PLAN")
        except Exception as exc:
            if str(exc) in {"TASK_PAUSED", "EXECUTION_LOCK_LOST"}:
                raise
            payload = {}
            generation_error = str(exc)[:300]
        call_id = self.last_call_id
        input_fact_ids = _prompt_visible_ids(prompt, facts)
        input_inference_ids = _prompt_visible_ids(prompt, inferences)
        plan = _sanitize_plan(
            payload, chapter_plan,
            fact_ids if generation_error else set(input_fact_ids),
            inference_ids if generation_error else set(input_inference_ids),
        )
        bind_plan_evidence(plan, facts, inferences)
        plan.update(
            generation_status="fallback" if generation_error else "generated",
            generation_error=generation_error,
            context_audit=context_audit,
        )
        update_call_funnel(
            call_id,
            narrative_scope="chapter",
            narrative_input_fact_ids=input_fact_ids,
            narrative_omitted_fact_ids=sorted(fact_ids - set(input_fact_ids)),
            narrative_input_inference_ids=input_inference_ids,
            narrative_omitted_inference_ids=sorted(inference_ids - set(input_inference_ids)),
            narrative_assigned_fact_ids=collect_assigned_ids(plan, "fact"),
            narrative_assigned_inference_ids=collect_assigned_ids(plan, "inference"),
            narrative_generation_error=generation_error,
        )
        try:
            save_task_artifact(
                task_id,
                f"narrative_plan:{chapter_plan.get('title','')}",
                {
                    "chapter_plan": chapter_plan,
                    "fact_ids": sorted(fact_ids),
                    "inference_ids": sorted(inference_ids),
                    "business_block": business_block,
                },
                plan,
                run_id=run_id,
            )
        except Exception:
            pass
        return plan

    def plan_document(
        self,
        task_id: str,
        report_plan: dict,
        facts: list[dict],
        inferences: list[dict],
        run_id: str = "",
        style_block: str = "",
    ) -> dict:
        """Create an article-level contract without replacing Final ReportPlan."""
        fact_ids = {int(item["id"]) for item in facts if item.get("id") is not None}
        inference_ids = {int(item["id"]) for item in inferences if item.get("id") is not None}
        from app.runtime_profiles import stage_input_budget_tokens, stage_profile

        prompt, _audit = planning_prompt(
            "narrative_document",
            [
                ContextSection("全文任务", [
                    f"标题:{report_plan.get('title', '')}",
                    f"核心判断:{report_plan.get('core_judgment', '')}",
                    f"叙事逻辑:{report_plan.get('narrative_logic', '')}",
                    f"目标字数:{int((report_plan.get('budget') or {}).get('target_words') or 0)}",
                    f"用户要求:{report_plan.get('user_requirements') or ''}",
                ], weight=5, required_items=2),
                ContextSection("内部证据弧线", [json.dumps(report_plan.get("chapter_plans") or [], ensure_ascii=False)], weight=5),
                ContextSection("模板表达参考", [style_block] if style_block else [], weight=2),
            ],
            facts, inferences, stage_input_budget_tokens("narrative"),
            required_facts=collect_assigned_ids(report_plan.get("chapter_plans") or [], "fact"),
            required_inferences=collect_assigned_ids(report_plan.get("chapter_plans") or [], "inference"),
        )
        generation_error = ""
        try:
            payload = self.generate_json(
                prompt, system=_DOCUMENT_SYSTEM,
                max_tokens=stage_profile("narrative").output_tokens,
            )
        except Exception as exc:
            if str(exc) in {"TASK_PAUSED", "EXECUTION_LOCK_LOST"}:
                raise
            payload = {}
            generation_error = str(exc)[:300]
        call_id = self.last_call_id
        input_fact_ids = _prompt_visible_ids(prompt, facts)
        input_inference_ids = _prompt_visible_ids(prompt, inferences)
        if not isinstance(payload, dict) or not payload.get("beats"):
            payload = {}
            generation_error = generation_error or "EMPTY_NARRATIVE_PLAN"
        # Resolve evidence dependencies before testing coverage or assigning
        # space. An inference's basis is not a separate missing discourse arc.
        bind_plan_evidence(payload, facts, inferences)
        plan = _sanitize_document_plan(
            payload, report_plan,
            fact_ids if generation_error else set(input_fact_ids),
            inference_ids if generation_error else set(input_inference_ids),
        )
        bind_plan_evidence(plan, facts, inferences)
        plan.update(
            generation_status="fallback" if generation_error else "generated",
            generation_error=generation_error,
            context_audit=_audit,
        )
        update_call_funnel(
            call_id,
            narrative_scope="document",
            narrative_input_fact_ids=input_fact_ids,
            narrative_omitted_fact_ids=sorted(fact_ids - set(input_fact_ids)),
            narrative_input_inference_ids=input_inference_ids,
            narrative_omitted_inference_ids=sorted(inference_ids - set(input_inference_ids)),
            narrative_assigned_fact_ids=collect_assigned_ids(plan, "fact"),
            narrative_assigned_inference_ids=collect_assigned_ids(plan, "inference"),
            narrative_generation_error=generation_error,
        )
        if task_id:
            try:
                save_task_artifact(
                    task_id, "document_narrative_plan",
                    {"plan_id": report_plan.get("id"), "fact_ids": sorted(fact_ids), "inference_ids": sorted(inference_ids)},
                    plan, run_id=run_id,
                )
            except Exception:
                pass
        return plan


    def qa_chapter(
        self,
        chapter_title: str,
        draft_paragraphs: list[str],
        narrative_plan: dict,
        facts: list[dict],
    ) -> dict:
        """Narrative QA:按 Topic 完成条件判断草稿是否完成(语义判断,非字数)。

        返回 {"topics": [{topic_id, status, evidence_sufficient, missing_aspects,
        action, target_paragraph}], "structure_note": ...};失败返回空 topics。
        """
        plan_for_qa = _strip_plan_for_qa(narrative_plan)
        from app.runtime_profiles import stage_input_budget_tokens

        budget = stage_input_budget_tokens("narrative_qa")
        prompt, _audit = build_prompt_from_sections(
            "narrative_qa",
            [
                ContextSection("任务", [f"章节:{chapter_title}", "请输出各 Topic 完成度判断 JSON。"], weight=3, required_items=2),
                ContextSection("Narrative Plan", ["Narrative Plan:", json.dumps(plan_for_qa, ensure_ascii=False)], weight=5, required_items=2),
                ContextSection("章节草稿", ["章节草稿(按段落编号):", *[
                    f"[P{i + 1}] {paragraph}" for i, paragraph in enumerate(draft_paragraphs)
                ]], weight=5),
                ContextSection("支撑 Facts", ["支撑 Facts:", *[
                    f"{fact['id']}. {fact.get('content', '')}" for fact in facts
                ]], weight=4),
            ],
            budget,
        )
        try:
            from app.runtime_profiles import stage_profile
            payload = self.generate_json(
                prompt, system=_QA_SYSTEM,
                max_tokens=stage_profile("narrative_qa").output_tokens,
            )
        except Exception:
            return {"topics": [], "structure_note": ""}
        topics = []
        for item in payload.get("topics") or []:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "keep")
            if action not in {"keep", "rewrite", "expand", "replan"}:
                action = "keep"
            status = str(item.get("status") or "complete")
            if status not in {"complete", "partial", "missing"}:
                status = "complete"
            topics.append({
                "topic_id": str(item.get("topic_id") or "")[:16],
                "status": status,
                "evidence_sufficient": bool(item.get("evidence_sufficient", True)),
                "missing_aspects": [str(m)[:120] for m in (item.get("missing_aspects") or []) if str(m).strip()],
                "action": action,
                "target_paragraph": _safe_paragraph(item.get("target_paragraph"), len(draft_paragraphs)),
            })
        return {
            "topics": topics,
            "structure_note": str(payload.get("structure_note") or "")[:300],
        }


def _safe_paragraph(value, paragraph_count: int) -> int:
    try:
        paragraph = int(value)
    except (TypeError, ValueError):
        return 0
    return paragraph if 0 <= paragraph <= paragraph_count else 0


def _strip_plan_for_qa(narrative_plan: dict) -> dict:
    """QA 输入:完整传递规划(不截断话题/条数,语义完整性优先;
    上下文容量由调用侧 _fit_to_context_budget 控制)。"""
    topics = []
    for topic in narrative_plan.get("topics") or []:
        topics.append({
            "topic_id": topic.get("topic_id", ""),
            "name": topic.get("name", ""),
            "purpose": topic.get("purpose", ""),
            "core_question": topic.get("core_question", ""),
            "core_message": topic.get("core_message", ""),
            "detail_level": topic.get("detail_level", ""),
            "completion_criteria": topic.get("completion_criteria", []),
            "expected_content": topic.get("expected_content", ""),
            "fact_ids": topic.get("fact_ids", []),
            "inference_ids": topic.get("inference_ids", []),
            "discourse_flow": topic.get("discourse_flow", []),
        })
    return {
        "chapter_title": narrative_plan.get("chapter_title", ""),
        "central_message": narrative_plan.get("central_message", ""),
        "logic_order": narrative_plan.get("logic_order", []),
        "subsections": narrative_plan.get("subsections", []),
        "topics": topics,
        "must_not_claim": narrative_plan.get("must_not_claim", []),
        "core_question": narrative_plan.get("core_question", ""),
        "core_message": narrative_plan.get("core_message", ""),
        "transition_hint": narrative_plan.get("transition_hint", ""),
    }


def _sanitize_plan(payload: dict, chapter_plan: dict, fact_ids: set[int], inference_ids: set[int]) -> dict:
    if not isinstance(payload, dict):
        payload = {}
    topics = []
    for item in payload.get("topics") or []:
        if not isinstance(item, dict):
            continue
        fids = [int(i) for i in item.get("fact_ids") or [] if str(i).isdigit() and int(i) in fact_ids]
        iids = [int(i) for i in item.get("inference_ids") or [] if str(i).isdigit() and int(i) in inference_ids]
        if not fids and not iids:
            continue
        detail = str(item.get("detail_level") or "brief")
        if detail not in {"expand", "brief", "reference"}:
            detail = "brief"
        normalized = normalize_topic(item, fact_ids, inference_ids)
        normalized.update({
            "topic_id": str(item.get("topic_id") or f"T{len(topics) + 1}")[:16],
            "name": str(item.get("name") or "相关事实")[:40],
            "purpose": str(item.get("purpose") or ""),
            "detail_level": detail,
            "expected_content": str(item.get("expected_content") or "")[:240],
            "completion_criteria": [str(c)[:120] for c in (item.get("completion_criteria") or []) if str(c).strip()],
            "relation_to_previous": str(item.get("relation_to_previous") or ""),
            "writing_hint": str(item.get("writing_hint") or "")[:220],
        })
        topics.append(normalized)
    subsections = _sanitize_subsections(payload.get("subsections"), chapter_plan, topics, fact_ids, inference_ids)
    subsections = _normalize_subsection_targets(subsections, int(chapter_plan.get("target_words") or 0))
    subsections = [
        {
            **item,
            "paragraphs": _normalize_paragraph_targets(
                item.get("paragraphs") or [], int(item.get("target_words") or 0),
            ),
        }
        for item in subsections
    ]
    if subsections:
        topics = _topics_from_subsections(subsections)
        for index, subsection in enumerate(subsections, start=1):
            subsection["topic_ids"] = [f"T{index}"]
    if not topics and (fact_ids or inference_ids):
        topics = _fallback_topics(chapter_plan, fact_ids, inference_ids)
    logic_order = [str(item)[:40] for item in payload.get("logic_order") or [] if str(item).strip()]
    if not logic_order:
        logic_order = [topic["name"] for topic in topics]
    evidence_status = _evidence_status(
        payload.get("evidence_status"),
        has_evidence=bool(fact_ids or inference_ids),
    )
    if evidence_status == "unknown" and subsections:
        subsection_statuses = {str(item.get("evidence_status") or "unknown") for item in subsections}
        if subsection_statuses == {"sufficient"}:
            evidence_status = "sufficient"
        elif subsection_statuses == {"insufficient"}:
            evidence_status = "insufficient"
        elif subsection_statuses & {"limited", "insufficient"}:
            evidence_status = "limited"
    return {
        "chapter_title": str(payload.get("chapter_title") or chapter_plan.get("title") or ""),
        "central_message": str(payload.get("central_message") or chapter_plan.get("judgment") or "")[:240],
        "logic_order": logic_order,
        "target_words": int(chapter_plan.get("target_words") or 0),
    "evidence_status": evidence_status,
    "evidence_limited": evidence_status in {"limited", "insufficient"},
    "evidence_reason": str(payload.get("evidence_reason") or "")[:240],
    "handles_missing_information": _coerce_bool(
        payload.get("handles_missing_information"),
        fallback=bool(payload.get("missing_information")),
    ),
        "missing_information": [
            str(item)[:160] for item in payload.get("missing_information") or [] if str(item).strip()
        ],
        "subsections": subsections,
        "topics": topics,
        "background_fact_ids": [
            int(i) for i in payload.get("background_fact_ids") or []
            if str(i).isdigit() and int(i) in fact_ids
        ],
        "must_not_claim": [str(item) for item in payload.get("must_not_claim") or [] if str(item).strip()],
        "transition_hint": str(payload.get("transition_hint") or chapter_plan.get("next_bridge") or "")[:240],
    }


def _safe_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _coerce_bool(value, *, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    return str(value).strip().lower() in {"1", "true", "yes", "是", "需要"}


def _int_ids(values) -> list[int]:
    result = []
    for value in values or []:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def unit_evidence_ids(unit: dict) -> tuple[list[int], list[int]]:
    """Collect evidence explicitly assigned to a semantic unit at every plan depth."""
    facts = _int_ids(unit.get("fact_ids"))
    inferences = _int_ids(unit.get("inference_ids"))
    for paragraph in unit.get("paragraphs") or []:
        if isinstance(paragraph, dict):
            facts.extend(_int_ids(paragraph.get("fact_ids")))
            inferences.extend(_int_ids(paragraph.get("inference_ids")))
    for step in unit.get("discourse_flow") or []:
        if isinstance(step, dict):
            facts.extend(_int_ids(step.get("facts") or step.get("fact_ids")))
            inferences.extend(_int_ids(step.get("inferences") or step.get("inference_ids")))
    return list(dict.fromkeys(facts)), list(dict.fromkeys(inferences))


def _evidence_status(value, *, has_evidence: bool) -> str:
    if not has_evidence:
        return "insufficient"
    status = str(value or "unknown").lower()
    return status if status in {"sufficient", "limited", "insufficient"} else "unknown"


def _sanitize_subsections(raw, chapter_plan: dict, topics: list[dict],
                          fact_ids: set[int], inference_ids: set[int]) -> list[dict]:
    source = raw if isinstance(raw, list) and raw else chapter_plan.get("subsections")
    if not isinstance(source, list):
        return []
    topic_by_name = {_key(t.get("name")): t for t in topics}
    result = []
    for item in source:
        if not isinstance(item, dict):
            continue
        title = _clean_subsection_title(str(item.get("title") or ""))
        if not title:
            continue
        fids = [int(i) for i in item.get("fact_ids") or item.get("primary_fact_ids") or [] if str(i).isdigit() and int(i) in fact_ids]
        iids = [int(i) for i in item.get("inference_ids") or item.get("primary_inference_ids") or [] if str(i).isdigit() and int(i) in inference_ids]
        topic_ids = [str(i)[:16] for i in item.get("topic_ids") or [] if str(i).strip()]
        if not fids and not iids:
            matched = topic_by_name.get(_key(title))
            if matched:
                fids = list(matched.get("fact_ids") or [])
                iids = list(matched.get("inference_ids") or [])
                topic_ids = topic_ids or [matched.get("topic_id", "")]
        detail = str(item.get("detail_level") or "brief")
        if detail not in {"expand", "brief", "reference"}:
            detail = "brief"
        flow = _sanitize_flow(item.get("discourse_flow"), fact_ids, inference_ids)
        paragraphs = _sanitize_paragraph_plan(
            item.get("paragraphs"), fids, iids, fact_ids, inference_ids,
        )
        fids, iids = unit_evidence_ids({
            "fact_ids": fids, "inference_ids": iids,
            "paragraphs": paragraphs, "discourse_flow": flow,
        })
        if not fids and not iids:
            continue
        result.append({
            "title": title[:60],
            "purpose": str(item.get("purpose") or "")[:180],
            "core_message": str(item.get("core_message") or "")[:220],
            "topic_ids": [t for t in topic_ids if t],
            "fact_ids": fids,
            "inference_ids": iids,
            "target_words": max(0, _safe_int(item.get("target_words"))),
            "evidence_status": _evidence_status(
                item.get("evidence_status"), has_evidence=bool(fids or iids)
            ),
            "evidence_reason": str(item.get("evidence_reason") or "")[:240],
            "handles_missing_information": _coerce_bool(
                item.get("handles_missing_information"),
                fallback=bool(item.get("missing_information")),
            ),
            "missing_information": [
                str(value)[:160] for value in item.get("missing_information") or [] if str(value).strip()
            ],
            "detail_level": detail,
            "completion_criteria": [str(c)[:120] for c in (item.get("completion_criteria") or []) if str(c).strip()],
            "discourse_flow": flow,
            "paragraphs": paragraphs,
            "writing_hint": str(item.get("writing_hint") or "")[:180],
        })
    return result


def _sanitize_flow(raw, fact_ids: set[int], inference_ids: set[int]) -> list[dict]:
    result = []
    allowed_roles = {"background", "current_status", "evidence", "analysis", "limitation", "judgment"}
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in allowed_roles:
            continue
        result.append({
            "role": role,
            "facts": [
                int(value) for value in item.get("fact_ids") or item.get("facts") or []
                if str(value).isdigit() and int(value) in fact_ids
            ],
            "inferences": [
                int(value) for value in item.get("inference_ids") or []
                if str(value).isdigit() and int(value) in inference_ids
            ],
        })
    return result


def _sanitize_paragraph_plan(raw, default_facts: list[int], default_inferences: list[int],
                              fact_ids: set[int], inference_ids: set[int]) -> list[dict]:
    """Keep model-designed paragraph boundaries, with one safe semantic fallback."""
    paragraphs: list[dict] = []
    for index, item in enumerate(raw or [], start=1):
        if not isinstance(item, dict):
            continue
        fids = [value for value in _int_ids(item.get("fact_ids")) if value in fact_ids]
        iids = [value for value in _int_ids(item.get("inference_ids")) if value in inference_ids]
        if not fids and not iids:
            continue
        paragraphs.append({
            "paragraph_id": str(item.get("paragraph_id") or f"P{index}")[:16],
            "purpose": str(item.get("purpose") or "组织相关证据并推进当前小节")[:200],
            "argument": str(item.get("argument") or ""),
            "transition": str(item.get("transition") or "")[:160],
            "fact_ids": list(dict.fromkeys(fids)),
            "inference_ids": list(dict.fromkeys(iids)),
            "target_words": max(0, _safe_int(item.get("target_words"))),
        })
    if not paragraphs and (default_facts or default_inferences):
        paragraphs.append({
            "paragraph_id": "P1",
            "purpose": "完成当前语义单元的核心论证",
            "transition": "",
            "fact_ids": list(dict.fromkeys(default_facts)),
            "inference_ids": list(dict.fromkeys(default_inferences)),
            "target_words": 0,
        })
    return paragraphs


def _normalize_paragraph_targets(paragraphs: list[dict], target_words: int) -> list[dict]:
    if not paragraphs or target_words <= 0:
        return paragraphs
    weights = [max(1, _safe_int(item.get("target_words"))) for item in paragraphs]
    total = sum(weights)
    used = 0
    result = []
    for index, (item, weight) in enumerate(zip(paragraphs, weights)):
        amount = target_words - used if index == len(paragraphs) - 1 else round(target_words * weight / total)
        result.append({**item, "target_words": max(0, amount)})
        used += max(0, amount)
    return result


def _topics_from_subsections(subsections: list[dict]) -> list[dict]:
    return [{
        "topic_id": f"T{index}",
        "name": str(item.get("title") or ""),
        "purpose": str(item.get("purpose") or ""),
        "core_question": str(item.get("purpose") or ""),
        "core_message": str(item.get("core_message") or ""),
        "fact_ids": list(item.get("fact_ids") or []),
        "inference_ids": list(item.get("inference_ids") or []),
        "detail_level": str(item.get("detail_level") or "brief"),
        "completion_criteria": list(item.get("completion_criteria") or []),
        "discourse_flow": list(item.get("discourse_flow") or []),
        "writing_hint": str(item.get("writing_hint") or ""),
    } for index, item in enumerate(subsections, start=1)]


def _normalize_subsection_targets(items: list[dict], chapter_target: int) -> list[dict]:
    """Normalize semantic subsection budgets to the authoritative chapter target."""
    if len(items) < 2 or chapter_target <= 0:
        return items
    declared = [max(0, _safe_int(item.get("target_words"))) for item in items]
    if not sum(declared):
        detail_weights = {"expand": 2.0, "brief": 1.0, "reference": 0.5}
        declared = [
            detail_weights.get(str(item.get("detail_level") or "brief"), 1.0)
            * max(1.0, (len(item.get("fact_ids") or []) + len(item.get("inference_ids") or [])) ** 0.5)
            for item in items
        ]
    total = sum(declared) or len(items)
    allocated = []
    used = 0
    for index, (item, weight) in enumerate(zip(items, declared)):
        target = chapter_target - used if index == len(items) - 1 else round(chapter_target * weight / total)
        allocated.append({**item, "target_words": max(0, target)})
        used += max(0, target)
    return allocated


def _clean_subsection_title(text: str) -> str:
    value = strip_heading_prefix(text)
    value = re.split(r"[。！？!?；;\n]", value, maxsplit=1)[0].strip()
    return value


def _key(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def _fallback_topics(chapter_plan: dict, fact_ids: set[int], inference_ids: set[int]) -> list[dict]:
    """Preserve planner-defined semantic units when Narrative LLM output is unusable."""
    title = str(chapter_plan.get("title") or "本章")
    topics = []
    for subsection in chapter_plan.get("subsections") or []:
        if not isinstance(subsection, dict):
            continue
        fids = [
            int(i) for i in subsection.get("fact_ids") or subsection.get("primary_fact_ids") or []
            if str(i).isdigit() and int(i) in fact_ids
        ]
        iids = [
            int(i) for i in subsection.get("inference_ids") or subsection.get("primary_inference_ids") or []
            if str(i).isdigit() and int(i) in inference_ids
        ]
        if not fids and not iids:
            continue
        topics.append({
            "topic_id": f"T{len(topics) + 1}",
            "name": _clean_subsection_title(str(subsection.get("title") or title)),
            "purpose": str(subsection.get("purpose") or chapter_plan.get("judgment") or ""),
            "fact_ids": list(dict.fromkeys(fids)),
            "inference_ids": list(dict.fromkeys(iids)),
            "detail_level": str(subsection.get("detail_level") or "expand"),
            "writing_hint": "围绕该小节目的组织事实、比较关系与分析边界。",
        })
    if topics:
        return topics
    return [{
        "topic_id": "T1",
        "name": _clean_title(title),
        "purpose": str(chapter_plan.get("judgment") or "围绕本章核心问题组织事实。"),
        "fact_ids": sorted(fact_ids),
        "inference_ids": sorted(inference_ids),
        "detail_level": "expand",
        "writing_hint": "围绕中心意思组织事实关系,不要逐条罗列。",
    }]


def _clean_title(text: str) -> str:
    return re.sub(r"^[一二三四五六七八九十0-9]+[、.．]\s*", "", text or "")[:40] or "本章主线"


def _sanitize_document_plan(payload: dict, report_plan: dict,
                             fact_ids: set[int], inference_ids: set[int]) -> dict:
    """Validate model beats; FinalPlan provides a deterministic safe fallback."""
    raw_beats = payload.get("beats") if isinstance(payload, dict) else []
    beats: list[dict] = []
    for index, item in enumerate(raw_beats or [], start=1):
        if not isinstance(item, dict):
            continue
        fids = [value for value in _int_ids(item.get("fact_ids")) if value in fact_ids]
        iids = [value for value in _int_ids(item.get("inference_ids")) if value in inference_ids]
        if not fids and not iids:
            continue
        detail = str(item.get("detail_level") or "brief")
        beats.append({
            "beat_id": str(item.get("beat_id") or f"B{index}")[:16],
            "purpose": str(item.get("purpose") or "组织相关证据并推进全文主线")[:220],
            "transition": str(item.get("transition") or "")[:180],
            "fact_ids": list(dict.fromkeys(fids)),
            "inference_ids": list(dict.fromkeys(iids)),
            "target_words": max(0, _safe_int(item.get("target_words"))),
            "detail_level": detail if detail in {"expand", "brief", "reference"} else "brief",
            "must_not_claim": [str(value)[:160] for value in item.get("must_not_claim") or [] if str(value).strip()],
        })
    if not beats:
        for index, chapter in enumerate(report_plan.get("chapter_plans") or [], start=1):
            fids = [value for value in _int_ids(chapter.get("primary_fact_ids")) if value in fact_ids]
            iids = [value for value in _int_ids(chapter.get("primary_inference_ids")) if value in inference_ids]
            if not fids and not iids:
                continue
            beats.append({
                "beat_id": f"B{index}",
                "purpose": str(chapter.get("judgment") or chapter.get("core_question") or chapter.get("title") or "")[:220],
                "transition": str(chapter.get("relation_to_prev") or "")[:180],
                "fact_ids": fids,
                "inference_ids": iids,
                "target_words": max(0, _safe_int(chapter.get("target_words"))),
                "detail_level": "expand",
                "must_not_claim": [],
                "excluded_content": [str(value)[:160] for value in chapter.get("exclude") or [] if str(value).strip()],
            })
    else:
        # Use FinalPlan's explicit evidence ownership, not title semantics.
        # Complete an existing arc before creating a genuinely missing arc.
        assigned_facts = {value for beat in beats for value in beat["fact_ids"]}
        assigned_inferences = {value for beat in beats for value in beat["inference_ids"]}
        for chapter in report_plan.get("chapter_plans") or []:
            if not isinstance(chapter, dict):
                continue
            missing_facts = [
                value for value in _int_ids(chapter.get("primary_fact_ids"))
                if value in fact_ids and value not in assigned_facts
            ]
            missing_inferences = [
                value for value in _int_ids(chapter.get("primary_inference_ids"))
                if value in inference_ids and value not in assigned_inferences
            ]
            if not missing_facts and not missing_inferences:
                continue
            chapter_facts = set(_int_ids(chapter.get("primary_fact_ids")))
            chapter_inferences = set(_int_ids(chapter.get("primary_inference_ids")))
            matching = [
                beat for beat in beats
                if chapter_facts.intersection(beat["fact_ids"])
                or chapter_inferences.intersection(beat["inference_ids"])
            ]
            if matching:
                owner = max(matching, key=lambda beat: (
                    len(chapter_facts.intersection(beat["fact_ids"]))
                    + len(chapter_inferences.intersection(beat["inference_ids"]))
                ))
                owner["fact_ids"].extend(missing_facts)
                owner["inference_ids"].extend(missing_inferences)
                assigned_facts.update(missing_facts)
                assigned_inferences.update(missing_inferences)
                continue
            beats.append({
                "beat_id": f"B{len(beats) + 1}",
                "purpose": str(chapter.get("judgment") or chapter.get("core_question") or "补足必要证据")[:220],
                "transition": str(chapter.get("relation_to_prev") or "")[:180],
                "fact_ids": missing_facts,
                "inference_ids": missing_inferences,
                "target_words": max(0, _safe_int(chapter.get("target_words"))),
                "detail_level": "brief",
                "must_not_claim": [],
                "excluded_content": [str(value)[:160] for value in chapter.get("exclude") or [] if str(value).strip()],
            })
            assigned_facts.update(missing_facts)
            assigned_inferences.update(missing_inferences)
    if not beats and (fact_ids or inference_ids):
        beats = [{
            "beat_id": "B1",
            "purpose": str(report_plan.get("core_judgment") or report_plan.get("core_question") or "组织可用证据")[:220],
            "transition": "",
            "fact_ids": sorted(fact_ids),
            "inference_ids": sorted(inference_ids),
            "target_words": 0,
            "detail_level": "expand",
            "must_not_claim": [],
        }]
    target = _safe_int((report_plan.get("budget") or {}).get("target_words"))
    if beats and target:
        weights = [max(1, item["target_words"]) for item in beats]
        total_weight = sum(weights)
        used = 0
        for index, (beat, weight) in enumerate(zip(beats, weights)):
            value = target - used if index == len(beats) - 1 else round(target * weight / total_weight)
            beat["target_words"] = max(0, value)
            used += max(0, value)
    paragraphs = _sanitize_document_paragraphs(
        payload.get("paragraphs") if isinstance(payload, dict) else [], beats, target,
    )
    return {
        "composition_mode": "article_beats",
        "central_thread": str(payload.get("central_thread") or report_plan.get("narrative_logic") or "")[:360] if isinstance(payload, dict) else str(report_plan.get("narrative_logic") or "")[:360],
        "beats": beats,
        "paragraphs": paragraphs,
        "closing": str(payload.get("closing") or "")[:240] if isinstance(payload, dict) else "",
        "target_words": target,
    }


def _sanitize_document_paragraphs(raw, beats: list[dict], target_words: int) -> list[dict]:
    """Turn a document-level paragraph design into an auditable write contract."""
    beat_by_id = {str(item.get("beat_id")): item for item in beats}
    paragraphs: list[dict] = []
    covered_beats: set[str] = set()
    for index, item in enumerate(raw or [], start=1):
        if not isinstance(item, dict):
            continue
        beat_ids = [str(value) for value in item.get("beat_ids") or [] if str(value) in beat_by_id]
        if not beat_ids:
            continue
        allowed_facts = {
            value for beat_id in beat_ids for value in _int_ids(beat_by_id[beat_id].get("fact_ids"))
        }
        allowed_inferences = {
            value for beat_id in beat_ids for value in _int_ids(beat_by_id[beat_id].get("inference_ids"))
        }
        fids = [value for value in _int_ids(item.get("fact_ids")) if value in allowed_facts]
        iids = [value for value in _int_ids(item.get("inference_ids")) if value in allowed_inferences]
        paragraphs.append({
            "paragraph_id": str(item.get("paragraph_id") or f"P{index}")[:16],
            "beat_ids": beat_ids,
            "purpose": str(item.get("purpose") or "推进全文叙事")[:220],
            "argument": str(item.get("argument") or ""),
            "transition": str(item.get("transition") or "")[:180],
            "fact_ids": list(dict.fromkeys(fids or sorted(allowed_facts))),
            "inference_ids": list(dict.fromkeys(iids or sorted(allowed_inferences))),
            "target_words": max(0, _safe_int(item.get("target_words"))),
        })
        covered_beats.update(beat_ids)
    # Missing a beat means missing a content boundary. Preserve it as its own
    # paragraph rather than silently allowing the Writer to collapse it.
    for beat in beats:
        beat_id = str(beat.get("beat_id"))
        if beat_id in covered_beats:
            continue
        paragraphs.append({
            "paragraph_id": f"P{len(paragraphs) + 1}",
            "beat_ids": [beat_id],
            "purpose": str(beat.get("purpose") or "推进全文叙事")[:220],
            "transition": str(beat.get("transition") or "")[:180],
            "fact_ids": list(_int_ids(beat.get("fact_ids"))),
            "inference_ids": list(_int_ids(beat.get("inference_ids"))),
            "target_words": max(0, _safe_int(beat.get("target_words"))),
        })
    # A covered beat can still lose evidence when its paragraph lists only a
    # subset. Preserve the selected beat's remaining evidence in that same arc.
    for beat in beats:
        related = [item for item in paragraphs if str(beat.get("beat_id")) in item["beat_ids"]]
        if not related:
            continue
        for key in ("fact_ids", "inference_ids"):
            assigned = {value for item in related for value in item[key]}
            related[0][key].extend(value for value in _int_ids(beat.get(key)) if value not in assigned)
    return _normalize_paragraph_targets(paragraphs, target_words)


def _serializable_memory(memory: dict) -> dict:
    result = dict(memory or {})
    for key in ("used_fact_ids", "used_inference_ids"):
        if isinstance(result.get(key), set):
            result[key] = sorted(result[key])
    return result


narrative_agent = NarrativeAgent()

__all__ = ['NarrativeAgent', 'narrative_agent']
