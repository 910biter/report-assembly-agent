"""Narrative planning: organize facts before drafting prose."""
from __future__ import annotations

import json
import re

from app.agents.base import BaseAgent
from app.planning.structure import normalize_topic, serializable_memory
from app.rendering.headings import strip_heading_prefix
from app.task_artifacts import save_task_artifact
from app.context_budget import ContextSection, build_prompt_from_sections

_SYSTEM = """你是报告章节叙事规划师。你的任务不是写正文,而是在 Writer 写作前组织事实。
严格输出 JSON,不要任何解释:
{
  "chapter_title": "章节标题",
  "core_question": "本章要回答的核心问题(一句话,可被材料证实/证伪)",
  "core_message": "本章核心信息/主线判断(一句话)",
  "central_message": "本章中心意思",
  "evidence_status": "sufficient/limited/insufficient",
  "evidence_reason": "证据对本章目标的承载判断",
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
      "missing_information": ["该小节仍缺少什么"],
      "detail_level": "expand/brief/reference",
      "completion_criteria": ["表达什么才算完成,如:说明入口", "说明操作主体", "说明关键步骤"],
      "discourse_flow": [
        {"role": "background", "fact_ids": [1], "inference_ids": []},
        {"role": "analysis", "fact_ids": [2], "inference_ids": [3]}
      ],
      "writing_hint": "一句话说明如何自然展开"
    }
  ],
  "background_fact_ids": [1],
  "must_not_claim": ["不能写成什么"],
  "transition_hint": "本章与前后章节如何衔接"
}
要求:
1. 只使用输入中真实存在的 fact_ids/inference_ids,不得编造编号。
2. 先解决事实之间的组织关系,再考虑表达形式。
3. 不要把所有事实平均展开;区分主事实、背景事实、仅引用事实。
4. 若证据只能证明规则、要求、结构或缺失项,不得规划为已发生成果。
5. 规划应服务于本章核心问题和全文主线,不是材料清单复述。
6. 事实归属优先未在前文使用的事实(前文已用事实仅在承担新的逻辑作用时复用),
   避免各章节重复使用同一批事实导致后章无内容可写。
7. completion_criteria 由材料内容决定(该话题在材料里能支撑什么就写什么),不按固定模板。
8. 若当前章节规划包含 subsections,应优先继承并校准这些小节;若没有,只有在多个话题层次确实需要分层表达时才生成 subsections。
9. 小节标题必须是结构标题,不能是一句带判断的正文;不得为了格式美观硬设小节。
10. 每个小节的 discourse_flow 只定义必要的论证次序,role 取值:
    background(背景/定义)/ current_status(当前状态/进展)/ evidence(支撑证据/数据)/
    analysis(分析/因果/对比)/ limitation(限制/风险/未解决)/ judgment(判断/结论)。
    flow 按真实逻辑顺序排列,每项 facts 列承担该角色的 fact_ids(可为空表示该角色靠推断/衔接)。
    flow 表达论证次序而不是自然段模板；Writer 可将直接相关的相邻角色组织在同一自然段中。
11. subsections 是唯一的成文语义单元,不要再输出重复的 topics。若有两个及以上小节,每个小节必须明确目的、
    话题、主要事实/推断和目标篇幅,各小节 target_words 总和应接近章节目标。
12. Narrative Plan 不规划自然段数量或逐段骨架。自然段由 Writer 在小节内部根据事实关系、证据密度
    和阅读需要自主组织,避免固定段数与事实逐条罗列。
13. evidence_status 判断“当前事实与推断能否支撑计划目的和目标篇幅”。证据有限时保留原目标预算，
    但明确缺口；不得通过重复、常识扩写或无依据概括把 limited/insufficient 伪装成 sufficient。"""

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
要求:
1. status 依据 Topic 的 completion_criteria 是否已表达,不是字数多少。
2. action 语义:
   - keep: 已完成,无需处理
   - rewrite: 该话题已写但表达有误/不充分,需重写对应段落
   - expand: 该话题部分完成且有证据支撑可继续展开
   - replan: 结构性问题,该话题无法按原计划完成
3. evidence_sufficient=false 且 status=missing 时,action 必须为 keep(证据不足不扩写)。
4. target_paragraph 指向草稿中该话题所在段落编号(1 起);无对应段落的 replan 可为 0。
5. 只判断计划内的话题,不评判文风。"""


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
    ) -> dict:
        from app.config import settings

        fact_ids = {int(f["id"]) for f in facts if f.get("id") is not None}
        inference_ids = {int(i["id"]) for i in inferences if i.get("id") is not None}
        safe_unit_words = max(
            500,
            int(settings.writer_output_tokens * settings.writer_visible_word_token_ratio),
        )
        prompt = (
            f"全文标题:{report_plan.get('title','')}\n"
            f"全文核心判断:{report_plan.get('core_judgment','')}\n"
            f"全文叙事逻辑:{report_plan.get('narrative_logic','')}\n"
            f"本轮更新目标:{report_plan.get('update_instruction','')}\n"
            f"本章目标字数:{int(chapter_plan.get('target_words') or 0)}\n"
            f"单个小节一次生成的建议安全规模:不超过约 {safe_unit_words} 字。"
            "若章节较长且存在多个真实语义层次,应据此规划多个有独立目的的小节;"
            "不得为了切分长度制造无意义小节。\n"
            f"当前章节规划:{json.dumps(chapter_plan, ensure_ascii=False)}\n"
            f"前文记忆:{json.dumps(serializable_memory(report_memory or {}), ensure_ascii=False)}\n"
            + (f"业务/证据边界:\n{business_block}\n" if business_block else "")
            + "可用 Facts:\n"
            + "\n".join(f"{f['id']}. {f.get('content','')}" for f in facts)
            + "\n\n可用 Inferences:\n"
            + "\n".join(f"{i['id']}. {i.get('content','')}" for i in inferences)
            + "\n\n请输出本章 Narrative Plan JSON。"
        )
        try:
            payload = self.generate_json(prompt)
        except Exception:
            payload = {}
        plan = _sanitize_plan(payload, chapter_plan, fact_ids, inference_ids)
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
    if not topics and subsections:
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
        if not fids and not iids:
            continue
        detail = str(item.get("detail_level") or "brief")
        if detail not in {"expand", "brief", "reference"}:
            detail = "brief"
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
            "missing_information": [
                str(value)[:160] for value in item.get("missing_information") or [] if str(value).strip()
            ],
            "detail_level": detail,
            "completion_criteria": [str(c)[:120] for c in (item.get("completion_criteria") or []) if str(c).strip()],
            "discourse_flow": _sanitize_flow(item.get("discourse_flow"), fact_ids, inference_ids),
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


def _serializable_memory(memory: dict) -> dict:
    result = dict(memory or {})
    for key in ("used_fact_ids", "used_inference_ids"):
        if isinstance(result.get(key), set):
            result[key] = sorted(result[key])
    return result


narrative_agent = NarrativeAgent()

__all__ = ['NarrativeAgent', 'narrative_agent']
