"""Writer Agent:基于 Report Blueprint + SectionPlan 逐章生成,带 Report Memory 跨章一致性。

流程(每章):
1. 章节上下文:Context Manager 检索本章相关事实/推断
2. SectionPlan:先规划本章(目的/核心问题/要点/所需事实/禁止内容/结构),再生成
3. 生成:按 SectionPlan 组织,注入蓝图主线 + Report Memory(前章摘要/已用事实/统一术语)
4. 更新 Report Memory:章节摘要、已用事实、术语

硬约束:句子必须引用真实 fact/inference;无依据句子不输出;不重复其他章节内容。
"""

import json
import re
import time
import uuid
from difflib import SequenceMatcher

from app.agents.base import BaseAgent
from app.planning.narrative import narrative_agent
from app.db import session_scope
from app.infrastructure.orm import Base, ORMReport, ORMSentence, ORMTaskArtifact
from sqlalchemy import select, update, delete
from app.models import Report
from app.policy import policy_prompt_block
from app.planning.structure import serializable_memory
from app.planning.structure import order_chapters
from app.planning.scale import normalize_execution_plan
from app.rendering.headings import has_heading_prefix, strip_heading_prefix
from app.document_shape import normalize_composition_mode
from app.task_artifacts import latest_task_artifact, save_task_artifact
from app.token_monitor import update_call_funnel, update_call_metrics, update_call_products
from app.config import settings
from app.context_budget import (
    ContextSection,
    build_prompt_from_sections,
    count_tokens,
    publish_context_audit,
    tokenizer_method,
    truncate_tokens,
)
from app.writing.scale_execution import (
    assess_chapter_output,
    measure_text_words,
)


ORMSentenceFact = Base.metadata.tables["report_sentence_fact"]
ORMSentenceInference = Base.metadata.tables["report_sentence_inference"]


_SYSTEM = """你是情报报告撰稿人。依据 Narrative Plan、ChapterPlan 与机构风格,撰写成文的自然语言报告。
每次只撰写一个章节,严格输出 JSON,不要任何解释:
{"paragraphs": [{"sentences": [{"text": "句子内容", "fact_ids": [1], "inference_ids": []}]}]}
要求:
1. 严格按 Narrative Plan 与 ChapterPlan 的核心问题、事实主次和逻辑顺序组织内容,不得偏离
2. 不重复 Report Memory 中"前文已表述"的内容(其他章节/前章摘要)
3. 每个段落围绕一个核心观点,事实与分析自然融合,判断必须有依据
4. 事实句引用 fact_ids,推断句引用 inference_ids,id 必须真实存在
5. 正文不得出现来源标签、内部编号或证据标注;引用关系只写在 JSON 的 fact_ids/inference_ids 中
6. 只撰写当前指定章节,不要输出其他章节内容
7. 承上启下、章节导语等过渡句可以没有引用,但应保持少量且不得编造事实
8. 若当前材料包缺少真实成果材料,不得写成“已取得显著成效/完成某项成果/满意度提升”等未被材料直接证明的结论
9. 段落、句式、详略、表格或分项表达由 ChapterPlan、模板风格、材料信息量和分层策略共同决定;不要机械套用固定格式
10. 不要把无关 Fact 压缩拼接进同一句;一段一主题,一个句子通常只承担一个核心事实或一个直接相关的事实组
11. 具体事实优先,少使用“机制完善、顶层定标、全面就位”等没有新增信息的抽象套话
12. 不要把事实清单直接排列成正文;每段应有主题句、必要解释和自然承接,使读者能理解事实之间的关系
13. Narrative Plan 中 detail_level=expand 的话题应适度展开;brief/reference 只简要承接,不得平均铺陈
14. paragraphs 是文档自然段,sentences 是段内溯源单位;同一自然段中的多句话应放入同一个 paragraphs[].sentences,不要把每句话都拆成一个 paragraph
15. ChapterPlan 的目标字数是本章有效规模目标。证据充分时应实质接近该目标并完成各段预算；
    不得用重复表述、空泛套话或虚构事实凑字数，证据不足时可以低于目标。"""


def _filter_facts_by_roles(
    facts: list[dict],
    allowed_roles: list[str],
    limit: int | None = None,
) -> list[dict]:
    if not allowed_roles:
        return facts if limit is None else facts[:limit]
    filtered = [f for f in facts if set(f.get("source_roles") or []) & set(allowed_roles)]
    return (filtered or facts) if limit is None else (filtered or facts)[:limit]


def _filter_inferences_by_facts(
    inferences: list[dict],
    facts: list[dict],
    limit: int | None = None,
) -> list[dict]:
    fact_ids = {int(f.get("id")) for f in facts if f.get("id") is not None}
    if not fact_ids:
        return inferences[:limit]
    result = [i for i in inferences if fact_ids & set(i.get("based_fact_ids") or [])]
    return (result or inferences)[:limit]


def _business_block(task_profile: dict | None, chapter_plan: dict) -> str:
    if not task_profile:
        return ""
    lines = [f"材料包模式: {task_profile.get('report_mode', 'generic')}"]
    if task_profile.get("report_mode") == "requirement_summary":
        lines.append("当前材料未证明实际完成过程或结果时，不得虚构已发生事项、成效提升或量化业绩。")
        lines.append("如果材料只能证明规则、结构、字段、标准或缺失项，应写成连贯的说明、边界判断和后续动作建议,不能退化为事实清单。")
        lines.append("模板材料主要用于版式、结构和写作口吻学习；除确有内容约束外，不应作为正文事实反复表述。")
    if chapter_plan.get("allowed_roles"):
        lines.append("本章允许使用的材料角色: " + "、".join(chapter_plan.get("allowed_roles") or []))
    if task_profile.get("missing_inputs") and _chapter_likely_handles_gaps(chapter_plan):
        lines.append("本章可结合规划目标说明当前缺失信息: " + "；".join(task_profile.get("missing_inputs") or []))
    return "\n".join(lines)


def _chapter_likely_handles_gaps(chapter_plan: dict) -> bool:
    """Infer whether missing-information guidance belongs here from runtime plan text."""
    text = " ".join(
        str(item)
        for item in [
            chapter_plan.get("title", ""),
            chapter_plan.get("judgment", ""),
            *(chapter_plan.get("questions") or []),
            *(chapter_plan.get("required_facts") or []),
        ]
    )
    return any(term in text for term in ("缺失", "不足", "待补", "风险", "边界", "限制", "后续", "建议"))


def _merge_required_facts(chapter_facts: list[dict], all_facts: list[dict],
                          required_ids: set[int], limit: int) -> list[dict]:
    if not required_ids:
        return chapter_facts[:limit]
    by_id = {int(f.get("id")): f for f in all_facts if f.get("id") is not None}
    required = [by_id[fid] for fid in required_ids if fid in by_id]
    seen = {int(f.get("id")) for f in required if f.get("id") is not None}
    merged = required + [f for f in chapter_facts if int(f.get("id", -1)) not in seen]
    return merged[:max(limit, len(required))]






def _int_ids(values) -> list[int]:
    result = []
    for value in values or []:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _json_dumps(value, limit: int | None = None) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return truncate_tokens(text, limit) if limit else text


def _text_length(text: str) -> int:
    """Approximate Chinese report length in the same unit used by scale stats."""
    return measure_text_words(text)







def _discourse_plan_block(narrative_plan: dict | None) -> str:
    """Narrative Plan 的 discourse_flow → prompt 块(每个 topic 的逻辑流)。"""
    if not narrative_plan:
        return ""
    topics = narrative_plan.get("topics") or []
    lines = []
    for idx, topic in enumerate(topics, start=1):
        flow = topic.get("discourse_flow") or []
        if not flow:
            continue
        head = f"{idx}. 话题「{topic.get('name', '')}」core_question: {topic.get('core_question', '')}"
        lines.append(head)
        lines.append(f"   core_message: {topic.get('core_message', '')}")
        for step in flow:
            role = str(step.get("role") or "")
            facts = step.get("facts") or []
            lines.append(f"   [{role}] facts {facts}")
    return "\n".join(lines)


def _extract_chapter_memory(chapter_title: str, narrative_plan: dict,
                            chapter_text: list[str], qa_result: dict) -> dict:
    """本章写后抽取分级记忆(SurveyGen-I 收敛):
    - unified_terms:已定义术语(保持定义一致)
    - expressed_points:已表达观点(避免原样重复)
    - formed_judgments:已形成判断(引用/发展,不重新论证)
    - unresolved_issues:未解决问题(后续章节优先回应;来自 QA 判 partial/missing 的 topic)
    - fact_roles:fact_id -> 已承担的逻辑角色(discourse role,Facts 复用须承担新角色)
    """
    terms, points, judgments, unresolved = [], [], [], []
    fact_roles: dict[int, str] = {}
    topics = (narrative_plan or {}).get("topics") or []
    qa_topics = {(t.get("topic_id") or ""): t for t in (qa_result or {}).get("topics") or []}
    seen_terms: set[str] = set()
    for topic in topics:
        tid = topic.get("topic_id") or ""
        cm = str(topic.get("core_message") or "").strip()
        if cm:
            points.append(f"[{chapter_title}] {cm}")
            judgments.append(f"[{chapter_title}] {cm}")
        name = str(topic.get("name") or "").strip()
        if name and name not in seen_terms:
            seen_terms.add(name)
            terms.append({"term": name, "definition": cm[:80]})
        # 未解决:QA 判 partial/missing 且证据不足的 topic 的 core_question 进 unresolved
        qa_t = qa_topics.get(tid) or {}
        if qa_t.get("status") in ("partial", "missing") and qa_t.get("evidence_sufficient") is not False:
            q = str(topic.get("core_question") or topic.get("purpose") or "").strip()
            if q:
                unresolved.append(f"[{chapter_title}] {q}")
        # Fact 逻辑角色:discourse_flow 中每个 fact 承担的角色
        for step in topic.get("discourse_flow") or []:
            role = str(step.get("role") or "")
            for fid in step.get("facts") or []:
                try:
                    fact_roles[int(fid)] = role
                except (TypeError, ValueError):
                    pass
    return {
        "terms": terms[-12:],
        "points": points[-12:],
        "judgments": judgments[-8:],
        "unresolved": unresolved[-8:],
        "fact_roles": fact_roles,
    }


def _split_structured_items(text: str) -> list[str]:
    """Keep checklist items as separate report sentences for editing/export."""
    if "\n" not in text:
        return [_normalize_report_sentence(text.strip())]
    parts = [_normalize_report_sentence(p.strip()) for p in text.splitlines() if p.strip()]
    if any(_is_list_item(p) for p in parts):
        return parts
    return [_normalize_report_sentence(text.strip())]


def _is_list_item(text: str) -> bool:
    return bool(re.match(r"^(?:[•\-*]|[一二三四五六七八九十]+[、.]|\d+[、.])\s*", text.strip()))


def _normalize_report_sentence(text: str) -> str:
    """Remove UI/Markdown-style bullets from formal report prose."""
    value = re.sub(r"^\s*[•\-*]\s*", "", text or "").strip()
    return value


def _planned_subsection_titles(narrative_plan: dict) -> list[str]:
    titles = []
    for item in (narrative_plan or {}).get("subsections") or []:
        title = strip_heading_prefix(str(item.get("title") or ""))
        if title:
            titles.append(title)
    return titles


def _style_sample_type(unit_plan: dict, chapter_index: int, chapter_count: int) -> str:
    """Map Narrative discourse roles to a style-retrieval intent."""
    roles = {
        str(item.get("role") or "").strip().lower()
        for item in (unit_plan or {}).get("discourse_flow") or []
        if isinstance(item, dict)
    }
    if roles & {"limitation", "risk"}:
        return "risk"
    if roles & {"analysis", "judgment"}:
        return "analysis"
    if chapter_index == 1:
        return "opening"
    if chapter_index == chapter_count:
        return "conclusion"
    return "fact"


def _subsection_generation_units(narrative_plan: dict, chapter_target: int,
                                 minimum_ratio: float = 0.0) -> list[dict]:
    """Turn semantic subsection planning into one-shot Writer units.

    This is not length-based batching: each unit is an indivisible Narrative
    subsection. A chapter without a real multi-subsection structure stays one
    Writer call, and a single orphan subsection is flattened into the chapter.
    """
    subsections = [
        dict(item) for item in (narrative_plan or {}).get("subsections") or []
        if isinstance(item, dict) and str(item.get("title") or "").strip()
    ]
    if len(subsections) < 2:
        return [{
            "index": 1,
            "count": 1,
            "title": "",
            "target_words": max(0, int(chapter_target or 0)),
            "minimum_words": round(max(0, int(chapter_target or 0)) * max(0.0, minimum_ratio)),
            "plan": None,
            "evidence_status": str((narrative_plan or {}).get("evidence_status") or "unknown"),
            "evidence_reason": str((narrative_plan or {}).get("evidence_reason") or ""),
            "missing_information": list((narrative_plan or {}).get("missing_information") or []),
        }]

    declared = [max(0, int(item.get("target_words") or 0)) for item in subsections]
    if not sum(declared):
        detail_weight = {"expand": 2.0, "brief": 1.0, "reference": 0.5}
        declared = [
            detail_weight.get(str(item.get("detail_level") or "brief"), 1.0)
            * max(1.0, (len(item.get("fact_ids") or []) + len(item.get("inference_ids") or [])) ** 0.5)
            for item in subsections
        ]
    total_weight = sum(declared) or len(subsections)
    used = 0
    result = []
    for index, (subsection, weight) in enumerate(zip(subsections, declared), start=1):
        target = (
            max(0, int(chapter_target or 0) - used)
            if index == len(subsections)
            else max(0, round(int(chapter_target or 0) * weight / total_weight))
        )
        used += target
        result.append({
            "index": index,
            "count": len(subsections),
            "title": strip_heading_prefix(str(subsection.get("title") or "")),
            "target_words": target,
            "minimum_words": round(target * max(0.0, minimum_ratio)),
            "plan": subsection,
            "evidence_status": str(subsection.get("evidence_status") or "unknown"),
            "evidence_reason": str(subsection.get("evidence_reason") or ""),
            "missing_information": list(subsection.get("missing_information") or []),
        })
    return result


def _focused_unit_evidence(
    facts: list[dict], inferences: list[dict], unit_plan: dict | None, chapter_plan: dict,
) -> tuple[list[dict], list[dict]]:
    """Give a unit its planned evidence instead of the entire chapter pool."""
    unit_plan = unit_plan or {}
    fact_ids = set(_int_ids(unit_plan.get("fact_ids")))
    inference_ids = set(_int_ids(unit_plan.get("inference_ids")))
    if not fact_ids:
        fact_ids = set(_int_ids(chapter_plan.get("primary_fact_ids")))
    if not inference_ids:
        inference_ids = set(_int_ids(chapter_plan.get("primary_inference_ids")))
    selected_facts = [item for item in facts if int(item.get("id") or 0) in fact_ids] if fact_ids else list(facts)
    selected_inferences = [item for item in inferences if int(item.get("id") or 0) in inference_ids] if inference_ids else list(inferences)
    # Older plans may not contain assignments; preserve their usable fallback.
    return selected_facts or list(facts), selected_inferences or list(inferences)


def _subsection_execution_hint(titles: list[str]) -> str:
    if not titles:
        return (
            "小标题策略:当前 Narrative Plan 未规划小节标题。Writer 不得自行创造 2.1/2.2 等编号小标题;"
            "如需分层,用自然段主题句承接。"
        )
    lines = "\n".join(f"- {title}" for title in titles)
    return (
        "小节结构(规划约束):\n"
        f"{lines}\n"
        "当前小节及其边界由 Narrative Plan 决定。Writer 只写当前小节正文,不要输出小标题;"
        "系统会在首个有效正文段前确定性渲染规划标题。"
    )


def _paragraph_execution_hint(paragraphs: list[dict] | None) -> str:
    """Expose the narrative paragraph contract without turning it into headings."""
    if not paragraphs:
        return ""
    lines = []
    for index, item in enumerate(paragraphs, start=1):
        lines.append(
            f"P{index}: {item.get('purpose', '')}；"
            f"核心事实 {item.get('fact_ids', [])}；推断 {item.get('inference_ids', [])}；"
            f"约 {item.get('target_words', 0)} 字"
        )
    return (
        "段落执行计划(语义边界，不是小标题):\n- " + "\n- ".join(lines)
        + "\n按上述顺序各输出一个 paragraphs 元素；不得把不同段落计划压成同一长段。"
    )


def _evidence_execution_hint(generation_unit: dict) -> str:
    status = str((generation_unit or {}).get("evidence_status") or "unknown").lower()
    reason = str((generation_unit or {}).get("evidence_reason") or "").strip()
    missing = [str(item) for item in (generation_unit or {}).get("missing_information") or [] if str(item).strip()]
    details = (f" 判断依据:{reason}" if reason else "") + (f" 证据缺口:{'；'.join(missing)}" if missing else "")
    if status == "sufficient":
        minimum = int((generation_unit or {}).get("minimum_words") or 0)
        floor = f"本次完整成稿不得少于 {minimum} 字，不要提前收束。" if minimum else "实质接近目标篇幅。"
        return "证据状态:充分。围绕核心问题充分解释事实关系、比较、机制和边界；" + floor + details
    if status == "limited":
        return (
            "证据状态:有限。优先写清已有具体事实及其关系、适用边界和可靠推断；允许低于目标篇幅，"
            "不得重复事实、泛化常识或制造结论凑字数。必要时简洁说明关键缺口。" + details
        )
    if status == "insufficient":
        return (
            "证据状态:不足。只陈述能够直接追溯的内容，并明确现有材料无法支持的关键问题；"
            "不追求写满目标篇幅，不得以背景常识、套话或重复内容替代缺失证据。" + details
        )
    return "证据状态:未明确。以事实可追溯和不虚构为优先，能充分解释则展开，不能支撑时允许欠填。"


def _split_embedded_subheading(text: str, allowed_titles: list[str] | None = None) -> list[dict]:
    """Split model output like '2.3 小标题。正文...' into structure + prose.

    This is deterministic structure repair. It does not invent headings or
    rewrite facts; it only prevents headings from being rendered as body text.
    """
    value = str(text or "").strip()
    split = _extract_embedded_subheading(value)
    if not split:
        return [{"text": value}]
    heading, body = split
    clean_heading = _clean_generated_subheading(heading)
    if not _heading_allowed(clean_heading, allowed_titles or []):
        return [{"text": body}] if body else []
    parts = [{"text": clean_heading, "source_level": "SUBHEADING"}]
    if body:
        parts.append({"text": body})
    return parts


def _clean_generated_subheading(text: str) -> str:
    value = strip_heading_prefix(text)
    value = re.split(r"[。！？!?；;\n]", value, maxsplit=1)[0].strip()
    return value


def _extract_embedded_subheading(text: str) -> tuple[str, str] | None:
    if not has_heading_prefix(text):
        return None
    for mark in ("。", "；", "！", "？"):
        index = text.find(mark)
        if 0 < index <= 80 and index < len(text) - 1:
            return text[:index], text[index + 1:].strip()
    return None


def _heading_allowed(heading: str, allowed_titles: list[str]) -> bool:
    if not allowed_titles:
        return False
    key = _structure_key(heading)
    allowed = {_structure_key(title) for title in allowed_titles}
    return key in allowed


def _is_duplicate_structure_sentence(text: str, chapter_title: str, report_title: str) -> bool:
    """Drop title/chapter echoes that the model sometimes emits as body prose."""
    value = _structure_key(text)
    if not value:
        return False
    candidates = {_structure_key(chapter_title), _structure_key(report_title)}
    chapter_no = re.match(r"^([一二三四五六七八九十]+)[、.]\s*(.+)$", str(chapter_title or "").strip())
    if chapter_no:
        candidates.add(_structure_key(chapter_no.group(2)))
        candidates.add(_structure_key(f"{_chinese_number_to_int(chapter_no.group(1))}. {chapter_no.group(2)}"))
    return value in {item for item in candidates if item}


def _structure_key(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"^[第]?[一二三四五六七八九十]+[章节部分、.]\s*", "", value)
    value = re.sub(r"^\d+(?:\.\d+)*[、.)]?\s*", "", value)
    value = re.sub(r"[。．.\s]+$", "", value)
    return re.sub(r"\s+", "", value)


def _chinese_number_to_int(text: str) -> int:
    mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    value = str(text or "")
    if value == "十":
        return 10
    if value.startswith("十"):
        return 10 + mapping.get(value[-1], 0)
    if "十" in value:
        left, _, right = value.partition("十")
        return mapping.get(left, 1) * 10 + mapping.get(right, 0)
    return mapping.get(value, 0)


def _writer_policy_only(policy: dict) -> dict:
    if not policy:
        return {}
    return {
        "hard_guardrails": policy.get("hard_guardrails") or [],
        "domain_policy": policy.get("domain_policy") or [],
        "template_policy": policy.get("template_policy") or {},
        "writer_policy": policy.get("writer_policy") or [],
    }


def _writer_prompt_token_budget() -> int:
    """Serving-derived Writer input budget in tokens."""
    from app.runtime_profiles import stage_input_budget_tokens
    return stage_input_budget_tokens("writer")


def _pack_writer_evidence(
    fact_lines: list[str],
    inference_lines: list[str],
    available_tokens: int,
) -> tuple[list[str], list[str]]:
    """Pack complete traceable lines, preserving both facts and inferences."""
    available = max(0, int(available_tokens or 0))

    def take(lines: list[str], budget: int) -> tuple[list[str], int]:
        selected: list[str] = []
        used = 0
        for line in lines:
            cost = count_tokens(line + "\n")
            if selected and used + cost > budget:
                break
            if cost > budget:
                continue
            selected.append(line)
            used += cost
        return selected, used

    inference_share = available // 3 if inference_lines else 0
    selected_inferences, inference_used = take(inference_lines, inference_share)
    selected_facts, fact_used = take(fact_lines, available - inference_used)
    remaining = available - inference_used - fact_used
    if remaining > 0 and len(selected_inferences) < len(inference_lines):
        extra, _ = take(inference_lines[len(selected_inferences):], remaining)
        selected_inferences.extend(extra)
    return selected_facts, selected_inferences


def _article_evidence_ids(chapters: list[dict]) -> tuple[set[int], set[int]]:
    """Collect FinalPlan evidence assignments without using title semantics."""
    fact_ids: set[int] = set()
    inference_ids: set[int] = set()
    for chapter in chapters or []:
        if not isinstance(chapter, dict):
            continue
        fact_ids.update(_int_ids(chapter.get("primary_fact_ids")))
        inference_ids.update(_int_ids(chapter.get("primary_inference_ids")))
        for subsection in chapter.get("subsections") or []:
            if isinstance(subsection, dict):
                fact_ids.update(_int_ids(subsection.get("primary_fact_ids") or subsection.get("fact_ids")))
                inference_ids.update(_int_ids(subsection.get("primary_inference_ids") or subsection.get("inference_ids")))
    return fact_ids, inference_ids


def _article_beat_groups(beats: list[dict], safe_words: int) -> list[list[dict]]:
    """Partition only at planned article-beat boundaries when output is physical-limit bound."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    current_words = 0
    for beat in beats or []:
        words = max(1, int(beat.get("target_words") or 0))
        if current and current_words + words > safe_words:
            groups.append(current)
            current, current_words = [], 0
        current.append(beat)
        current_words += words
    if current:
        groups.append(current)
    return groups or [[]]


def _chapter_position(chapter_index: int, local_position: int) -> int:
    return max(1, int(chapter_index or 1)) * 10000 + max(1, int(local_position or 1))


def _coerce_paragraphs(payload: dict) -> list[dict]:
    """Normalize model JSON into paragraphs[{sentences:[...]}] without crashing.

    JSON mode guarantees syntactic JSON, not business schema. If the model returns
    strings or alternate section wrappers, keep only objects we can safely trace.
    """
    raw = payload.get("paragraphs")
    if not raw and isinstance(payload.get("sections"), list):
        raw = []
        for section in payload.get("sections") or []:
            if isinstance(section, dict) and isinstance(section.get("paragraphs"), list):
                raw.extend(section.get("paragraphs") or [])
    if isinstance(raw, dict):
        raw = [raw]
    if isinstance(raw, str):
        raw = [{"sentences": [{"text": raw, "fact_ids": [], "inference_ids": []}]}]
    if not isinstance(raw, list):
        raw = []
    result: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            sentences = item.get("sentences")
            if isinstance(sentences, dict):
                item["sentences"] = [sentences]
            elif isinstance(sentences, str):
                item["sentences"] = [{"text": sentences, "fact_ids": [], "inference_ids": []}]
            elif not isinstance(sentences, list):
                text = str(item.get("text", "")).strip()
                item["sentences"] = [{"text": text, "fact_ids": [], "inference_ids": []}] if text else []
            paragraph_fact_ids = item.get("fact_ids") or []
            paragraph_inference_ids = item.get("inference_ids") or []
            normalized_sentences = []
            for sentence in item.get("sentences") or []:
                normalized = _normalize_sentence_item(sentence, paragraph_fact_ids, paragraph_inference_ids)
                if normalized:
                    normalized_sentences.append(normalized)
            if normalized_sentences:
                item["sentences"] = normalized_sentences
                result.append(item)
        elif isinstance(item, str):
            result.append({"sentences": [{"text": item, "fact_ids": [], "inference_ids": []}]})
    if result:
        return result

    if isinstance(payload.get("sentences"), list):
        by_paragraph: dict[int, list] = {}
        for sentence in payload.get("sentences") or []:
            if not isinstance(sentence, dict):
                continue
            try:
                paragraph_number = int(sentence.get("paragraph") or 1)
            except (TypeError, ValueError):
                paragraph_number = 1
            by_paragraph.setdefault(max(1, paragraph_number), []).append(sentence)
        return [
            {"sentences": by_paragraph[key]}
            for key in sorted(by_paragraph)
            if by_paragraph[key]
        ]
    return []


def _normalize_sentence_item(sentence, paragraph_fact_ids=None, paragraph_inference_ids=None) -> dict | None:
    if isinstance(sentence, str):
        text = sentence.strip()
        return {"text": text, "fact_ids": paragraph_fact_ids or [], "inference_ids": paragraph_inference_ids or []} if text else None
    if not isinstance(sentence, dict):
        return None
    text = str(sentence.get("text", "")).strip()
    if not text:
        return None
    result = dict(sentence)
    result["text"] = text
    if not result.get("fact_ids") and paragraph_fact_ids:
        result["fact_ids"] = paragraph_fact_ids
    if not result.get("inference_ids") and paragraph_inference_ids:
        result["inference_ids"] = paragraph_inference_ids
    return result


class WriterAgent(BaseAgent):
    # Writer is the only workflow Agent that may need a long-form response.
    output_token_limit = settings.writer_output_tokens
    name = "writer"
    role = _SYSTEM
    max_retries = 1
    # Narrative has already made the semantic decisions. Writer should spend
    # its generation budget on report prose rather than hidden reasoning.
    thinking = False

    def write(self, plan: dict, facts: list[dict], inferences: list[dict],
              style_block: str, profile_id: int | None = None, cm=None,
              institution_rules: dict | None = None, task_profile: dict | None = None,
              progress_callback=None,
              existing_report_id: int | None = None, report_callback=None,
              chapter_callback=None, report_policy: dict | None = None,
              task_id: str = "",
              target_chapter_titles: list[str] | None = None, run_id: str = "") -> Report:
        """按 ReportPlan + ChapterPlan[] 逐章执行生成(不重复规划)。

        ChapterPlan 决定本章信息需求:章节标题 + 核心问题 + 核心判断 + 所需事实
        共同构建检索 query,使文章逻辑与材料检索打通。
        institution_rules:机构硬性要求(must_include 等),注入 Writer 约束全文覆盖。
        """
        self._task_id = task_id
        self._run_id = run_id
        style_variant = None
        if profile_id is not None:
            try:
                from app.memory.style import get_variant

                style_variant = get_variant(int(profile_id))
            except Exception:
                style_variant = None
        # Incremental revisions may reuse a plan created before the unified
        # scale contract. Recover and align it before any chapter is written.
        plan = normalize_execution_plan(plan)
        chapters = plan.get("chapter_plans") or []
        chapters = order_chapters(chapters)
        report_target = int((plan.get("budget") or {}).get("target_words") or 0)
        report_minimum = int((plan.get("budget") or {}).get("min_words") or 0)
        minimum_ratio = (
            min(1.0, report_minimum / report_target)
            if report_target and report_minimum else settings.writer_min_budget_completion_ratio
        )
        valid_fact_ids = {f["id"] for f in facts}
        valid_inf_ids = {i["id"] for i in inferences}
        inf_levels = {i["id"]: i["source_level"] for i in inferences}

        # Report Memory:跨章一致性(核心观点/统一术语/已用事实/已用推断/前章摘要/禁止重复)
        # 分级(SurveyGen-I 收敛):术语保持定义一致;观点避免原样重复;
        # Fact 可复用但须承担新逻辑作用;判断可引用发展不重新论证;未解决问题后续优先回应
        report_memory = {
            "core_judgment": plan.get("core_judgment", ""),
            "narrative_logic": plan.get("narrative_logic", ""),
            "unified_terms": [],           # 已定义术语: {term, definition}
            "expressed_points": [],        # 已表达观点(避免原样重复)
            "formed_judgments": [],        # 已形成判断(引用/发展,不重新论证)
            "unresolved_issues": [],       # 未解决问题(后续章节优先回应)
            "fact_roles": {},              # fact_id -> 已承担的逻辑角色(discourse role)
            "used_fact_ids": set(),
            "used_inference_ids": set(),
            "chapter_summaries": [],       # {chapter, summary, core_message}
        }
        # 持久化记忆优先:断点恢复时保留术语、判断、未解决问题和 Fact 角色。
        if task_id:
            stored = latest_task_artifact(task_id, "report_memory", run_id=run_id)
            saved = (stored or {}).get("payload", {}).get("memory", {}) if stored else {}
            for key in ("unified_terms", "expressed_points", "formed_judgments", "unresolved_issues", "chapter_summaries"):
                report_memory[key] = list(saved.get(key) or [])
            report_memory["fact_roles"] = dict(saved.get("fact_roles") or {})
            report_memory["used_fact_ids"] = {int(x) for x in saved.get("used_fact_ids", [])}
            report_memory["used_inference_ids"] = {int(x) for x in saved.get("used_inference_ids", [])}

        report = self._open_or_create_report(plan, profile_id, existing_report_id)
        if report_callback:
            report_callback(report)

        # The FinalPlan still owns evidence allocation for every document. For
        # editorial/continuous forms, however, its chapters are evidence arcs,
        # not independent prose calls. Do not hide headings after drafting four
        # self-contained mini-reports: compose one article from a document plan.
        composition_mode = normalize_composition_mode(
            plan.get("composition_mode"), shape=plan.get("document_shape"),
        )
        if composition_mode == "article_beats":
            return self._write_article(
                report=report,
                plan=plan,
                chapters=chapters,
                facts=facts,
                inferences=inferences,
                style_block=style_block,
                style_variant=style_variant,
                valid_fact_ids=valid_fact_ids,
                valid_inf_ids=valid_inf_ids,
                inf_levels=inf_levels,
                institution_rules=institution_rules,
                task_profile=task_profile,
                report_policy=report_policy,
                task_id=task_id,
                run_id=run_id,
                progress_callback=progress_callback,
                chapter_callback=chapter_callback,
                minimum_ratio=minimum_ratio,
            )

        target_titles = {str(title).strip() for title in (target_chapter_titles or []) if str(title).strip()}
        existing_titles = {str(chapter.get("title", "")).strip() for chapter in chapters}
        preserved_titles = existing_titles - target_titles if target_titles else set()
        completed_sections, position = self._resume_report_memory(
            report.id,
            report_memory,
            chapters,
            excluded_sections=target_titles,
            preserved_sections=preserved_titles,
        )
        if target_titles:
            # 旧 revision 的 chapter_draft 只是历史完成标记。当前增量明确命中的
            # 章节必须重新生成,不能被同 task_id 下的旧 artifact 跳过。
            completed_sections.difference_update(target_titles)
            for title in existing_titles - target_titles:
                completed_sections.add(title)
        chapter_count = len(chapters) or 1
        failed_chapters: list[str] = []
        for chapter_index, chapter_plan in enumerate(chapters, start=1):
            chapter_title = str(chapter_plan.get("title", "") or f"章节{chapter_index}")
            chapter_start = time.time()
            if chapter_title in completed_sections:
                if progress_callback:
                    progress_callback(chapter_index, chapter_count, chapter_title, "resumed", 0)
                continue
            if progress_callback:
                progress_callback(chapter_index - 1, chapter_count, chapter_title, "generating", 0)
            # 1. 信息需求 → 检索 query(标题+核心问题+核心判断+所需事实,而非仅标题)
            query = " ".join(filter(None, [
                chapter_title,
                " ".join(chapter_plan.get("questions") or []),
                chapter_plan.get("judgment", ""),
                " ".join(chapter_plan.get("required_facts") or []),
            ]))
            support_fact_ids = set(int(x) for x in (chapter_plan.get("primary_fact_ids") or []))
            if cm is not None:
                chapter_facts, chapter_inferences, chapter_style = cm.for_writer_section(
                    query, facts, inferences, style_block,
                    required_fact_ids=support_fact_ids,
                    used_fact_ids=report_memory["used_fact_ids"],
                    coverage_queries=[
                        str(item.get("purpose") or item.get("title") or "")
                        for item in (chapter_plan.get("subsections") or [])
                        if isinstance(item, dict)
                    ] + [str(item) for item in (chapter_plan.get("questions") or [])],
                )
            else:
                chapter_facts, chapter_inferences, chapter_style = facts, inferences, style_block
            narrative_plan = narrative_agent.plan_chapter(
                task_id=task_id,
                chapter_plan=chapter_plan,
                report_plan=plan,
                facts=chapter_facts,
                inferences=chapter_inferences,
                report_memory=report_memory,
                run_id=run_id,
            )
            previous_chapter_text = "".join(self._chapter_texts(report.id, chapter_title))
            # 2. 执行 ChapterPlan 生成(不再重复规划)。生成阶段不持有 SQLite 写事务。
            chapter_sentences = self._generate_chapter(
                chapter_title, chapter_index, chapter_count,
                [c.get("title", "") for c in chapters],
                chapter_facts, chapter_inferences, chapter_style,
                plan, report_memory, valid_fact_ids, valid_inf_ids,
                chapter_plan=chapter_plan,
                institution_rules=institution_rules,
                narrative_plan=narrative_plan,
                business_block=_business_block(task_profile, chapter_plan),
                policy_block=policy_prompt_block(_writer_policy_only(report_policy or {})),
                minimum_ratio=minimum_ratio,
                style_variant=style_variant,
            )
            if not chapter_sentences:
                # A failed generation is not a reviewable version. Keep the
                # stored chapter intact rather than replacing it with emptiness.
                if progress_callback:
                    progress_callback(chapter_index - 1, chapter_count, chapter_title, "failed", round(time.time() - chapter_start, 1))
                failed_chapters.append(chapter_title)
                continue
            chapter_assessment = assess_chapter_output(
                target_words=int(chapter_plan.get("target_words") or 0),
                generated_text="".join(str(item.get("text") or "") for item in chapter_sentences),
                previous_text=previous_chapter_text,
                minimum_completion_ratio=minimum_ratio,
                evidence_limited=(
                    bool(narrative_plan.get("evidence_limited"))
                    or not (chapter_facts or chapter_inferences)
                    or str((plan.get("budget") or {}).get("evidence_status") or "").lower()
                    in {"limited", "insufficient"}
                ),
            )
            chapter_assessment.update(getattr(self, "_last_chapter_generation_stats", {}) or {})
            if style_variant is not None and task_id:
                try:
                    from app.memory.style_profile import assess_style_alignment

                    style_diagnostic = assess_style_alignment(
                        "".join(str(item.get("text") or "") for item in chapter_sentences),
                        style_variant.writing_patterns,
                        style_variant.terminology,
                    )
                    save_task_artifact(
                        task_id,
                        f"style_diagnostic:{chapter_title}",
                        {
                            "profile_id": style_variant.id,
                            "profile_version": style_variant.profile_version,
                            "chapter_title": chapter_title,
                        },
                        style_diagnostic,
                        run_id=run_id,
                    )
                    chapter_assessment["style_alignment"] = style_diagnostic
                except Exception:
                    # Style diagnostics are advisory and must never block a
                    # traceable draft from reaching review.
                    pass
            paragraph_number = 0
            written_fact_ids: set[int] = set()
            written_inference_ids: set[int] = set()
            chapter_text: list[str] = []
            chapter_call_id = self.last_call_id
            call_products: dict[str, dict[str, object]] = {}
            sentence_ids: list[int] = []
            with session_scope() as s:
                lineage_assignments = self._match_candidate_lineages(
                    s, report.id, chapter_title, chapter_sentences,
                )
                # Generation happens before this transaction. Delete
                # and insert are committed atomically, so readers see either the
                # old chapter or the complete new chapter, never a half chapter.
                self._delete_chapter_in_session(s, report.id, chapter_title)
                for candidate_index, sent in enumerate(chapter_sentences):
                    if sent["paragraph"] != paragraph_number:
                        paragraph_number = sent["paragraph"]
                    position = _chapter_position(chapter_index, len(sentence_ids) + 1)
                    fact_ids = sent["fact_ids"]
                    inf_ids = sent["inference_ids"]
                    if fact_ids:
                        level = "MATERIAL_FACT"
                    elif inf_ids:
                        level = inf_levels.get(inf_ids[0], "MATERIAL_INFERENCE")
                    elif sent.get("source_level") == "SUBHEADING":
                        level = "SUBHEADING"
                    else:
                        level = "TRANSITION"  # 过渡句:无引用,不参与来源分级
                    source_refs = {
                        "fact_ids": fact_ids,
                        "inference_ids": inf_ids,
                    }
                    if sent.get("trace_granularity_warning"):
                        source_refs["trace_granularity_warning"] = True
                    origin_call_id = str(sent.get("_origin_call_id") or chapter_call_id or "")
                    sent_cur = s.execute(
                        ORMSentence.insert().values(
                            report_id=report.id, section=chapter_title,
                            lineage_id=lineage_assignments[candidate_index][0],
                            parent_sentence_id=lineage_assignments[candidate_index][1],
                            paragraph=paragraph_number, position=position,
                            content=sent["text"], source_level=level,
                            source_refs=json.dumps(source_refs, ensure_ascii=False),
                            origin_call_id=origin_call_id or None,
                        )
                    )
                    sentence_id = int(sent_cur.inserted_primary_key[0])
                    sentence_ids.append(sentence_id)
                    for fid in dict.fromkeys(fact_ids):
                        existing = s.execute(
                            select(ORMSentenceFact.c.sentence_id).where(
                                ORMSentenceFact.c.sentence_id == sentence_id,
                                ORMSentenceFact.c.fact_id == fid,
                            )
                        ).first()
                        if existing is None:
                            s.execute(ORMSentenceFact.insert().values(sentence_id=sentence_id, fact_id=fid))
                    for iid in dict.fromkeys(inf_ids):
                        existing = s.execute(
                            select(ORMSentenceInference.c.sentence_id).where(
                                ORMSentenceInference.c.sentence_id == sentence_id,
                                ORMSentenceInference.c.inference_id == iid,
                            )
                        ).first()
                        if existing is None:
                            s.execute(ORMSentenceInference.insert().values(sentence_id=sentence_id, inference_id=iid))
                    written_fact_ids.update(fact_ids)
                    written_inference_ids.update(inf_ids)
                    chapter_text.append(sent["text"])
                    if origin_call_id:
                        product = call_products.setdefault(origin_call_id, {
                            "fact_ids": set(), "inference_ids": set(), "chars": 0,
                        })
                        product["fact_ids"].update(fact_ids)
                        product["inference_ids"].update(inf_ids)
                        product["chars"] += len(sent["text"] or "")
            for call_id, product in call_products.items():
                update_call_products(
                    call_id,
                    produced_chapter_ids=[chapter_index],
                    final_used_fact_ids=sorted(product["fact_ids"]),
                    final_used_inference_ids=sorted(product["inference_ids"]),
                )
                update_call_metrics(
                    call_id,
                    stored_chars=int(product["chars"]),
                    final_chars=int(product["chars"]),
                )
            # 3. Narrative QA:按 Topic 完成条件做语义判断(字数只作观察,不触发补写)
            qa_result = narrative_agent.qa_chapter(
                chapter_title,
                self._paragraph_texts(report.id, chapter_title),
                narrative_plan,
                chapter_facts,
            )
            for item in qa_result.get("topics") or []:
                # QA may replace a clearly defective existing paragraph, but
                # never starts an expansion/continuation pass to chase length.
                if item["action"] == "rewrite" and item["evidence_sufficient"] and item["target_paragraph"] > 0:
                    rewritten = self._rewrite_paragraph(
                        report.id, chapter_title, chapter_index, item,
                        narrative_plan, chapter_facts, chapter_inferences,
                        valid_fact_ids, valid_inf_ids, inf_levels, plan, report_memory,
                    )
                    if rewritten:
                        written_fact_ids.update(rewritten["fact_ids"])
                        written_inference_ids.update(rewritten["inference_ids"])
                        # 段落已替换,重读本段文本用于 Report Memory
                        chapter_text = self._chapter_texts(report.id, chapter_title)
                        update_call_products(
                            self.last_call_id,
                            produced_chapter_ids=[chapter_index],
                            final_used_fact_ids=sorted(written_fact_ids),
                            final_used_inference_ids=sorted(written_inference_ids),
                        )

                chapter_text = self._chapter_texts(report.id, chapter_title)
                update_call_products(
                    self.last_call_id,
                    produced_chapter_ids=[chapter_index],
                    final_used_fact_ids=sorted(written_fact_ids),
                    final_used_inference_ids=sorted(written_inference_ids),
                )
            self._record_narrative_qa(chapter_title, qa_result)
            # 4. 更新 Report Memory(分级记忆:术语/观点/判断/未解决/Fact 角色/章节摘要)
            report_memory["used_fact_ids"].update(written_fact_ids)
            report_memory["used_inference_ids"].update(written_inference_ids)
            _mem = _extract_chapter_memory(chapter_title, narrative_plan, chapter_text, qa_result)
            report_memory["unified_terms"].extend(_mem["terms"])
            report_memory["expressed_points"].extend(_mem["points"])
            report_memory["formed_judgments"].extend(_mem["judgments"])
            report_memory["unresolved_issues"].extend(_mem["unresolved"])
            for fid, role in (_mem["fact_roles"] or {}).items():
                report_memory["fact_roles"][fid] = role
            report_memory["chapter_summaries"].append({
                "chapter": chapter_title,
                "summary": "".join(chapter_text)[:200],
                "core_message": narrative_plan.get("core_message") or narrative_plan.get("central_message", ""),
            })
            try:
                save_task_artifact(task_id, "report_memory", {"report_id": report.id}, {"memory": serializable_memory(report_memory)}, run_id=run_id)
            except Exception:
                pass
            if progress_callback:
                progress_callback(chapter_index, chapter_count, chapter_title, "done", round(time.time() - chapter_start, 1))
            if chapter_callback:
                chapter_callback({
                    "report_id": report.id,
                    "chapter": chapter_title,
                    "chapter_index": chapter_index,
                    "chapter_count": chapter_count,
                    "sentence_count": len(chapter_sentences),
                    "fact_ids": sorted(written_fact_ids),
                    "inference_ids": sorted(written_inference_ids),
                    "narrative_plan": narrative_plan,
                    "duration_seconds": round(time.time() - chapter_start, 1),
                    "status": "done",
                    **chapter_assessment,
                })
        if failed_chapters:
            raise RuntimeError(
                "WRITER_INCOMPLETE_CHAPTERS:" + " | ".join(failed_chapters)
            )

        # EvidenceGap 接口(WriteHERE 预留):未解决问题汇总落 artifact,供 Evidence 回流/后续升级
        gaps = report_memory.get("unresolved_issues") or []
        if gaps:
            try:
                save_task_artifact(task_id, "evidence_gaps", {"source": "writer"}, {"gaps": gaps}, run_id=run_id)
            except Exception:
                pass
        return report

    def _write_article(self, *, report: Report, plan: dict, chapters: list[dict],
                       facts: list[dict], inferences: list[dict], style_block: str,
                       style_variant, valid_fact_ids: set[int], valid_inf_ids: set[int],
                       inf_levels: dict[int, str], institution_rules: dict | None,
                       task_profile: dict | None, report_policy: dict | None,
                       task_id: str, run_id: str, progress_callback, chapter_callback,
                       minimum_ratio: float) -> Report:
        """Write a continuous document from one document-level narrative plan.

        ``chapters`` remain the FinalPlan's auditable evidence arcs. They are
        deliberately not treated as model-call boundaries in this branch.
        """
        article_title = "__article__"
        if progress_callback:
            progress_callback(0, 1, "正文", "generating", 0)
        planned_fact_ids, planned_inference_ids = _article_evidence_ids(chapters)
        article_facts = [item for item in facts if int(item.get("id") or 0) in planned_fact_ids]
        article_inferences = [item for item in inferences if int(item.get("id") or 0) in planned_inference_ids]
        # Old plans can lack explicit assignments. Keep their safe, task-local
        # fallback instead of silently producing an empty article.
        article_facts = article_facts or list(facts)
        if not article_inferences:
            supplied_fact_ids = {int(item.get("id") or 0) for item in article_facts}
            article_inferences = [
                item for item in inferences
                if supplied_fact_ids & set(_int_ids(item.get("based_fact_ids")))
            ] or list(inferences)
        article_plan = narrative_agent.plan_document(
            task_id=task_id,
            report_plan=plan,
            facts=article_facts,
            inferences=article_inferences,
            run_id=run_id,
        )
        paragraph_plan = list(article_plan.get("paragraphs") or [])
        evidence_units = paragraph_plan or list(article_plan.get("beats") or [])
        allowed_fact_ids = {
            fact_id for unit in evidence_units
            for fact_id in _int_ids(unit.get("fact_ids"))
        } or {int(item.get("id") or 0) for item in article_facts}
        allowed_inference_ids = {
            inference_id for unit in evidence_units
            for inference_id in _int_ids(unit.get("inference_ids"))
        } or {int(item.get("id") or 0) for item in article_inferences}
        article_facts = [item for item in article_facts if int(item.get("id") or 0) in allowed_fact_ids]
        article_inferences = [item for item in article_inferences if int(item.get("id") or 0) in allowed_inference_ids]
        article_style = style_block
        if style_variant is not None:
            article_style = style_variant.writer_prompt_block({
                "title": str(plan.get("title") or ""),
                "purpose": str(article_plan.get("central_thread") or plan.get("core_judgment") or ""),
                "report_type": str(plan.get("report_type") or ""),
                "keywords": [str(beat.get("purpose") or "") for beat in article_plan.get("beats") or []],
                "target_words": int((plan.get("budget") or {}).get("target_words") or 0),
                "sample_type": "opening",
            })
        safe_words = max(
            500,
            int(settings.writer_output_tokens * settings.writer_visible_word_token_ratio * 0.82),
        )
        groups = _article_beat_groups(evidence_units, safe_words)
        generated: list[dict] = []
        previous_tail = ""
        paragraph_offset = 0
        for group_index, group in enumerate(groups, start=1):
            group_fact_ids = {value for unit in group for value in _int_ids(unit.get("fact_ids"))}
            group_inference_ids = {value for unit in group for value in _int_ids(unit.get("inference_ids"))}
            group_beat_ids = {beat_id for unit in group for beat_id in unit.get("beat_ids") or []}
            group_beats = [
                beat for beat in article_plan.get("beats") or []
                if not group_beat_ids or str(beat.get("beat_id")) in group_beat_ids
            ]
            group_plan = {
                **article_plan,
                "beats": group_beats,
                "paragraphs": group if paragraph_plan else [],
                "target_words": sum(int(unit.get("target_words") or 0) for unit in group),
                "previous_tail": previous_tail,
                "group_index": group_index,
                "group_count": len(groups),
            }
            group_result = self._generate_article_pass(
                plan=plan,
                article_plan=group_plan,
                facts=[item for item in article_facts if int(item.get("id") or 0) in group_fact_ids],
                inferences=[item for item in article_inferences if int(item.get("id") or 0) in group_inference_ids],
                style_block=article_style,
                allowed_fact_ids=group_fact_ids & valid_fact_ids,
                allowed_inference_ids=group_inference_ids & valid_inf_ids,
                institution_rules=institution_rules,
                task_profile=task_profile,
                policy_block=policy_prompt_block(_writer_policy_only(report_policy or {})),
                minimum_ratio=minimum_ratio,
            )
            if not group_result:
                generated = []
                break
            for item in group_result:
                item["paragraph"] = int(item.get("paragraph") or 1) + paragraph_offset
            paragraph_offset = max(int(item.get("paragraph") or 1) for item in generated + group_result)
            generated.extend(group_result)
            previous_tail = "".join(item.get("text") or "" for item in group_result)[-320:]
        if not generated:
            raise RuntimeError("WRITER_INCOMPLETE_ARTICLE")

        written_fact_ids = {fact_id for item in generated for fact_id in _int_ids(item.get("fact_ids"))}
        written_inference_ids = {
            inference_id for item in generated for inference_id in _int_ids(item.get("inference_ids"))
        }
        call_products: dict[str, dict[str, object]] = {}
        with session_scope() as s:
            # A coherent article replaces the prior document atomically. Keeping
            # old chapter rows would make a hidden, stale outline leak into UI
            # and DOCX export.
            rows = s.execute(select(ORMSentence.c.id).where(ORMSentence.c.report_id == report.id)).mappings().all()
            old_ids = [int(row["id"]) for row in rows]
            if old_ids:
                s.execute(delete(ORMSentenceFact).where(ORMSentenceFact.c.sentence_id.in_(old_ids)))
                s.execute(delete(ORMSentenceInference).where(ORMSentenceInference.c.sentence_id.in_(old_ids)))
                s.execute(delete(ORMSentence).where(ORMSentence.c.id.in_(old_ids)))
            for index, item in enumerate(generated, start=1):
                fact_ids = _int_ids(item.get("fact_ids"))
                inference_ids = _int_ids(item.get("inference_ids"))
                level = "MATERIAL_FACT" if fact_ids else (
                    inf_levels.get(inference_ids[0], "MATERIAL_INFERENCE") if inference_ids else "TRANSITION"
                )
                origin_call_id = str(item.get("_origin_call_id") or self.last_call_id or "")
                cursor = s.execute(ORMSentence.insert().values(
                    report_id=report.id, section=article_title,
                    lineage_id=uuid.uuid4().hex, paragraph=int(item.get("paragraph") or 1),
                    position=_chapter_position(1, index), content=item["text"], source_level=level,
                    source_refs=json.dumps({"fact_ids": fact_ids, "inference_ids": inference_ids}, ensure_ascii=False),
                    origin_call_id=origin_call_id or None,
                ))
                sentence_id = int(cursor.inserted_primary_key[0])
                for fact_id in dict.fromkeys(fact_ids):
                    s.execute(ORMSentenceFact.insert().values(sentence_id=sentence_id, fact_id=fact_id))
                for inference_id in dict.fromkeys(inference_ids):
                    s.execute(ORMSentenceInference.insert().values(sentence_id=sentence_id, inference_id=inference_id))
                if origin_call_id:
                    product = call_products.setdefault(origin_call_id, {"fact_ids": set(), "inference_ids": set(), "chars": 0})
                    product["fact_ids"].update(fact_ids)
                    product["inference_ids"].update(inference_ids)
                    product["chars"] += len(item.get("text") or "")
        for call_id, product in call_products.items():
            update_call_products(
                call_id, produced_chapter_ids=[1],
                final_used_fact_ids=sorted(product["fact_ids"]),
                final_used_inference_ids=sorted(product["inference_ids"]),
            )
            update_call_metrics(call_id, stored_chars=int(product["chars"]), final_chars=int(product["chars"]))
        article_text = "".join(item.get("text") or "" for item in generated)
        assessment = assess_chapter_output(
            target_words=int((plan.get("budget") or {}).get("target_words") or 0),
            generated_text=article_text, previous_text="",
            minimum_completion_ratio=minimum_ratio,
            evidence_limited=not bool(article_facts or article_inferences),
        )
        assessment.update({
            "generation_mode": "article_beats",
            "generation_units": len(groups),
            "generation_calls": len(groups),
            "planned_fact_count": len(allowed_fact_ids),
            "planned_inference_count": len(allowed_inference_ids),
        })
        try:
            save_task_artifact(task_id, "article_draft", {"report_id": report.id}, {
                "status": "done", "section": article_title, "article_plan": article_plan,
                "fact_ids": sorted(written_fact_ids), "inference_ids": sorted(written_inference_ids),
                "assessment": assessment,
            }, run_id=run_id)
        except Exception:
            pass
        if progress_callback:
            progress_callback(1, 1, "正文", "done", 0)
        if chapter_callback:
            chapter_callback({
                "report_id": report.id, "chapter": "正文", "chapter_index": 1, "chapter_count": 1,
                "sentence_count": len(generated), "fact_ids": sorted(written_fact_ids),
                "inference_ids": sorted(written_inference_ids), "narrative_plan": article_plan,
                "duration_seconds": 0, "status": "done", **assessment,
            })
        return report

    def _generate_article_pass(self, *, plan: dict, article_plan: dict,
                               facts: list[dict], inferences: list[dict], style_block: str,
                               allowed_fact_ids: set[int], allowed_inference_ids: set[int],
                               institution_rules: dict | None, task_profile: dict | None,
                               policy_block: str, minimum_ratio: float) -> list[dict]:
        """Generate a continuous article and enforce its local evidence contract."""
        target_words = int(
            article_plan.get("target_words") or (plan.get("budget") or {}).get("target_words") or 0
        )
        fact_lines = [f"{item['id']}. {item.get('content', '')}" for item in facts]
        inference_lines = [f"{item['id']}. ({item.get('source_level', '')}) {item.get('content', '')}" for item in inferences]
        contract = _json_dumps(article_plan, limit=1800)
        paragraph_contract = _json_dumps(article_plan.get("paragraphs") or [], limit=1200)
        final_arcs = _json_dumps([
            {"title": item.get("title"), "purpose": item.get("judgment") or item.get("core_question"), "facts": item.get("primary_fact_ids", []), "inferences": item.get("primary_inference_ids", [])}
            for item in plan.get("chapter_plans") or []
        ], limit=1000)
        rules = ""
        if institution_rules and institution_rules.get("must_include"):
            rules = "全文硬性要求:\n- " + "\n- ".join(str(item) for item in institution_rules["must_include"]) + "\n"
        group_index = int(article_plan.get("group_index") or 1)
        group_count = max(1, int(article_plan.get("group_count") or 1))
        if group_index < group_count:
            pass_contract = (
                f"这是连续文章的第 {group_index}/{group_count} 段写作，不是全文结尾。"
                "只完成本批 Narrative Plan 的论证，结尾自然引向下一层问题；"
                "不得使用‘综上’‘总之’‘结论’或给出全文性判断。"
            )
        else:
            pass_contract = (
                "这是连续文章的最后一段写作。承接上文完成剩余论证，"
                "只在确有必要时给出一次简洁收束，不重复前文已经得出的结论。"
            )

        def build_prompt(selected_facts: list[str], selected_inferences: list[str]) -> str:
            return (
                f"报告标题:{plan.get('title', '')}\n"
                f"文档形态:{(plan.get('document_shape') or {}).get('kind', '')}；本次必须写成一篇连续文章，不要章节标题、编号、Markdown 标题或分节标签。\n"
                f"全文目标:约 {target_words} 字；证据不足时允许欠填，禁止重复或虚构。\n"
                f"核心判断:{plan.get('core_judgment', '')}\n"
                f"全文叙事逻辑:{plan.get('narrative_logic', '')}\n"
                f"内部证据弧线(只用于核对覆盖，不得照抄为标题):{final_arcs}\n\n"
                f"文章级 Narrative Plan(必须执行):\n{contract}\n\n"
                + (
                    "段落执行计划(按顺序每项输出一个 paragraphs 元素；不得把不同段落计划合并成一个长段):\n"
                    f"{paragraph_contract}\n\n"
                    if article_plan.get("paragraphs") else ""
                )
                + f"连续写作位置:{pass_contract}\n\n"
                + (f"上一连续片段结尾(只用于自然承接，不得重复):{article_plan.get('previous_tail')}\n\n" if article_plan.get("previous_tail") else "")
                + (rules + "\n" if rules else "")
                + (f"业务边界:\n{_business_block(task_profile, {})}\n\n" if task_profile else "")
                + (f"{policy_block}\n\n" if policy_block else "")
                + "允许引用的事实:\n" + "\n".join(selected_facts) + "\n\n"
                + "允许引用的推断:\n" + "\n".join(selected_inferences) + "\n\n"
                + f"{style_block}\n"
                + "请先按段落执行计划安排开篇、展开、转折与收束，再输出连贯正文。自然段由论证关系决定，不要逐条复述事实；每句事实性内容必须绑定下列允许 ID。"
                  "严格输出 JSON 对象：{\"paragraphs\":[{\"sentences\":[{\"text\":\"...\",\"fact_ids\":[1],\"inference_ids\":[]}]}]}。"
            )

        empty_prompt = build_prompt([], [])
        available = max(0, _writer_prompt_token_budget() - count_tokens(empty_prompt))
        fact_lines, inference_lines = _pack_writer_evidence(fact_lines, inference_lines, available)
        prompt = build_prompt(fact_lines, inference_lines)
        publish_context_audit({
            "stage": "writer", "tokenizer_method": tokenizer_method(),
            "budget_tokens": _writer_prompt_token_budget(), "actual_tokens": count_tokens(prompt),
            "composition_mode": "article_beats",
            "sections": {"事实": {"candidate_items": len(facts), "selected_items": len(fact_lines)}, "推断": {"candidate_items": len(inferences), "selected_items": len(inference_lines)}},
        })
        try:
            payload = self.generate_json(prompt)
        except Exception:
            return []
        call_id = self.last_call_id
        result: list[dict] = []
        transition_budget = 4 if facts or inferences else 0
        model_sentences = accepted_sentences = dropped_untraced_sentences = 0
        seen: set[str] = set()
        for paragraph_number, paragraph in enumerate(_coerce_paragraphs(payload), start=1):
            for sentence in paragraph.get("sentences") or []:
                if not isinstance(sentence, dict):
                    dropped_untraced_sentences += 1
                    continue
                model_sentences += 1
                text = str(sentence.get("text") or "").strip()
                fact_ids = [item for item in _int_ids(sentence.get("fact_ids")) if item in allowed_fact_ids]
                inference_ids = [item for item in _int_ids(sentence.get("inference_ids")) if item in allowed_inference_ids]
                key = re.sub(r"\s+", "", text)
                if not text or key in seen:
                    continue
                if not fact_ids and not inference_ids:
                    if transition_budget <= 0 or len(text) > 40:
                        dropped_untraced_sentences += 1
                        continue
                    transition_budget -= 1
                seen.add(key)
                accepted_sentences += 1
                result.append({
                    "text": text, "fact_ids": fact_ids, "inference_ids": inference_ids,
                    "paragraph": paragraph_number, "source_level": "", "_origin_call_id": call_id,
                })
        update_call_funnel(
            call_id, model_sentences=model_sentences, accepted_sentences=accepted_sentences,
            dropped_untraced_sentences=dropped_untraced_sentences, split_items=0,
        )
        return result

    def rewrite_user_scope(self, *, report_id: int, scope: dict, instruction: str,
                           facts: list[dict], inferences: list[dict]) -> dict:
        """Rewrite one persisted sentence or paragraph with explicit evidence bindings."""
        arguments = dict(scope.get("arguments") or {})
        sentence_id = int(arguments.get("sentence_id") or 0)
        chapter_title = str(arguments.get("chapter_title") or "")
        paragraph = int(arguments.get("paragraph") or 0)
        with session_scope() as s:
            if sentence_id:
                target = s.execute(select(ORMSentence).where(
                    ORMSentence.c.id == sentence_id, ORMSentence.c.report_id == report_id,
                )).mappings().first()
                if target is None:
                    raise ValueError("REVISION_SENTENCE_NOT_FOUND")
                chapter_title = str(target["section"])
                paragraph = int(target["paragraph"] or 1)
            rows = s.execute(select(ORMSentence).where(
                ORMSentence.c.report_id == report_id,
                ORMSentence.c.section == chapter_title,
                ORMSentence.c.paragraph == paragraph,
                ORMSentence.c.selected == 1,
            ).order_by(ORMSentence.c.position, ORMSentence.c.id)).mappings().all()
            neighbours = s.execute(select(
                ORMSentence.c.paragraph, ORMSentence.c.content, ORMSentence.c.user_edit,
            ).where(
                ORMSentence.c.report_id == report_id,
                ORMSentence.c.section == chapter_title,
                ORMSentence.c.paragraph.in_([max(1, paragraph - 1), paragraph + 1]),
                ORMSentence.c.selected == 1,
            ).order_by(ORMSentence.c.position)).mappings().all()
        editable_rows = [row for row in rows if str(row["source_level"] or "") != "SUBHEADING"]
        if not editable_rows:
            raise ValueError("REVISION_PARAGRAPH_NOT_FOUND")
        target_rows = [row for row in editable_rows if int(row["id"]) == sentence_id] if sentence_id else editable_rows
        if not target_rows:
            raise ValueError("REVISION_TARGET_NOT_FOUND")

        existing_fact_ids: set[int] = set()
        existing_inference_ids: set[int] = set()
        for row in editable_rows:
            try:
                refs = json.loads(row["source_refs"] or "{}")
            except (TypeError, ValueError):
                refs = {}
            existing_fact_ids.update(_int_ids(refs.get("fact_ids")))
            existing_inference_ids.update(_int_ids(refs.get("inference_ids")))
        requested_fact_ids = set(_int_ids(arguments.get("reference_fact_ids")))
        requested_inference_ids = set(_int_ids(arguments.get("reference_inference_ids")))
        allowed_fact_ids = existing_fact_ids | requested_fact_ids
        allowed_inference_ids = existing_inference_ids | requested_inference_ids
        support_facts = [item for item in facts if int(item.get("id") or 0) in allowed_fact_ids]
        support_inferences = [item for item in inferences if int(item.get("id") or 0) in allowed_inference_ids]
        valid_fact_ids = {int(item.get("id") or 0) for item in support_facts}
        valid_inference_ids = {int(item.get("id") or 0) for item in support_inferences}

        original = "".join(str(row["user_edit"] or row["content"] or "") for row in target_rows)
        paragraph_text = "".join(str(row["user_edit"] or row["content"] or "") for row in editable_rows)
        neighbour_text = {}
        for row in neighbours:
            neighbour_text.setdefault(int(row["paragraph"]), []).append(str(row["user_edit"] or row["content"] or ""))
        context_lines = [
            f"上一段：{''.join(neighbour_text.get(paragraph - 1, [])) or '无'}",
            f"当前段：{paragraph_text}",
            f"下一段：{''.join(neighbour_text.get(paragraph + 1, [])) or '无'}",
        ]
        scope_label = "指定句子" if sentence_id else "指定段落"
        output_rule = (
            "只输出一个 sentences 元素" if sentence_id
            else "按自然表达输出该段所需的多个 sentences 元素"
        )
        prompt, _audit = build_prompt_from_sections(
            "writer",
            [
                ContextSection("修改任务", [
                    f"章节：{chapter_title}；目标：{scope_label}",
                    f"用户要求：{instruction}",
                    f"原内容：{original}",
                    "只修改指定范围，保持与前后文衔接。具体事实优先，不得引入未提供的信息。"
                    "每个事实性句子必须绑定实际支持它的 fact_ids/inference_ids；不得沿用不再支持新表达的旧引用。"
                    f"严格输出 JSON：{{\"paragraphs\":[{{\"sentences\":[{{\"text\":\"...\",\"fact_ids\":[],\"inference_ids\":[]}}]}}]}}；{output_rule}。",
                ], weight=6, required_items=4),
                ContextSection("前后文", context_lines, weight=2),
                ContextSection("可用事实", [
                    f"fact_id={item.get('id')}：{item.get('content', '')}" for item in support_facts
                ], weight=5),
                ContextSection("可用推论", [
                    f"inference_id={item.get('id')}；依据事实={item.get('based_fact_ids') or []}：{item.get('content', '')}"
                    for item in support_inferences
                ], weight=4),
            ],
            min(6000, settings.model_context_window_tokens // 3),
        )
        payload = self.generate_json(prompt, max_tokens=min(3072, settings.writer_output_tokens))
        generated = []
        for item in _coerce_paragraphs(payload):
            for sentence in item.get("sentences") or []:
                if not isinstance(sentence, dict) or not str(sentence.get("text") or "").strip():
                    continue
                fact_ids = [value for value in _int_ids(sentence.get("fact_ids")) if value in valid_fact_ids]
                inference_ids = [value for value in _int_ids(sentence.get("inference_ids")) if value in valid_inference_ids]
                generated.append({
                    "text": str(sentence.get("text") or "").strip(),
                    "fact_ids": fact_ids,
                    "inference_ids": inference_ids,
                })
        if sentence_id and len(generated) != 1:
            raise ValueError("REVISION_SENTENCE_OUTPUT_INVALID")
        if not generated:
            raise ValueError("REVISION_OUTPUT_EMPTY")
        if (existing_fact_ids or existing_inference_ids) and not any(
            item["fact_ids"] or item["inference_ids"] for item in generated
        ):
            raise ValueError("REVISION_EVIDENCE_BINDING_MISSING")

        if sentence_id:
            self._replace_user_sentence(report_id, target_rows[0], generated[0])
        else:
            self._replace_user_paragraph(report_id, chapter_title, paragraph, editable_rows, generated)
        return {
            "scope": "sentence" if sentence_id else "paragraph",
            "section": chapter_title,
            "paragraph": paragraph,
            "sentence_count": len(generated),
            "fact_ids": sorted({value for item in generated for value in item["fact_ids"]}),
            "inference_ids": sorted({value for item in generated for value in item["inference_ids"]}),
        }

    def _replace_user_sentence(self, report_id: int, row: dict, generated: dict) -> None:
        sentence_id = int(row["id"])
        history = []
        try:
            history = json.loads(row["edit_history"] or "[]")
        except (TypeError, ValueError):
            pass
        history.append({"content": row["user_edit"] or row["content"], "source": "writer_scope_revision"})
        level = "MATERIAL_FACT" if generated["fact_ids"] else (
            "MATERIAL_INFERENCE" if generated["inference_ids"] else "TRANSITION"
        )
        with session_scope() as s:
            s.execute(delete(ORMSentenceFact).where(ORMSentenceFact.c.sentence_id == sentence_id))
            s.execute(delete(ORMSentenceInference).where(ORMSentenceInference.c.sentence_id == sentence_id))
            s.execute(update(ORMSentence).where(
                ORMSentence.c.id == sentence_id, ORMSentence.c.report_id == report_id,
            ).values(
                user_edit=generated["text"], source_level=level,
                source_refs=json.dumps({"fact_ids": generated["fact_ids"], "inference_ids": generated["inference_ids"]}, ensure_ascii=False),
                edit_history=json.dumps(history[-50:], ensure_ascii=False), origin_call_id=self.last_call_id,
            ))
            for fact_id in generated["fact_ids"]:
                s.execute(ORMSentenceFact.insert().values(sentence_id=sentence_id, fact_id=fact_id))
            for inference_id in generated["inference_ids"]:
                s.execute(ORMSentenceInference.insert().values(sentence_id=sentence_id, inference_id=inference_id))

    def _replace_user_paragraph(self, report_id: int, chapter_title: str, paragraph: int,
                                rows: list[dict], generated: list[dict]) -> None:
        sentence_ids = [int(row["id"]) for row in rows]
        positions = [int(row["position"]) for row in rows]
        with session_scope() as s:
            s.execute(delete(ORMSentenceFact).where(ORMSentenceFact.c.sentence_id.in_(sentence_ids)))
            s.execute(delete(ORMSentenceInference).where(ORMSentenceInference.c.sentence_id.in_(sentence_ids)))
            s.execute(delete(ORMSentence).where(ORMSentence.c.id.in_(sentence_ids)))
            for index, item in enumerate(generated):
                position = positions[index] if index < len(positions) else positions[-1] + index + 1
                level = "MATERIAL_FACT" if item["fact_ids"] else (
                    "MATERIAL_INFERENCE" if item["inference_ids"] else "TRANSITION"
                )
                cursor = s.execute(ORMSentence.insert().values(
                    report_id=report_id, section=chapter_title, paragraph=paragraph,
                    position=position, content=item["text"], source_level=level,
                    source_refs=json.dumps({"fact_ids": item["fact_ids"], "inference_ids": item["inference_ids"]}, ensure_ascii=False),
                    lineage_id=uuid.uuid4().hex, origin_call_id=self.last_call_id,
                ))
                new_id = int(cursor.inserted_primary_key[0])
                for fact_id in item["fact_ids"]:
                    s.execute(ORMSentenceFact.insert().values(sentence_id=new_id, fact_id=fact_id))
                for inference_id in item["inference_ids"]:
                    s.execute(ORMSentenceInference.insert().values(sentence_id=new_id, inference_id=inference_id))

    @staticmethod
    def _delete_chapter_in_session(s, report_id: int, chapter_title: str) -> None:
        rows = s.execute(
            select(ORMSentence.c.id).where(
                ORMSentence.c.report_id == report_id,
                ORMSentence.c.section == chapter_title,
            )
        ).mappings().all()
        sentence_ids = [int(r["id"]) for r in rows]
        if not sentence_ids:
            return
        s.execute(delete(ORMSentenceFact).where(ORMSentenceFact.c.sentence_id.in_(sentence_ids)))
        s.execute(delete(ORMSentenceInference).where(ORMSentenceInference.c.sentence_id.in_(sentence_ids)))
        s.execute(delete(ORMSentence).where(ORMSentence.c.id.in_(sentence_ids)))

    @staticmethod
    def _match_candidate_lineages(s, report_id: int, chapter_title: str,
                                  candidates: list[dict]) -> list[tuple[str, int | None]]:
        """Carry stable sentence identity across rewrites when text remains related."""
        old_rows = s.execute(
            select(
                ORMSentence.c.id, ORMSentence.c.lineage_id, ORMSentence.c.content,
                ORMSentence.c.user_edit, ORMSentence.c.paragraph,
            ).where(
                ORMSentence.c.report_id == report_id,
                ORMSentence.c.section == chapter_title,
            ).order_by(ORMSentence.c.position, ORMSentence.c.id)
        ).mappings().all()
        available = set(range(len(old_rows)))
        assignments: list[tuple[str, int | None]] = []
        for candidate in candidates:
            text = str(candidate.get("text") or "")
            paragraph = int(candidate.get("paragraph") or 1)
            best_index = None
            best_score = 0.0
            for index in available:
                old = old_rows[index]
                old_text = str(old["user_edit"] or old["content"] or "")
                score = SequenceMatcher(None, old_text, text).ratio()
                if int(old["paragraph"] or 1) == paragraph:
                    score += 0.08
                if score > best_score:
                    best_index, best_score = index, score
            if best_index is not None and best_score >= 0.58:
                old = old_rows[best_index]
                available.remove(best_index)
                assignments.append((str(old["lineage_id"] or uuid.uuid4().hex), int(old["id"])))
            else:
                assignments.append((uuid.uuid4().hex, None))
        return assignments

    def _open_or_create_report(self, plan: dict, profile_id: int | None,
                               existing_report_id: int | None = None) -> Report:
        if existing_report_id is not None:
            with session_scope() as s:
                row = s.execute(
                    select(ORMReport).where(ORMReport.c.id == int(existing_report_id))
                ).mappings().first()
                if row is not None and int(row["plan_id"]) != int(plan["id"]):
                    # A revision can create a new FinalPlan while retaining the
                    # same report workspace. Keep the report-to-plan pointer in
                    # sync so version snapshots explain the candidate correctly.
                    s.execute(update(ORMReport).where(
                        ORMReport.c.id == int(existing_report_id)
                    ).values(plan_id=int(plan["id"])))
            if row is not None:
                return Report(
                    id=row["id"],
                    plan_id=int(plan["id"]),
                    title=row["title"],
                    style_profile_id=row["style_profile_id"],
                    status=row["status"],
                )
        report = Report(
            plan_id=plan["id"],
            title=plan.get("title", ""),
            style_profile_id=profile_id,
        )
        with session_scope() as s:
            result = s.execute(
                ORMReport.insert().values(
                    plan_id=report.plan_id, title=report.title,
                    style_profile_id=report.style_profile_id, status="draft",
                )
            )
            report.id = int(result.inserted_primary_key[0])
        return report

    def _resume_report_memory(self, report_id: int, report_memory: dict, chapters: list[dict],
                              excluded_sections: set[str] | None = None,
                              preserved_sections: set[str] | None = None) -> tuple[set[str], int]:
        """Load existing chapter rows so a partial report can continue safely."""
        order = {
            str(chapter.get("title", "")): index
            for index, chapter in enumerate(chapters, start=1)
        }
        with session_scope() as s:
            rows = s.execute(
                select(ORMSentence.c.section, ORMSentence.c.position, ORMSentence.c.content, ORMSentence.c.source_refs)
                .where(ORMSentence.c.report_id == report_id)
                .order_by(ORMSentence.c.position)
            ).mappings().all()
            artifact_rows = s.execute(
                select(ORMTaskArtifact.c.payload).where(
                    ORMTaskArtifact.c.task_id == (getattr(self, "_task_id", "") or ""),
                    ORMTaskArtifact.c.run_id == (getattr(self, "_run_id", "") or ""),
                    ORMTaskArtifact.c.stage.like("chapter_draft:%"),
                    ORMTaskArtifact.c.status == "done",
                )
            ).mappings().all()
        excluded_sections = excluded_sections or set()
        preserved_sections = preserved_sections or set()
        completed_markers: set[str] = set()
        for artifact in artifact_rows:
            try:
                payload = json.loads(artifact["payload"] or "{}")
            except (TypeError, ValueError):
                continue
            if payload.get("status") == "done" and payload.get("chapter"):
                completed_markers.add(str(payload.get("chapter")))
        completed_sections: set[str] = set()
        section_text: dict[str, list[str]] = {}
        position = 0
        for row in rows:
            section = str(row["section"])
            if section in excluded_sections:
                continue
            if section not in completed_markers and section not in preserved_sections:
                # Rows from an older run are a baseline, not resume state. If
                # they were counted as memory here, their Facts would be marked
                # used before the replacement chapter was generated.
                continue
            completed_sections.add(section)
            section_text.setdefault(section, []).append(row["content"])
            position = max(position, int(row["position"]))
            try:
                refs = json.loads(row["source_refs"] or "{}")
            except (TypeError, ValueError):
                refs = {}
            report_memory["used_fact_ids"].update(_int_ids(refs.get("fact_ids")))
            report_memory["used_inference_ids"].update(_int_ids(refs.get("inference_ids")))
        for section, texts in section_text.items():
            report_memory["chapter_summaries"].append({
                "chapter": section,
                "summary": "".join(texts)[:200],
            })
        self._normalize_report_positions(report_id, order)
        return completed_sections, position

    @staticmethod
    def _normalize_report_positions(report_id: int, order: dict[str, int]) -> None:
        if not order:
            return
        with session_scope() as s:
            rows = s.execute(
                select(ORMSentence.c.id, ORMSentence.c.section)
                .where(ORMSentence.c.report_id == report_id)
                .order_by(ORMSentence.c.position, ORMSentence.c.id)
            ).mappings().all()
            counters: dict[str, int] = {}
            for row in rows:
                section = str(row["section"])
                chapter_index = order.get(section, 999)
                counters[section] = counters.get(section, 0) + 1
                s.execute(
                    update(ORMSentence)
                    .where(ORMSentence.c.id == row["id"])
                    .values(position=_chapter_position(chapter_index, counters[section]))
                )

    def _generate_chapter(self, chapter_title, chapter_index, chapter_count, structure,
                          chapter_facts, chapter_inferences, chapter_style,
                          plan, report_memory,
                          valid_fact_ids, valid_inf_ids, chapter_plan=None,
                          institution_rules: dict | None = None,
                          business_block: str = "",
                          narrative_plan: dict | None = None,
                          policy_block: str = "",
                          minimum_ratio: float | None = None,
                          style_variant=None) -> list[dict]:
        """Generate each Narrative subsection exactly once, without continuation."""
        from app.document_shape import normalize_document_shape, visible_subheadings

        chapter_plan = chapter_plan or {}
        narrative_plan = narrative_plan or {}
        document_shape = normalize_document_shape(plan.get("document_shape"))
        chapter_target = int(chapter_plan.get("target_words") or 0)
        effective_minimum_ratio = (
            settings.writer_min_budget_completion_ratio
            if minimum_ratio is None else minimum_ratio
        )
        units = _subsection_generation_units(
            narrative_plan, chapter_target, effective_minimum_ratio,
        )
        if document_shape.get("section_policy") == "hidden" and len(units) > 1:
            # Internal evidence groups must not become separately drafted
            # mini-articles once their headings are hidden from the reader.
            collapsed_fact_ids = [
                value for source in units for value in _int_ids((source.get("plan") or {}).get("fact_ids"))
            ]
            collapsed_inference_ids = [
                value for source in units for value in _int_ids((source.get("plan") or {}).get("inference_ids"))
            ]
            units = [{
                "index": 1,
                "count": 1,
                "title": "",
                "target_words": max(0, chapter_target),
                "minimum_words": round(max(0, chapter_target) * max(0.0, effective_minimum_ratio)),
                # Preserve the original assignment for evidence audit/statistics
                # even though visible hidden subsections are drafted together.
                "plan": {
                    "fact_ids": list(dict.fromkeys(collapsed_fact_ids)),
                    "inference_ids": list(dict.fromkeys(collapsed_inference_ids)),
                },
                "evidence_status": str(narrative_plan.get("evidence_status") or "unknown"),
                "evidence_reason": str(narrative_plan.get("evidence_reason") or ""),
                "missing_information": list(narrative_plan.get("missing_information") or []),
            }]
        all_results: list[dict] = []
        seen_texts: set[str] = set()
        paragraph_offset = 0
        generation_calls = 0
        unit_stats: list[dict] = []

        for unit in units:
            unit_chapter_plan = dict(chapter_plan)
            unit_chapter_plan["target_words"] = unit["target_words"]
            unit_narrative = dict(narrative_plan)
            if unit["plan"]:
                topic_ids = {str(value) for value in unit["plan"].get("topic_ids") or []}
                unit_narrative["subsections"] = [unit["plan"]]
                unit_narrative["topics"] = [
                    topic for topic in (narrative_plan.get("topics") or [])
                    if str(topic.get("topic_id") or "") in topic_ids
                ]
                unit_narrative["logic_order"] = [
                    str(topic.get("name") or "") for topic in unit_narrative["topics"]
                    if str(topic.get("name") or "").strip()
                ]
            unit_style = chapter_style
            if style_variant is not None:
                unit_plan = unit.get("plan") or {}
                purpose = str(
                    unit_plan.get("purpose")
                    or unit_plan.get("core_message")
                    or narrative_plan.get("core_message")
                    or chapter_plan.get("judgment")
                    or ""
                )
                keywords = [
                    str(value) for value in (
                        list(unit_plan.get("completion_criteria") or [])
                        + list(narrative_plan.get("logic_order") or [])
                    ) if str(value).strip()
                ]
                dynamic_style = style_variant.writer_prompt_block({
                    "title": str(unit.get("title") or chapter_title),
                    "purpose": purpose,
                    "report_type": str(plan.get("report_type") or ""),
                    "keywords": keywords,
                    "target_words": int(unit.get("target_words") or 0),
                    "sample_type": _style_sample_type(unit_plan, chapter_index, chapter_count),
                })
                # The context manager already carries the static profile. Use
                # one purpose-matched block here instead of injecting the same
                # template twice and over-constraining prose.
                unit_style = dynamic_style
            unit_facts, unit_inferences = _focused_unit_evidence(
                chapter_facts, chapter_inferences, unit.get("plan"), chapter_plan,
            )
            generated = self._generate_chapter_pass(
                chapter_title, chapter_index, chapter_count, structure,
                unit_facts, unit_inferences, unit_style,
                plan, report_memory, valid_fact_ids, valid_inf_ids,
                chapter_plan=unit_chapter_plan,
                institution_rules=institution_rules,
                business_block=business_block,
                narrative_plan=unit_narrative,
                policy_block=policy_block,
                generation_unit=unit,
            )
            generation_calls += 1
            actual_words = measure_text_words("".join(item.get("text", "") for item in generated))
            planned_fact_ids = set(_int_ids((unit.get("plan") or {}).get("fact_ids")))
            planned_inference_ids = set(_int_ids((unit.get("plan") or {}).get("inference_ids")))
            used_fact_ids = {fact_id for item in generated for fact_id in _int_ids(item.get("fact_ids"))}
            used_inference_ids = {
                inference_id for item in generated for inference_id in _int_ids(item.get("inference_ids"))
            }
            underfilled = bool(
                unit["minimum_words"] and actual_words < unit["minimum_words"]
            )
            unit_stats.append({
                "unit_index": unit["index"],
                "subsection_title": unit["title"],
                "target_words": unit["target_words"],
                "minimum_words": unit["minimum_words"],
                "actual_words": actual_words,
                "completion_rate": round(actual_words / unit["target_words"], 4) if unit["target_words"] else None,
                "evidence_status": unit["evidence_status"],
                "supplied_fact_count": len(unit_facts),
                "supplied_inference_count": len(unit_inferences),
                "planned_fact_count": len(planned_fact_ids),
                "planned_inference_count": len(planned_inference_ids),
                "used_fact_count": len(used_fact_ids),
                "used_inference_count": len(used_inference_ids),
                "planned_fact_coverage": (
                    round(len(planned_fact_ids & used_fact_ids) / len(planned_fact_ids), 4)
                    if planned_fact_ids else None
                ),
                "unused_planned_fact_ids": sorted(planned_fact_ids - used_fact_ids),
                "unused_planned_inference_ids": sorted(planned_inference_ids - used_inference_ids),
                "underfilled": underfilled,
                "underfill_reason": (
                    f"evidence_{unit['evidence_status']}"
                    if underfilled and unit["evidence_status"] in {"limited", "insufficient"}
                    else "generation_budget_not_fulfilled" if underfilled else ""
                ),
                "generation_calls": 1,
            })
            accepted_for_unit: list[dict] = []
            for item in generated:
                text_key = re.sub(r"\s+", "", str(item.get("text") or ""))
                if text_key and text_key in seen_texts:
                    continue
                if text_key:
                    seen_texts.add(text_key)
                item = dict(item)
                item["paragraph"] = int(item.get("paragraph") or 1) + paragraph_offset
                accepted_for_unit.append(item)
            # Narrative units are always useful for evidence organization, but
            # they only become visible text headings when the chosen document
            # form calls for them.
            if accepted_for_unit and unit["title"] and visible_subheadings(document_shape):
                first_paragraph = min(int(item.get("paragraph") or 1) for item in accepted_for_unit)
                all_results.append({
                    "text": unit["title"],
                    "fact_ids": [],
                    "inference_ids": [],
                    "paragraph": first_paragraph,
                    "source_level": "SUBHEADING",
                    "trace_granularity_warning": False,
                    "_origin_call_id": accepted_for_unit[0].get("_origin_call_id"),
                })
            all_results.extend(accepted_for_unit)
            if accepted_for_unit:
                paragraph_offset = max(int(item.get("paragraph") or 1) for item in all_results)
        self._last_chapter_generation_stats = {
            "generation_mode": "narrative_subsections",
            "generation_units": len(units),
            "generation_calls": generation_calls,
            "generation_unit_stats": unit_stats,
        }
        return all_results

    def _generate_chapter_pass(self, chapter_title, chapter_index, chapter_count, structure,
                          chapter_facts, chapter_inferences, chapter_style,
                          plan, report_memory,
                          valid_fact_ids, valid_inf_ids, chapter_plan=None,
                          institution_rules: dict | None = None,
                          business_block: str = "",
                          narrative_plan: dict | None = None,
                          policy_block: str = "",
                          generation_unit: dict | None = None) -> list[dict]:
        """单章:按 ChapterPlan 执行生成(不重新规划),过滤无依据句子。

        返回 [{text, fact_ids, inference_ids, paragraph}];单章失败返回 []。
        独立方法以便验证闭环对缺失/问题章节复用(补写)。
        """
        chapter_plan = chapter_plan or {}
        generation_unit = generation_unit or {"index": 1, "count": 1, "title": "", "plan": None}
        from app.document_shape import normalize_document_shape
        document_shape = normalize_document_shape(plan.get("document_shape"))
        chapter_target_words = int(chapter_plan.get("target_words") or 0)
        section_plan_block = _json_dumps({
            "本章目标字数": chapter_target_words,
            "本章回答的问题": chapter_plan.get("questions", []),
            "本章核心判断/目的": chapter_plan.get("judgment", ""),
            "与上一章的关系": chapter_plan.get("relation_to_prev", ""),
            "所需事实类别": chapter_plan.get("required_facts", []),
            "所需推断类别": chapter_plan.get("required_inferences", []),
            "本章不写(禁止重复)": chapter_plan.get("exclude", []),
            "结尾承接下一章": chapter_plan.get("next_bridge", ""),
        }, limit=800)
        narrative_block = _json_dumps(narrative_plan or {}, limit=1600)
        rules_block = ""
        if institution_rules:
            must = institution_rules.get("must_include") or []
            if must:
                rules_block = (
                    "机构级全文要求(仅当前小节与其直接相关时落实,不要在每个小节重复):\n- "
                    + "\n- ".join(str(m) for m in must) + "\n"
                )
        unit_plan = generation_unit.get("plan") or {}
        # Task-wide validity only proves an ID exists. The Writer must also be
        # constrained to evidence deliberately supplied to this generation
        # unit; otherwise a model can cite a real but out-of-scope Fact and
        # silently corrupt allocation/coverage metrics.
        allowed_fact_ids = {int(item.get("id") or 0) for item in chapter_facts}
        allowed_inference_ids = {int(item.get("id") or 0) for item in chapter_inferences}
        preferred_fact_ids = set(_int_ids(unit_plan.get("fact_ids")))
        ordered_facts = sorted(
            chapter_facts,
            key=lambda item: 0 if int(item.get("id") or 0) in preferred_fact_ids else 1,
        )
        fact_lines = [f"{_f['id']}. {_f['content']}" for _f in ordered_facts]
        preferred_inference_ids = set(_int_ids(unit_plan.get("inference_ids")))
        ordered_inferences = sorted(
            chapter_inferences,
            key=lambda item: 0 if int(item.get("id") or 0) in preferred_inference_ids else 1,
        )
        inference_lines = [f"{i['id']}. ({i['source_level']}) {i['content']}" for i in ordered_inferences]
        memory_block = self._memory_block(report_memory)
        discourse_block = _discourse_plan_block(narrative_plan)
        planned_subsections = _planned_subsection_titles(narrative_plan)
        subsection_hint = _subsection_execution_hint(planned_subsections)
        paragraph_hint = _paragraph_execution_hint(unit_plan.get("paragraphs"))
        evidence_hint = _evidence_execution_hint(generation_unit)
        current_unit_block = _json_dumps(unit_plan, limit=900) if unit_plan else ""
        def build_prompt(selected_facts: list[str], selected_inferences: list[str]) -> str:
            return (
            f"报告标题:{plan.get('title', '')}\n"
            + f"文档形态:{document_shape['kind']}；一级标题:{document_shape['heading_policy']}；二级标题:{document_shape['subheading_policy']}。"
              "内部语义单元只用于组织证据；是否显示标题由文档形态决定，不得自行添加编号或 Markdown 标题。\n"
            f"报告核心判断:{plan.get('core_judgment', '')}\n"
            f"总体叙事逻辑:{plan.get('narrative_logic', '')}\n"
            + (f"本轮更新目标:{plan.get('update_instruction', '')}\n" if plan.get("update_instruction") else "")
            + (
                f"本章有效规模目标:约 {chapter_target_words} 字。"
                "请围绕当前语义单元充分展开；证据不足时宁可少写，不得重复或虚构。\n"
                if chapter_target_words else ""
            )
            + (rules_block + "\n" if rules_block else "")
            + f"正在撰写第 {chapter_index}/{chapter_count} 章:「{chapter_title}」\n"
            + (
                f"当前规划小节:{generation_unit.get('index', 1)}/{generation_unit.get('count', 1)} "
                f"「{generation_unit.get('title', '')}」;本小节目标约 {chapter_target_words} 字。\n"
                if generation_unit.get("title") else "当前章节未规划多级小节,本次完整撰写本章。\n"
            )
            + "全文章节结构:" + (" / ".join(structure)) + "\n\n"
            + f"Report Memory(前文状态):\n{memory_block}\n\n"
            + f"ChapterPlan:\n{section_plan_block}\n\n"
            + f"Narrative Plan(优先执行,用于决定语义结构、事实主次和逻辑):\n{narrative_block}\n\n"
            + (f"当前小节计划(本次只完成这一语义单元):\n{current_unit_block}\n\n" if current_unit_block else "")
            + (f"Discourse Plan(每个话题的论证次序;可将直接相关的相邻角色自然合并,不得按事实编号罗列):\n{discourse_block}\n\n" if discourse_block else "")
            + (f"{paragraph_hint}\n\n" if paragraph_hint else "")
            + (f"业务约束:\n{business_block}\n\n" if business_block else "")
            + (f"{policy_block}\n\n" if policy_block else "")
            + (subsection_hint + "\n" if subsection_hint else "")
            + (evidence_hint + "\n" if evidence_hint else "")
            + (
                "当前小节计划中的 fact_ids/inference_ids 是完成本小节目的的核心证据。"
                "不得为了简短而遗漏完成条件所必需的核心证据；其他补充事实按论证需要选择，不追求机械全覆盖。\n"
                if unit_plan else ""
            )
            + "本章相关事实清单(当前小节事实优先排列,其余事实仅用于必要背景和关系校验):\n" + "\n".join(selected_facts) + "\n\n"
            + "本章相关推断清单:\n" + "\n".join(selected_inferences) + "\n\n"
            + f"{chapter_style}\n"
            + "请严格按 Narrative Plan 和 ChapterPlan 撰写当前章节或小节。若提供段落执行计划，必须遵守其边界和顺序；"
              "否则先在内部设计自然段的主题、顺序和承接关系，再输出正式、连贯、可阅读的正文;"
              "自然段数量和每段篇幅由规划的语义关系、证据密度与阅读需要决定，不要套用固定段数;"
              "不要把 fact 清单改写成一串短句。输出必须是一个 JSON 对象,优先只包含 paragraphs 字段;"
              "paragraphs 每项代表一个自然段,包含 sentences 数组;sentences 每项包含 text/fact_ids/inference_ids。"
              "同一自然段内多句话应放在同一个 paragraph 中,不要每句话单独建段。"
              "不要输出小标题或 source_level=\"SUBHEADING\";小标题由系统按 Narrative Plan 渲染。不要输出解释、备选文本、Markdown 代码块或额外字段。"
            )

        empty_prompt = build_prompt([], [])
        prompt_budget = _writer_prompt_token_budget()
        evidence_tokens = max(0, prompt_budget - count_tokens(empty_prompt))
        fact_lines, inference_lines = _pack_writer_evidence(
            fact_lines, inference_lines, evidence_tokens,
        )
        prompt = build_prompt(fact_lines, inference_lines)
        prompt_tokens = count_tokens(prompt)
        publish_context_audit({
            "stage": "writer",
            "tokenizer_method": tokenizer_method(),
            "budget_tokens": prompt_budget,
            "actual_tokens": prompt_tokens,
            "utilization": round(prompt_tokens / max(prompt_budget, 1), 4),
            "truncated": len(fact_lines) < len(ordered_facts) or len(inference_lines) < len(ordered_inferences),
            "sections": {
                "事实": {
                    "candidate_items": len(ordered_facts), "selected_items": len(fact_lines),
                    "omitted_items": max(0, len(ordered_facts) - len(fact_lines)),
                },
                "推论": {
                    "candidate_items": len(ordered_inferences), "selected_items": len(inference_lines),
                    "omitted_items": max(0, len(ordered_inferences) - len(inference_lines)),
                },
            },
        })
        try:
            payload = self.generate_json(prompt)
        except Exception:
            return []  # 单章失败不中断,其余章节继续
        call_id = self.last_call_id
        paragraphs = _coerce_paragraphs(payload)
        result: list[dict] = []
        paragraph_number = 0
        transition_budget = (
            4
            if chapter_facts or chapter_inferences else 0
        )
        model_sentences = 0
        accepted_sentences = 0
        dropped_untraced_sentences = 0
        split_item_extra_count = 0
        for paragraph in paragraphs:
            sentences = paragraph.get("sentences", [])
            if not sentences:
                continue
            paragraph_number += 1
            for sentence in sentences:
                if not isinstance(sentence, dict):
                    dropped_untraced_sentences += 1
                    continue
                model_sentences += 1
                text = str(sentence.get("text", "")).strip()
                sentence_level = str(sentence.get("source_level") or "").strip()
                fact_ids = [
                    i for i in _int_ids(sentence.get("fact_ids"))
                    if i in valid_fact_ids and i in allowed_fact_ids
                ]
                inf_ids = [
                    i for i in _int_ids(sentence.get("inference_ids"))
                    if i in valid_inf_ids and i in allowed_inference_ids
                ]
                split_items_for_sentence = _split_structured_items(text)
                split_items_count = len(split_items_for_sentence)
                split_item_extra_count += max(0, split_items_count - 1)
                trace_warning = split_items_count > 1
                for item_text in split_items_for_sentence:
                    if not item_text:
                        continue
                    if _is_duplicate_structure_sentence(item_text, chapter_title, str(plan.get("title", ""))):
                        continue
                    if sentence_level == "SUBHEADING":
                        continue
                    part_candidates = _split_embedded_subheading(item_text, planned_subsections)
                    for part in part_candidates:
                        part_text = part["text"]
                        is_subheading = part.get("source_level") == "SUBHEADING"
                        if is_subheading:
                            continue
                        part_fact_ids = [] if is_subheading else fact_ids
                        part_inf_ids = [] if is_subheading else inf_ids
                        if not part_fact_ids and not part_inf_ids and not is_subheading:
                            # 无依据句:仅允许少量过渡句(连接词开头/短句),超出丢弃
                            if (
                                transition_budget <= 0
                                or len(part_text) > 40  # 过渡句长度上限(工程约束,防无依据长句)
                            ):
                                dropped_untraced_sentences += 1
                                continue
                            transition_budget -= 1
                        accepted_sentences += 1
                        result.append({
                            "text": part_text, "fact_ids": part_fact_ids, "inference_ids": part_inf_ids,
                            "paragraph": paragraph_number,
                            "source_level": part.get("source_level", ""),
                            "trace_granularity_warning": trace_warning and not is_subheading,
                            "_origin_call_id": call_id,
                        })
        update_call_funnel(
            call_id,
            model_sentences=model_sentences,
            accepted_sentences=accepted_sentences,
            dropped_untraced_sentences=dropped_untraced_sentences,
            split_items=split_item_extra_count,
        )
        return result

    def _paragraph_texts(self, report_id: int, chapter_title: str) -> list[str]:
        """本章句子按段落分组(供 Narrative QA 判断)。"""
        with session_scope() as s:
            rows = s.execute(
                select(ORMSentence.c.paragraph, ORMSentence.c.content)
                .where(
                    ORMSentence.c.report_id == report_id,
                    ORMSentence.c.section == chapter_title,
                )
                .order_by(ORMSentence.c.position)
            ).mappings().all()
        paragraphs: dict[int, list[str]] = {}
        for row in rows:
            paragraphs.setdefault(int(row["paragraph"]), []).append(row["content"])
        return ["".join(paragraphs[key]) for key in sorted(paragraphs)]

    def _chapter_texts(self, report_id: int, chapter_title: str) -> list[str]:
        with session_scope() as s:
            rows = s.execute(
                select(ORMSentence.c.content)
                .where(
                    ORMSentence.c.report_id == report_id,
                    ORMSentence.c.section == chapter_title,
                )
                .order_by(ORMSentence.c.position)
            ).mappings().all()
        return [row["content"] for row in rows]

    def _record_narrative_qa(self, chapter_title: str, qa_result: dict) -> None:
        try:
            from app.task_artifacts import save_task_artifact

            save_task_artifact(
                getattr(self, "_task_id", "") or "",
                f"narrative_qa:{chapter_title}",
                {"chapter": chapter_title},
                qa_result,
                run_id=getattr(self, "_run_id", "") or "",
            )
        except Exception:
            pass

    def _rewrite_paragraph(self, report_id, chapter_title, chapter_index, qa_item,
                           narrative_plan, chapter_facts, chapter_inferences,
                           valid_fact_ids, valid_inf_ids, inf_levels,
                           plan, report_memory) -> dict | None:
        """Narrative QA 定位段落 → 原段 + Topic Plan + 支撑事实 → 局部重写替换。

        返回 {"fact_ids", "inference_ids"};失败返回 None(保留原段)。
        """
        target = int(qa_item.get("target_paragraph") or 0)
        if target <= 0:
            return None
        with session_scope() as s:
            rows = s.execute(
                select(ORMSentence.c.id, ORMSentence.c.content, ORMSentence.c.position)
                .where(
                    ORMSentence.c.report_id == report_id,
                    ORMSentence.c.section == chapter_title,
                    ORMSentence.c.paragraph == target,
                )
                .order_by(ORMSentence.c.position)
            ).mappings().all()
        if not rows:
            return None
        original_positions = [int(r["position"]) for r in rows]
        original = "".join(r["content"] for r in rows)
        topic = next(
            (t for t in (narrative_plan.get("topics") or [])
             if t.get("topic_id") == qa_item.get("topic_id")),
            {},
        )
        tids = {int(fid) for fid in (topic.get("fact_ids") or []) if str(fid).isdigit()}
        support_facts = [f for f in chapter_facts if f.get("id") in tids] or chapter_facts
        from app.runtime_profiles import stage_input_budget_tokens

        budget = stage_input_budget_tokens("writer")
        topic_block = json.dumps(topic, ensure_ascii=False)
        missing = "、".join(qa_item.get("missing_aspects") or []) or "按 Topic 计划完善表达"
        prompt, _audit = build_prompt_from_sections(
            "writer",
            [
                ContextSection("任务", [
                    f"章节:{chapter_title}(局部重写第 {target} 段,保持与前后段衔接)",
                    f"待改进方面:{missing}",
                    "请重写该段落:解决待改进方面,用支撑事实展开,不要罗列事实编号,"
                    "不要重复段落外的内容。输出 JSON,优先只包含 paragraphs 字段"
                    "(一个自然段对象,内部 sentences 每项含 text/fact_ids/inference_ids)。",
                ], weight=4, required_items=3),
                ContextSection("Narrative Topic", ["Narrative Topic 计划:", topic_block], weight=4, required_items=2),
                ContextSection("原段落", ["原段落:", original], weight=4, required_items=2),
                ContextSection("支撑事实", ["支撑事实:", *[
                    f"{fact['id']}. {fact.get('content', '')}" for fact in support_facts
                ]], weight=5),
            ],
            budget,
        )
        try:
            payload = self.generate_json(prompt)
        except Exception:
            return None
        paragraphs = _coerce_paragraphs(payload)
        new_sentences: list[dict] = []
        for paragraph in paragraphs:
            for sentence in paragraph.get("sentences", []):
                if not isinstance(sentence, dict):
                    continue
                text = str(sentence.get("text", "")).strip()
                fact_ids = [i for i in _int_ids(sentence.get("fact_ids")) if i in valid_fact_ids]
                inf_ids = [i for i in _int_ids(sentence.get("inference_ids")) if i in valid_inf_ids]
                if not fact_ids and not inf_ids:
                    continue  # 重写句也必须有依据
                new_sentences.append({"text": text, "fact_ids": fact_ids, "inference_ids": inf_ids})
        if not new_sentences:
            return None
        written_fact_ids: set[int] = set()
        written_inference_ids: set[int] = set()
        with session_scope() as s:
            sentence_ids = [r["id"] for r in rows]
            s.execute(
                delete(ORMSentenceFact).where(ORMSentenceFact.c.sentence_id.in_(sentence_ids))
            )
            s.execute(
                delete(ORMSentenceInference).where(ORMSentenceInference.c.sentence_id.in_(sentence_ids))
            )
            s.execute(
                delete(ORMSentence).where(ORMSentence.c.id.in_(sentence_ids))
            )
            for idx, sent in enumerate(new_sentences, start=1):
                position = original_positions[idx - 1] if idx <= len(original_positions) else original_positions[-1] + idx
                level = "MATERIAL_FACT" if sent["fact_ids"] else inf_levels.get(sent["inference_ids"][0], "MATERIAL_INFERENCE")
                source_refs = {"fact_ids": sent["fact_ids"], "inference_ids": sent["inference_ids"]}
                cur = s.execute(
                    ORMSentence.insert().values(
                        report_id=report_id, section=chapter_title, paragraph=target,
                        position=position, content=sent["text"], source_level=level,
                        source_refs=json.dumps(source_refs, ensure_ascii=False),
                        origin_call_id=self.last_call_id,
                    )
                )
                sentence_id = int(cur.inserted_primary_key[0])
                for fid in dict.fromkeys(sent["fact_ids"]):
                    existing = s.execute(
                        select(ORMSentenceFact.c.sentence_id).where(
                            ORMSentenceFact.c.sentence_id == sentence_id,
                            ORMSentenceFact.c.fact_id == fid,
                        )
                    ).first()
                    if existing is None:
                        s.execute(ORMSentenceFact.insert().values(sentence_id=sentence_id, fact_id=fid))
                for iid in dict.fromkeys(sent["inference_ids"]):
                    existing = s.execute(
                        select(ORMSentenceInference.c.sentence_id).where(
                            ORMSentenceInference.c.sentence_id == sentence_id,
                            ORMSentenceInference.c.inference_id == iid,
                        )
                    ).first()
                    if existing is None:
                        s.execute(ORMSentenceInference.insert().values(sentence_id=sentence_id, inference_id=iid))
                written_fact_ids.update(sent["fact_ids"])
                written_inference_ids.update(sent["inference_ids"])
        return {"fact_ids": written_fact_ids, "inference_ids": written_inference_ids}

    @staticmethod
    def _memory_block(report_memory: dict) -> str:
        """Report Memory 摘要:注入最近前文状态,保持衔接但不维护依赖图。"""
        lines = []
        if report_memory.get("core_judgment"):
            lines.append(f"核心观点: {report_memory['core_judgment']}")
        if report_memory.get("narrative_logic"):
            lines.append(f"总体叙事逻辑: {report_memory['narrative_logic']}")
        # 统一术语:全部注入(术语定义全文一致,不受依赖限制)
        terms = report_memory.get("unified_terms", [])
        if terms:
            term_lines = []
            for t in terms[-12:]:
                if isinstance(t, dict):
                    term_lines.append(f"{t.get('term', '')}({t.get('definition', '')[:40]})")
                else:
                    term_lines.append(str(t))
            lines.append(f"已定义术语(后文保持定义一致): {', '.join(term_lines)}")
        # 已形成判断:全部注入(可引用/发展,不重新论证)
        judgments = report_memory.get("formed_judgments", [])
        if judgments:
            lines.append("已形成判断(可引用发展,不要重新论证一遍):")
            for j in judgments[-6:]:
                lines.append(f"- {j}")
        # 未解决问题:全部注入(后续章节优先回应)
        unresolved = report_memory.get("unresolved_issues", [])
        if unresolved:
            lines.append(f"尚未解决的问题(后续章节如有证据优先回应): {'; '.join(unresolved[-4:])}")
        # 章节摘要:按自然顺序注入最近前文章节,避免依赖图字段造成规划失败。
        summaries = report_memory.get("chapter_summaries", [])
        relevant = summaries[-2:]
        if relevant:
            lines.append("前文章节记忆(用于承接其结论,不要重复其内容):")
            for s in relevant:
                core = s.get("core_message") or ""
                lines.append(f"- {s['chapter']}: {s['summary'][:100]}" + (f" | 核心: {core[:60]}" if core else ""))
        # 已表达观点:避免原样重复(注入最近几条)
        points = report_memory.get("expressed_points", [])
        if points:
            lines.append(f"前文已表达观点(避免原样重复,如需引用须换角度): {'; '.join(points[-4:])}")
        # Fact 角色:复用须承担新逻辑作用
        roles = report_memory.get("fact_roles", {})
        if roles:
            role_str = ", ".join(f"{fid}→{role}" for fid, role in list(roles.items())[-8:])
            lines.append(f"前文 Fact 已承担角色(复用须承担新角色): {role_str}")
        used = report_memory.get("used_fact_ids", set())
        if used:
            lines.append(f"前文已使用事实编号: {sorted(used)}")
        used_inf = report_memory.get("used_inference_ids", set())
        if used_inf:
            lines.append(f"前文已使用推断编号: {sorted(used_inf)}(如需再次引用必须承担新的逻辑作用,禁止原文重复)")
        return "\n".join(lines) or "无"
