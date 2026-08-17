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

from app.agents.base import BaseAgent
from app.planning.narrative import narrative_agent
from app.db import session_scope
from app.infrastructure.orm import Base, ORMReport, ORMSentence, ORMTaskArtifact
from sqlalchemy import select, update, delete
from app.models import Report
from app.policy import policy_prompt_block
from app.token_monitor import update_call_funnel, update_call_metrics, update_call_products


ORMSentenceFact = Base.metadata.tables["report_sentence_fact"]
ORMSentenceInference = Base.metadata.tables["report_sentence_inference"]


_SYSTEM = """你是情报报告撰稿人。依据 Narrative Plan、ChapterPlan 与机构风格,撰写成文的自然语言报告。
每次只撰写一个章节,严格输出 JSON,不要任何解释:
{"sentences": [{"paragraph": 1, "text": "句子内容", "fact_ids": [1], "inference_ids": []}]}
要求:
1. 严格按 Narrative Plan 与 ChapterPlan 的核心问题、事实主次和逻辑顺序组织内容,不得偏离
2. 不重复 Report Memory 中"前文已表述"的内容(其他章节/前章摘要)
3. 每个段落围绕一个核心观点,事实与分析自然融合,判断必须有依据
4. 事实句引用 fact_ids,推断句引用 inference_ids,id 必须真实存在
5. 正文不得出现来源标签、内部编号或证据标注;引用关系只写在 JSON 的 fact_ids/inference_ids 中
6. 只撰写当前指定章节,不要输出其他章节内容
7. 承上启下、章节导语等过渡句可以没有引用,但整章不超过 2 句,且不得编造事实
8. 若当前材料包缺少真实成果材料,不得写成“已取得显著成效/完成某项成果/满意度提升”等未被材料直接证明的结论
9. 段落、句式、详略、表格或分项表达由 ChapterPlan、模板风格、材料信息量和分层策略共同决定;不要机械套用固定格式
10. 不要把无关 Fact 压缩拼接进同一句;一段一主题,一个句子通常只承担一个核心事实或一个直接相关的事实组
11. 具体事实优先,少使用“机制完善、顶层定标、全面就位”等没有新增信息的抽象套话
12. 不要把事实清单直接排列成正文;每段应有主题句、必要解释和自然承接,使读者能理解事实之间的关系
13. Narrative Plan 中 detail_level=expand 的话题应适度展开;brief/reference 只简要承接,不得平均铺陈"""


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


def _business_block(task_profile: dict, chapter_plan: dict) -> str:
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
    return text[:limit] + ("…" if limit and len(text) > limit else "")


def _text_length(text: str) -> int:
    """Approximate Chinese report length in the same unit used by scale stats."""
    return len(re.findall(r"[\u4e00-\u9fff]", text or "")) + len(re.findall(r"[A-Za-z0-9]+", text or ""))


def _ids_used_in_chapter(report_id: int, chapter_title: str) -> dict[str, set[int]]:
    with session_scope() as s:
        rows = s.execute(
            select(ORMSentence.c.source_refs).where(
                ORMSentence.c.report_id == report_id,
                ORMSentence.c.section == chapter_title,
            )
        ).mappings().all()
    fact_ids: set[int] = set()
    inference_ids: set[int] = set()
    for row in rows:
        try:
            refs = json.loads(row["source_refs"] or "{}")
        except (TypeError, ValueError):
            continue
        fact_ids.update(_int_ids(refs.get("fact_ids")))
        inference_ids.update(_int_ids(refs.get("inference_ids")))
    return {"fact_ids": fact_ids, "inference_ids": inference_ids}




def _execution_format_hint(chapter_title: str, chapter_plan: dict, facts: list[dict]) -> str:
    """Tell Writer when structured output is useful, without domain vocabulary."""
    evidence_texts = [str(f.get("content", "")) for f in facts]
    plan_text = " ".join([
        chapter_title,
        " ".join(chapter_plan.get("questions") or []),
        " ".join(chapter_plan.get("required_facts") or []),
    ])
    if not _looks_structurable(plan_text, evidence_texts):
        return ""
    return (
        "表达组织策略(软策略,服从模板风格和 ChapterPlan):\n"
        "- 若本章包含多项并列要求、时间节点、条件、流程、责任分工或可核对事项,且分项表达能降低阅读成本,可采用正式中文分项句;不要机械清单化。\n"
        "- 若采用分项表达,每个事实性分项尽量写成独立 sentences[] 对象,并绑定该分项自己的 fact_ids/inference_ids。\n"
        "- 同一事实或要求优先在最合适章节展开;其他章节只做必要承接,但不得删掉 required facts 或关键风险。\n"
        "- 若 Fact 中有具体日期、文件名、对象名、数量、比例、地点或渠道,优先写出具体值;证据没有明确值时不要猜测。\n"
        "- 除非模板明确采用列表符号,正文避免 Markdown 式、界面式前缀,使用自然中文报告句式。\n"
    )


def _paragraph_organization_hint(chapter_title: str, facts: list[dict]) -> str:
    """Generic writing strategy: organize facts before drafting prose."""
    groups: dict[str, list[int]] = {}
    for fact in facts:
        if fact.get("id") is None:
            continue
        label = _fact_topic_label(fact)
        groups.setdefault(label, []).append(int(fact["id"]))
    lines = [f"- {label}: facts {ids}" for label, ids in groups.items() if ids]
    if not lines:
        return ""
    return (
        "段落级信息组织策略(软策略):\n"
        f"当前章节「{chapter_title}」可先按以下话题组织,再决定自然段顺序:\n"
        + "\n".join(lines[:5])
        + "\n写作时一段聚焦一个话题;不同层级信息不要硬塞进同一句。"
        "允许适度展开说明事实含义,但不要用空泛套话替代具体事实。\n"
    )


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


def _looks_structurable(plan_text: str, evidence_texts: list[str]) -> bool:
    text = f"{plan_text}\n" + "\n".join(evidence_texts[:16])
    if len(evidence_texts) >= 4:
        return True
    if re.search(r"\d{1,4}(?:\.\d+)?%?|\d{1,2}月\d{1,2}日|\d{4}年", text):
        return True
    if re.search(r"[一二三四五六七八九十]+[、.]|\d+[、.)]|[;；]", text):
        return True
    return any(term in text for term in ("条件", "节点", "要求", "流程", "责任", "风险", "清单", "步骤", "标准"))


def _fact_topic_label(fact: dict) -> str:
    for key in ("dimension", "fact_type"):
        value = str(fact.get(key) or "").strip()
        if value:
            return value[:24]
    roles = [str(item) for item in fact.get("source_roles") or [] if str(item).strip()]
    if roles:
        return roles[0][:24]
    return "相关事实"


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
    value = re.sub(r"^([^：:]{1,12})[:：]\s*(.+)$", r"\1方面，\2", value)
    return value


_SUBHEADING_PREFIX_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)+|[（(][一二三四五六七八九十]+[）)]|[一二三四五六七八九十]+[、.])\s*"
)


def _planned_subsection_titles(narrative_plan: dict) -> list[str]:
    titles = []
    for item in (narrative_plan or {}).get("subsections") or []:
        title = str(item.get("title") or "").strip()
        if title:
            titles.append(title)
    return titles


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
        "如使用小标题,只能使用以上标题,并单独输出为 source_level=\"SUBHEADING\" 的 sentence;"
        "小标题后正文另起 sentence,不得把“小标题。正文”粘在同一 text 中。"
    )


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


def _planned_heading_part(text: str, allowed_titles: list[str]) -> list[dict]:
    clean_heading = _clean_generated_subheading(text)
    if not _heading_allowed(clean_heading, allowed_titles):
        return []
    return [{"text": clean_heading, "source_level": "SUBHEADING"}]


def _clean_generated_subheading(text: str) -> str:
    value = str(text or "").strip()
    value = _SUBHEADING_PREFIX_RE.sub("", value)
    value = re.split(r"[。！？!?；;\n]", value, maxsplit=1)[0].strip()
    return value


def _extract_embedded_subheading(text: str) -> tuple[str, str] | None:
    if not _SUBHEADING_PREFIX_RE.match(text):
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


def _fit_block(text: str, budget_chars: int, overhead_chars: int = 800) -> str:
    """按剩余上下文容量截取单个文本块(预算来自模型上下文物理上限)。"""
    remaining = max(0, budget_chars - overhead_chars)
    return text[:remaining] if len(text) > remaining else text


def _chapter_position(chapter_index: int, local_position: int) -> int:
    return max(1, int(chapter_index or 1)) * 10000 + max(1, int(local_position or 1))


def _coerce_paragraphs(payload: dict) -> list[dict]:
    """Normalize model JSON into paragraphs[{sentences:[...]}] without crashing.

    JSON mode guarantees syntactic JSON, not business schema. If the model returns
    strings or alternate section wrappers, keep only objects we can safely trace.
    """
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
        return []
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
            result.append(item)
        elif isinstance(item, str):
            result.append({"sentences": [{"text": item, "fact_ids": [], "inference_ids": []}]})
    return result


class WriterAgent(BaseAgent):
    name = "writer"
    role = _SYSTEM
    max_retries = 1

    def write(self, plan: dict, facts: list[dict], inferences: list[dict],
              style_block: str, profile_id: int | None = None, cm=None,
              institution_rules: dict | None = None, task_profile: dict | None = None,
              progress_callback=None,
              existing_report_id: int | None = None, report_callback=None,
              chapter_callback=None, report_policy: dict | None = None,
              task_id: str = "", attribution_plans: dict | None = None) -> Report:
        """按 ReportPlan + ChapterPlan[] 逐章执行生成(不重复规划)。

        ChapterPlan 决定本章信息需求:章节标题 + 核心问题 + 核心判断 + 所需事实
        共同构建检索 query,使文章逻辑与材料检索打通。
        institution_rules:机构硬性要求(must_include 等),注入 Writer 约束全文覆盖。
        """
        self._task_id = task_id
        chapters = plan.get("chapter_plans") or [{"title": t} for t in (plan.get("structure") or [])]
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
            "chapter_summaries": [],       # {chapter, summary, core_message, dependencies}
        }

        report = self._open_or_create_report(plan, profile_id, existing_report_id)
        if report_callback:
            report_callback(report)

        completed_sections, position = self._resume_report_memory(report.id, report_memory, chapters)
        chapter_count = len(chapters) or 1
        for chapter_index, chapter_plan in enumerate(chapters, start=1):
            chapter_title = str(chapter_plan.get("title", "") or f"章节{chapter_index}")
            chapter_start = time.time()
            if chapter_title in completed_sections:
                if progress_callback:
                    progress_callback(chapter_index, chapter_count, chapter_title, "resumed", 0)
                continue
            self._discard_incomplete_chapter(report.id, chapter_title)
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
            )
            attribution_plan = (attribution_plans or {}).get(chapter_title)
            # 2. 执行 ChapterPlan 生成(不再重复规划)。生成阶段不持有 SQLite 写事务。
            chapter_sentences = self._generate_chapter(
                chapter_title, chapter_index, chapter_count,
                [c.get("title", "") for c in chapters],
                chapter_facts, chapter_inferences, chapter_style,
                plan, report_memory, valid_fact_ids, valid_inf_ids,
                chapter_plan=chapter_plan,
                institution_rules=institution_rules,
                narrative_plan=narrative_plan,
                attribution_plan=attribution_plan,
                policy_block=policy_prompt_block(_writer_policy_only(report_policy or {})),
            )
            paragraph_number = 0
            written_fact_ids: set[int] = set()
            written_inference_ids: set[int] = set()
            chapter_text: list[str] = []
            chapter_call_id = self.last_call_id
            sentence_ids: list[int] = []
            with session_scope() as s:
                for sent in chapter_sentences:
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
                    sent_cur = s.execute(
                        ORMSentence.insert().values(
                            report_id=report.id, section=chapter_title,
                            paragraph=paragraph_number, position=position,
                            content=sent["text"], source_level=level,
                            source_refs=json.dumps(source_refs, ensure_ascii=False),
                            origin_call_id=chapter_call_id,
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
            update_call_products(
                chapter_call_id,
                produced_chapter_ids=[chapter_index],
                final_used_fact_ids=sorted(written_fact_ids),
                final_used_inference_ids=sorted(written_inference_ids),
            )
            stored_chars = sum(len(text or "") for text in chapter_text)
            update_call_metrics(chapter_call_id, stored_chars=stored_chars, final_chars=stored_chars)
            # 3. Narrative QA:按 Topic 完成条件做语义判断(字数只作观察,不触发补写)
            qa_result = narrative_agent.qa_chapter(
                chapter_title,
                self._paragraph_texts(report.id, chapter_title),
                narrative_plan,
                chapter_facts,
            )
            for item in qa_result.get("topics") or []:
                if item["action"] in ("rewrite", "expand") and item["evidence_sufficient"] and item["target_paragraph"] > 0:
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
                "dependencies": narrative_plan.get("dependencies") or [],
            })
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
                })
        # EvidenceGap 接口(WriteHERE 预留):未解决问题汇总落 artifact,供 Evidence 回流/后续升级
        gaps = report_memory.get("unresolved_issues") or []
        if gaps:
            try:
                from app.task_artifacts import save_task_artifact
                save_task_artifact(task_id, "evidence_gaps", {"source": "writer"}, {"gaps": gaps})
            except Exception:
                pass
        return report

    def _discard_incomplete_chapter(self, report_id: int, chapter_title: str) -> None:
        with session_scope() as s:
            rows = s.execute(
                select(ORMSentence.c.id).where(
                    ORMSentence.c.report_id == report_id,
                    ORMSentence.c.section == chapter_title,
                )
            ).mappings().all()
            sentence_ids = [int(r["id"]) for r in rows]
            if not sentence_ids:
                return
            s.execute(
                delete(ORMSentenceFact).where(ORMSentenceFact.c.sentence_id.in_(sentence_ids))
            )
            s.execute(
                delete(ORMSentenceInference).where(ORMSentenceInference.c.sentence_id.in_(sentence_ids))
            )
            s.execute(
                delete(ORMSentence).where(ORMSentence.c.id.in_(sentence_ids))
            )

    def _open_or_create_report(self, plan: dict, profile_id: int | None,
                               existing_report_id: int | None = None) -> Report:
        if existing_report_id is not None:
            with session_scope() as s:
                row = s.execute(
                    select(ORMReport).where(ORMReport.c.id == int(existing_report_id))
                ).mappings().first()
            if row is not None:
                return Report(
                    id=row["id"],
                    plan_id=row["plan_id"],
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

    def _resume_report_memory(self, report_id: int, report_memory: dict, chapters: list[dict]) -> tuple[set[str], int]:
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
                    ORMTaskArtifact.c.stage.like("chapter_draft:%"),
                    ORMTaskArtifact.c.status == "done",
                )
            ).mappings().all()
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
            if row["section"] in completed_markers:
                completed_sections.add(row["section"])
            section_text.setdefault(row["section"], []).append(row["content"])
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
                          attribution_plan: dict | None = None,
                          policy_block: str = "") -> list[dict]:
        """单章:按 ChapterPlan 执行生成(不重新规划),过滤无依据句子。

        返回 [{text, fact_ids, inference_ids, paragraph}];单章失败返回 []。
        独立方法以便验证闭环对缺失/问题章节复用(补写)。
        """
        chapter_plan = chapter_plan or {}
        section_plan_block = _json_dumps({
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
                rules_block = "机构硬性要求(报告全文必须覆盖,缺一不可):\n- " + "\n- ".join(str(m) for m in must) + "\n"
        fact_lines = [f"{f['id']}. {f['content']}" for f in chapter_facts]
        inference_lines = [f"{i['id']}. ({i['source_level']}) {i['content']}" for i in chapter_inferences]
        memory_block = self._memory_block(report_memory, dependencies=(narrative_plan or {}).get("dependencies") or [])
        discourse_block = _discourse_plan_block(narrative_plan)
        execution_hint = _execution_format_hint(chapter_title, chapter_plan, chapter_facts)
        organization_hint = _paragraph_organization_hint(chapter_title, chapter_facts)
        planned_subsections = _planned_subsection_titles(narrative_plan)
        subsection_hint = _subsection_execution_hint(planned_subsections)
        prompt = (
            f"报告标题:{plan.get('title', '')}\n"
            f"报告核心判断:{plan.get('core_judgment', '')}\n"
            f"总体叙事逻辑:{plan.get('narrative_logic', '')}\n"
            + (rules_block + "\n" if rules_block else "")
            + f"正在撰写第 {chapter_index}/{chapter_count} 章:「{chapter_title}」\n"
            + "全文章节结构:" + (" / ".join(structure)) + "\n\n"
            + f"Report Memory(前文状态):\n{memory_block}\n\n"
            + f"ChapterPlan:\n{section_plan_block}\n\n"
            + f"Narrative Plan(优先执行,用于决定事实组织、主次和段落逻辑):\n{narrative_block}\n\n"
            + (f"Discourse Plan(每个话题的段落逻辑流,必须按 flow 顺序逐段写,一段一个角色,禁止打散或按事实编号罗列):\n{discourse_block}\n\n" if discourse_block else "")
            + (f"Evidence Attribution Plan(每章观点与证据绑定,写作必须遵循;无证据观点禁止输出):\n{_attribution_block(attribution_plan)}\n\n" if _attribution_block(attribution_plan) else "")
            + (f"业务约束:\n{business_block}\n\n" if business_block else "")
            + (f"{policy_block}\n\n" if policy_block else "")
            + (execution_hint + "\n" if execution_hint else "")
            + (organization_hint + "\n" if organization_hint else "")
            + (subsection_hint + "\n" if subsection_hint else "")
            + "本章相关事实清单:\n" + "\n".join(fact_lines) + "\n\n"
            + "本章相关推断清单:\n" + "\n".join(inference_lines) + "\n\n"
            + f"{chapter_style}\n"
            + "请严格按 Narrative Plan 和 ChapterPlan 撰写本章内容。先根据 subsections/topics/logic_order 组织段落,再写成正式、连贯、可阅读的报告正文;"
              "不要把 fact 清单改写成一串短句。输出必须是一个 JSON 对象,优先只包含 sentences 字段;"
              "sentences 每项包含 paragraph/text/fact_ids/inference_ids,小标题句可额外包含 source_level=\"SUBHEADING\"。不要输出解释、备选文本、Markdown 代码块或额外字段。"
        )
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
                fact_ids = [i for i in _int_ids(sentence.get("fact_ids")) if i in valid_fact_ids]
                inf_ids = [i for i in _int_ids(sentence.get("inference_ids")) if i in valid_inf_ids]
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
                        part_candidates = _planned_heading_part(item_text, planned_subsections)
                    else:
                        part_candidates = _split_embedded_subheading(item_text, planned_subsections)
                    for part in part_candidates:
                        part_text = part["text"]
                        is_subheading = part.get("source_level") == "SUBHEADING"
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
        from app.config import settings

        budget = getattr(settings, "max_context_chars", 12000)
        topic_block = json.dumps(topic, ensure_ascii=False)
        fact_block = "\n".join(f"{f['id']}. {f.get('content', '')}" for f in support_facts)
        missing = "、".join(qa_item.get("missing_aspects") or []) or "按 Topic 计划完善表达"
        prompt = (
            f"章节:{chapter_title}(局部重写第 {target} 段,保持与前后段衔接)\n"
            f"Narrative Topic 计划:{_fit_block(topic_block, budget)}\n"
            f"待改进方面:{missing}\n"
            f"原段落:{_fit_block(original, budget)}\n"
            f"支撑事实:\n{_fit_block(fact_block, budget)}\n"
            "请重写该段落:解决待改进方面,用支撑事实展开,不要罗列事实编号,"
            "不要重复段落外的内容。输出 JSON,优先只包含 sentences 字段"
            "(每项 paragraph=1,含 text/fact_ids/inference_ids)。"
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
    def _memory_block(report_memory: dict, dependencies: list[str] | None = None) -> str:
        """Report Memory 摘要(依赖注入:只注入当前章 dependencies 对应章节的记忆)。"""
        lines = []
        deps = dependencies or []
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
        # 章节摘要:只注入当前章 dependencies 命中的前文章节
        summaries = report_memory.get("chapter_summaries", [])
        dep_chapters = {d for d in deps}
        relevant = [s for s in summaries if s.get("chapter") in dep_chapters]
        if deps and not relevant:
            # dependencies 里列了但还没写(写序错乱兜底)——退化为最近一章
            relevant = summaries[-1:]
        if relevant:
            lines.append("依赖章节记忆(本段承接其结论,不要重复其内容):")
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


def _attribution_block(attribution_plan: dict | None) -> str:
    """Attribution Plan → prompt 块:观点 + 支撑证据(程序只格式,不判断)。"""
    if not attribution_plan:
        return ""
    points = attribution_plan.get("points") or []
    if not points:
        return ""
    lines = []
    for idx, p in enumerate(points, start=1):
        lines.append(
            f"{idx}. 观点: {p.get('statement', '')}\n"
            f"   支撑事实: {p.get('supporting_fact_ids', [])} 支撑推断: {p.get('supporting_inference_ids', [])}"
            f" 关系: {p.get('relation', 'direct')}"
        )
    return "\n".join(lines)

