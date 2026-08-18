"""Narrative planning: organize facts before drafting prose."""
from __future__ import annotations

import json
import re

from app.agents.base import BaseAgent
from app.planning.structure import normalize_topic, serializable_memory
from app.task_artifacts import save_task_artifact

_SYSTEM = """你是报告章节叙事规划师。你的任务不是写正文,而是在 Writer 写作前组织事实。
严格输出 JSON,不要任何解释:
{
  "chapter_title": "章节标题",
  "core_question": "本章要回答的核心问题(一句话,可被材料证实/证伪)",
  "core_message": "本章核心信息/主线判断(一句话)",
  "central_message": "本章中心意思",
  "dependencies": ["本章依赖的前置章节标题(无则空列表)"],
  "logic_order": ["话题1", "话题2", "..."],
  "subsections": [
    {
      "title": "小节标题",
      "purpose": "该小节解决什么问题",
      "topic_ids": ["T1"],
      "fact_ids": [1,2],
      "inference_ids": [3],
      "detail_level": "expand/brief/reference",
      "completion_criteria": ["表达什么才算完成"]
    }
  ],
  "topics": [
    {
      "topic_id": "T1",
      "name": "话题名称",
      "purpose": "这个话题解决什么问题",
      "core_question": "这个话题要回答的问题",
      "core_message": "这个话题要传达的核心意思(一句话)",
      "fact_ids": [1,2],
      "supporting_fact_ids": [1,2],
      "inference_ids": [3],
      "detail_level": "expand/brief/reference",
      "expected_content": "这个话题预期写出的内容要点",
      "completion_criteria": ["表达什么才算完成,如:说明入口", "说明操作主体", "说明关键步骤"],
      "relation_to_previous": "与上一个话题的逻辑关系",
      "discourse_flow": [
        {"role": "background", "facts": [1]},
        {"role": "current_status", "facts": [2, 3]},
        {"role": "evidence", "facts": [4]},
        {"role": "analysis", "facts": []},
        {"role": "limitation", "facts": [5]}
      ],
      "writing_hint": "如何展开,避免事实罗列"
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
10. 【Discourse Plan——最重要的要求】每个 topic 的 discourse_flow 定义"这段按什么逻辑组织",role 取值:
    background(背景/定义)/ current_status(当前状态/进展)/ evidence(支撑证据/数据)/
    analysis(分析/因果/对比)/ limitation(限制/风险/未解决)/ judgment(判断/结论)。
    flow 按逻辑顺序排列(背景→状态→证据→分析→限制→判断),每项 facts 列承担该角色的
    fact_ids(可为空表示该角色靠推断/衔接);Writer 严格按 flow 顺序写,一段一个角色,
    禁止把 flow 打散或按 fact 编号罗列。
11. dependencies 列出本章写作前必须先完成的前置章节(如"机制概述"是"威胁分析"的前置),
    用于 Writer 只注入依赖章节的记忆,防止上下文膨胀。"""

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

    def plan_chapter(
        self,
        task_id: str,
        chapter_plan: dict,
        report_plan: dict,
        facts: list[dict],
        inferences: list[dict],
        business_block: str = "",
        report_memory: dict | None = None,
    ) -> dict:
        fact_ids = {int(f["id"]) for f in facts if f.get("id") is not None}
        inference_ids = {int(i["id"]) for i in inferences if i.get("id") is not None}
        prompt = (
            f"全文标题:{report_plan.get('title','')}\n"
            f"全文核心判断:{report_plan.get('core_judgment','')}\n"
            f"全文叙事逻辑:{report_plan.get('narrative_logic','')}\n"
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
        from app.config import settings

        budget = getattr(settings, "max_context_chars", 12000)
        draft_block = "\n".join(f"[P{i + 1}] {p}" for i, p in enumerate(draft_paragraphs))
        fact_block = "\n".join(f"{f['id']}. {f.get('content', '')}" for f in facts)
        plan_block = json.dumps(plan_for_qa, ensure_ascii=False)
        draft_block, fact_block = _fit_to_context_budget([draft_block, fact_block], budget)
        prompt = (
            f"章节:{chapter_title}\n\n"
            "章节草稿(按段落编号):\n"
            + draft_block
            + "\n\nNarrative Plan:\n"
            + plan_block
            + "\n\n支撑 Facts:\n" + (fact_block or "无")
            + "\n\n请输出各 Topic 完成度判断 JSON。"
        )
        try:
            payload = self.generate_json(prompt, system=_QA_SYSTEM)
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


def _fit_to_context_budget(blocks: list[str], budget_chars: int, overhead_chars: int = 800) -> list[str]:
    """按剩余上下文容量动态截取各文本块(不预设业务阈值)。

    超长块截断到剩余容量;预算耗尽后剩余块置空。
    """
    remaining = max(0, budget_chars - overhead_chars)
    result: list[str] = []
    for block in blocks:
        if remaining <= 0:
            result.append("")
            continue
        if len(block) <= remaining:
            result.append(block)
            remaining -= len(block)
        else:
            result.append(block[:remaining])
            remaining = 0
    return result


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
        "dependencies": narrative_plan.get("dependencies", []),
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
    if not topics and fact_ids:
        topics = _fallback_topics(chapter_plan, fact_ids)
    logic_order = [str(item)[:40] for item in payload.get("logic_order") or [] if str(item).strip()]
    if not logic_order:
        logic_order = [topic["name"] for topic in topics]
    subsections = _sanitize_subsections(payload.get("subsections"), chapter_plan, topics, fact_ids, inference_ids)
    return {
        "chapter_title": str(payload.get("chapter_title") or chapter_plan.get("title") or ""),
        "central_message": str(payload.get("central_message") or chapter_plan.get("judgment") or "")[:240],
        "logic_order": logic_order,
        "subsections": subsections,
        "topics": topics,
        "background_fact_ids": [
            int(i) for i in payload.get("background_fact_ids") or []
            if str(i).isdigit() and int(i) in fact_ids
        ],
        "must_not_claim": [str(item) for item in payload.get("must_not_claim") or [] if str(item).strip()],
        "transition_hint": str(payload.get("transition_hint") or chapter_plan.get("next_bridge") or "")[:240],
    }


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
            "topic_ids": [t for t in topic_ids if t],
            "fact_ids": fids,
            "inference_ids": iids,
            "detail_level": detail,
            "completion_criteria": [str(c)[:120] for c in (item.get("completion_criteria") or []) if str(c).strip()],
        })
    return result


def _clean_subsection_title(text: str) -> str:
    value = re.sub(r"^\s*(?:\d+(?:\.\d+)+|[（(][一二三四五六七八九十]+[）)]|[一二三四五六七八九十]+[、.])\s*", "", text or "").strip()
    value = re.split(r"[。！？!?；;\n]", value, maxsplit=1)[0].strip()
    return value


def _key(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def _fallback_topics(chapter_plan: dict, fact_ids: set[int]) -> list[dict]:
    title = str(chapter_plan.get("title") or "本章")
    return [{
        "name": _clean_title(title),
        "purpose": str(chapter_plan.get("judgment") or "围绕本章核心问题组织事实。"),
        "fact_ids": sorted(fact_ids),
        "inference_ids": [],
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
