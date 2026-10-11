"""报告质量检查:生成后自动检查,结果供人工审核参考。

规则检查(本地零成本):重复内容(句间相似)、模板章节缺失/多余、标题正文错配、术语违规、
机构规则(必须/禁止/字数)、数字与事实一致性;
LLM 检查:逻辑跳跃、引用与内容一致性。结果统一为 qa_notes 列表。

自修分级由 workflow 控制:
确定性问题自动修;低风险问题可逆修;语义性问题仅提示人工/模型复核。
"""

import hashlib
import json
import logging
import re
from decimal import Decimal, InvalidOperation

from app.agents.base import BaseAgent
from app.assertion_semantics import normalize_semantic_profile, semantic_profile_line
from app.context_budget import ContextSection, build_prompt_from_sections
from app.db import session_scope
from app.runtime_profiles import stage_input_budget_tokens

logger = logging.getLogger(__name__)

_QA_SYSTEM = """你是报告质量检查员。按稳定句子编号核验逻辑、引用和明显重复。只输出 JSON：
{"issues": [{
  "type": "LOGIC_GAP|CITATION_MISMATCH|REDUNDANT",
  "target_type": "sentence|paragraph|section|report",
  "sentence_ids": [123],
  "section": "章节",
  "paragraph": 1,
  "quote": "必须来自被指向正文的原文片段",
  "support_status": "PARTIALLY_SUPPORTED|UNSUPPORTED|CONTRADICTED|INSUFFICIENT_EVIDENCE",
  "note": "问题说明"
}]}
核验步骤:
1. 先将句子的 FACT_IDS/INFERENCE_IDS 与 DIRECT_SENTENCES/INFERENCE_BASIS_SENTENCES/SENTENCES 映射核对；对每条推论沿 BASED_FACT_IDS 查看原始事实及来源语义。全部已绑定依据应联合判断复合句中的每个独立主张，允许语义等价改述。
2. 对有引用的句子逐项核对每个独立主张，指出仍缺支持的最小分句及原因；对无引用而包含事实、数量、因果、效果或评价的句子同样核验。特别区分来源明示的行动与作者补入的动机、市场需求、目的或效果：仅有行动、规模和时间的依据不能单独证明这些解释。纯组织性过渡可无引用。来源观点、计划、目标、假设和预测只支持相应范围的陈述；已实现或已验证的独立结论需要相应来源证据。局部缺失说明只适用于已检查范围。
3. 引用问题的 support_status 采用 PARTIALLY_SUPPORTED（部分主张有依据）、UNSUPPORTED（现有绑定依据不支持）、CONTRADICTED（明确反证）或 INSUFFICIENT_EVIDENCE（尚不足以判断）。逻辑跳跃与重复问题可省略该字段；普通写作偏好不作为质量问题。
4. 句子与段落问题使用输入中的真实 sentence_ids；章节或全文问题可为空。quote 逐字截取有问题的最小正文片段。输出前逐条复核：只有 note 指出的主张确实缺支持时才保留 issue，support_status 应与最终 note 一致。无问题时返回 {"issues": []}。"""


class _QualityAgent(BaseAgent):
    name = "qa"
    role = _QA_SYSTEM
    temperature = 0.0


def _source_ids_for_qa(rows: list[dict]) -> tuple[set[int], set[int]]:
    fact_ids: set[int] = set()
    inference_ids: set[int] = set()
    for row in rows or []:
        try:
            refs = json.loads(row.get("source_refs") or "{}")
        except (TypeError, ValueError):
            refs = {}
        if not isinstance(refs, dict):
            continue
        for key, target in (("fact_ids", fact_ids), ("inference_ids", inference_ids)):
            for value in refs.get(key) or []:
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    continue
                if parsed > 0:
                    target.add(parsed)
    return fact_ids, inference_ids


def _build_qa_context(rows: list[dict], *, fact_by_id: dict[int, str],
                      inference_by_id: dict[int, dict],
                      evidence_by_fact: dict[int, list[dict]],
                      fact_semantics_by_id: dict[int, dict] | None = None) -> dict[str, list[str]]:
    """Build compact QA records only from evidence linked to report sentences."""
    fact_semantics_by_id = fact_semantics_by_id or {}
    direct_fact_sentences: dict[int, list[int]] = {}
    inference_basis_sentences: dict[int, list[int]] = {}
    inference_sentences: dict[int, list[int]] = {}
    sentence_lines = []
    for row in rows or []:
        try:
            refs = json.loads(row.get("source_refs") or "{}")
        except (TypeError, ValueError):
            refs = {}
        if not isinstance(refs, dict):
            refs = {}
        fact_ids = sorted({int(value) for value in refs.get("fact_ids") or [] if str(value).isdigit()})
        inference_ids = sorted({int(value) for value in refs.get("inference_ids") or [] if str(value).isdigit()})
        sentence_id = int(row.get("id") or 0)
        for fact_id in fact_ids:
            direct_fact_sentences.setdefault(fact_id, []).append(sentence_id)
        for inference_id in inference_ids:
            inference_sentences.setdefault(inference_id, []).append(sentence_id)
            inference = inference_by_id.get(inference_id) or {}
            for fact_id in inference.get("based_fact_ids") or []:
                inference_basis_sentences.setdefault(int(fact_id), []).append(sentence_id)
        sentence_lines.append(
            f"[SECTION:{row.get('section', '')}][PARAGRAPH:{int(row.get('paragraph') or 0)}]"
            f"[SENTENCE:{sentence_id}][SOURCE:{row.get('source_level', '')}]"
            f"[FACT_IDS:{','.join(map(str, fact_ids))}][INFERENCE_IDS:{','.join(map(str, inference_ids))}] "
            f"{row.get('content', '')}"
        )

    linked_fact_ids = set(direct_fact_sentences) | set(inference_basis_sentences)
    fact_lines = []
    for fact_id in sorted(linked_fact_ids):
        if fact_id not in fact_by_id:
            continue
        direct_ids = sorted(set(direct_fact_sentences.get(fact_id, [])))
        inference_ids = sorted(set(inference_basis_sentences.get(fact_id, [])))
        fact_semantics = fact_semantics_by_id.get(fact_id) or {}
        profile = fact_semantics.get("semantic_profile", fact_semantics)
        source_profile = semantic_profile_line(
            profile,
            fact_semantics.get("assertion_mode"),
            fact_semantics.get("assertion_mode_raw"),
        )
        fact_lines.append(
            f"[FACT:{fact_id}][DIRECT_SENTENCES:{','.join(map(str, direct_ids))}]"
            f"[INFERENCE_BASIS_SENTENCES:{','.join(map(str, inference_ids))}] "
            f"{fact_by_id[fact_id]} "
            f"[SOURCE_SEMANTICS:{source_profile}]"
        )
    inference_lines = []
    for inference_id, sentence_ids in sorted(inference_sentences.items()):
        inference = inference_by_id.get(inference_id)
        if not inference:
            continue
        based_fact_ids = inference.get("based_fact_ids") or []
        inference_lines.append(
            f"[INFERENCE:{inference_id}][SOURCE_LEVEL:{inference.get('source_level', '')}]"
            f"[BASED_FACT_IDS:{','.join(map(str, based_fact_ids))}]"
            f"[SENTENCES:{','.join(map(str, sorted(set(sentence_ids))))}] "
            f"{inference.get('content', '')} "
            f"[INFERENCE_SEMANTICS:{semantic_profile_line(inference.get('semantic_profile'))}]"
        )

    evidence_lines = []
    seen_evidence = set()
    for fact_id in sorted(linked_fact_ids):
        sentence_ids = sorted(set(direct_fact_sentences.get(fact_id, []))
                              | set(inference_basis_sentences.get(fact_id, [])))
        for evidence in evidence_by_fact.get(fact_id, []):
            source_file = str(evidence.get("source_file") or "来源未记录")
            locator = []
            if evidence.get("page") is not None:
                locator.append(f"第{evidence['page']}页")
            if evidence.get("paragraph") is not None:
                locator.append(f"第{evidence['paragraph']}段")
            quote = str(evidence.get("quote") or "").strip()
            key = (fact_id, source_file, tuple(locator), quote)
            if not quote or key in seen_evidence:
                continue
            seen_evidence.add(key)
            location = " · ".join([source_file, *locator])
            evidence_lines.append(
                f"[EVIDENCE_FOR_FACT:{fact_id}][SENTENCES:{','.join(map(str, sorted(set(sentence_ids))))}] "
                f"{location} | {quote}"
            )
    return {
        "sentence_lines": sentence_lines,
        "fact_lines": fact_lines,
        "inference_lines": inference_lines,
        "evidence_lines": evidence_lines,
    }

_VAGUE_TERMS = (
    "相关文件", "有关文件", "有关要求", "相关材料", "规定时间", "指定时间",
    "一定数量", "相关部门", "有关人员", "按要求", "及时提交", "适时完成",
)
_SPECIFIC_PATTERN = re.compile(
    r"(\d{1,2}月\d{1,2}日|\d{4}年(?:\d{1,2}月)?|《[^》]{2,}》|"
    r"\d+(?:\.\d+)?%|\d+(?:\.\d+)?(?:万|亿|项|个|份|页|小时|分钟))"
)
_BULLET_PREFIX_PATTERN = re.compile(r"^[•\-*]\s*")
_STRUCTURED_ITEM_PATTERN = re.compile(
    r"^(?:[•\-*]|[一二三四五六七八九十]+[、.]|\d+[、.])\s*"
)


def _cosine_similarity(a: str, b: str) -> float:
    """极简重叠相似度:字符二元组 Jaccard(轻量去重检测,不引 numpy 重计算)。"""
    grams_a = {a[i:i + 2] for i in range(len(a) - 1)}
    grams_b = {b[i:i + 2] for i in range(len(b) - 1)}
    if not grams_a or not grams_b:
        return 0.0
    return len(grams_a & grams_b) / len(grams_a | grams_b)


_NUMERIC_TOKEN_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?(?:[kK])?(?![A-Za-z])")


def _normalized_numeric_tokens(text: str) -> set[str]:
    """Normalize plain numbers and the common k-thousand suffix before QA comparison."""
    normalized = set()
    for match in _NUMERIC_TOKEN_RE.findall(str(text or "")):
        has_k_suffix = match[-1:].lower() == "k"
        numeric = match[:-1] if has_k_suffix else match
        try:
            value = Decimal(numeric) * (Decimal("1000") if has_k_suffix else Decimal("1"))
        except InvalidOperation:
            continue
        normalized.add(format(value.normalize(), "f"))
    return normalized


def run_quality_check(report_id: int, plan_structure: list[str],
                      forbidden_terms: list[str] | None = None,
                      institution_rules: dict | None = None,
                      qa_policy: dict | None = None) -> list[dict]:
    """对报告做质量检查,返回问题列表 [{type, section, quote, note}]。

    institution_rules:机构规则层(must_include 必须出现 / forbidden 禁止 /
    word_count 字数区间),业务约束而非语言风格。
    """
    qa_policy = qa_policy or {}
    allow_symbolic_lists = bool(qa_policy.get("allow_symbolic_lists"))
    from app.infrastructure.orm import ORMSentence
    from sqlalchemy import select
    with session_scope() as s:
        rows = s.execute(
            select(ORMSentence.c.id, ORMSentence.c.section, ORMSentence.c.paragraph,
                   ORMSentence.c.content, ORMSentence.c.source_level, ORMSentence.c.source_refs)
            .where(ORMSentence.c.report_id == report_id, ORMSentence.c.selected == 1)
            .order_by(ORMSentence.c.position)
        ).mappings().all()
    issues: list[dict] = []

    # 1. 重复内容:同段或近段内句子高相似
    for index, row in enumerate(rows):
        for other_index in range(index + 1, min(index + 6, len(rows))):
            if abs(row["paragraph"] - rows[other_index]["paragraph"]) > 2:
                continue
            if _cosine_similarity(row["content"], rows[other_index]["content"]) > 0.7:
                issues.append({
                    "type": "REDUNDANT", "section": row["section"],
                    "quote": row["content"][:60],
                    "note": f"与附近句子内容高度重复(相似度 {_cosine_similarity(row['content'], rows[other_index]['content']):.2f})",
                })
                break

    # 2. 模板结构:plan 章节缺失
    present_sections = []
    for row in rows:
        if row["section"] and (not present_sections or present_sections[-1] != row["section"]):
            present_sections.append(row["section"])
    for section in plan_structure:
        if section not in present_sections:
            issues.append({
                "type": "MISSING_SECTION", "section": section,
                "quote": "", "note": "规划章节未生成内容",
            })

    # 3. 术语:违反模板禁止表达
    for word in (forbidden_terms or []):
        if not word:
            continue
        for row in rows:
            if word in row["content"]:
                issues.append({
                    "type": "TERM_VIOLATION", "section": row["section"],
                    "quote": row["content"][:60], "note": f"出现模板禁用表达「{word}」",
                })

    # 3.5 机构规则:必须出现的内容缺失 / 禁止内容出现 / 字数区间
    rules = institution_rules or {}
    full_text = "\n".join(row["content"] for row in rows)
    for required in rules.get("must_include", []):
        if required and required not in full_text:
            issues.append({
                "type": "RULE_VIOLATION", "section": "",
                "quote": "", "note": f"机构规则要求包含「{required}」但缺失",
            })
    for banned in rules.get("forbidden", []):
        if banned and banned in full_text:
            issues.append({
                "type": "RULE_VIOLATION", "section": "",
                "quote": full_text[full_text.find(banned):full_text.find(banned) + 60],
                "note": f"机构规则禁止出现「{banned}」",
            })
    word_count = rules.get("word_count")
    if word_count and len(full_text) > 0:
        import re as _re
        numbers = _re.findall(r"\d+", str(word_count))
        if len(numbers) >= 2:
            low, high = int(numbers[0]), int(numbers[1])
            if not (low <= len(full_text) <= high):
                issues.append({
                    "type": "RULE_VIOLATION", "section": "",
                    "quote": "", "note": f"字数 {len(full_text)} 超出机构要求区间 {low}-{high}",
                })

    # 4. 数字一致性:句子中的数字应在其引用事实(含原文片段)中出现(防模型改述出错)
    # Do not treat architecture/product tokens such as x86 or SGXv1 as
    # standalone quantitative claims.
    from app.infrastructure.orm import ORMFact, ORMEvidence, ORMInference
    linked_fact_ids, linked_inference_ids = _source_ids_for_qa(rows)
    with session_scope() as s:
        inference_rows = (
            s.execute(
                select(ORMInference.c.id, ORMInference.c.content, ORMInference.c.source_level,
                       ORMInference.c.based_fact_ids, ORMInference.c.confidence_level,
                       ORMInference.c.semantic_profile_json)
                .where(ORMInference.c.id.in_(linked_inference_ids))
            ).mappings().all()
            if linked_inference_ids else []
        )
        inference_by_id = {}
        for row in inference_rows:
            try:
                based_fact_ids = json.loads(row["based_fact_ids"] or "[]")
            except (TypeError, ValueError):
                based_fact_ids = []
            inference_by_id[int(row["id"])] = {
                "content": row["content"],
                "source_level": row["source_level"],
                "based_fact_ids": [int(value) for value in based_fact_ids if str(value).isdigit()],
                "confidence_level": row["confidence_level"],
                "semantic_profile": normalize_semantic_profile(row.get("semantic_profile_json")),
            }
        linked_fact_ids.update({
            fact_id for inference in inference_by_id.values()
            for fact_id in inference.get("based_fact_ids") or []
        })
        fact_rows = (
            s.execute(select(
                ORMFact.c.id, ORMFact.c.content, ORMFact.c.semantic_profile_json,
                ORMFact.c.assertion_mode, ORMFact.c.assertion_mode_raw,
            ).where(ORMFact.c.id.in_(linked_fact_ids))).mappings().all()
            if linked_fact_ids else []
        )
        ev_rows = (
            s.execute(
                select(ORMEvidence.c.fact_id, ORMEvidence.c.source_file, ORMEvidence.c.page,
                       ORMEvidence.c.paragraph, ORMEvidence.c.quote)
                .where(ORMEvidence.c.fact_id.in_(linked_fact_ids))
                .order_by(ORMEvidence.c.fact_id, ORMEvidence.c.id)
            ).mappings().all()
            if linked_fact_ids else []
        )
    fact_by_id = {r["id"]: r["content"] for r in fact_rows}
    fact_semantics_by_id = {
        int(row["id"]): {
            "semantic_profile": normalize_semantic_profile(row.get("semantic_profile_json")),
            "assertion_mode": row.get("assertion_mode"),
            "assertion_mode_raw": row.get("assertion_mode_raw"),
        }
        for row in fact_rows
    }
    quote_by_fact: dict[int, list[str]] = {}
    evidence_by_fact: dict[int, list[dict]] = {}
    for ev in ev_rows:
        quote_by_fact.setdefault(ev["fact_id"], []).append(ev["quote"])
        evidence_by_fact.setdefault(ev["fact_id"], []).append(dict(ev))
    for row in rows:
        try:
            refs = json.loads(row["source_refs"] or "{}")
        except (TypeError, ValueError):
            continue
        fact_ids = refs.get("fact_ids") or []
        if not fact_ids:
            continue
        sentence_digits = _normalized_numeric_tokens(row["content"])
        if not sentence_digits:
            continue
        fact_digits: set[str] = set()
        for fid in fact_ids:
            fact_digits |= _normalized_numeric_tokens(fact_by_id.get(int(fid), ""))
            for quote in quote_by_fact.get(int(fid), []):
                fact_digits |= _normalized_numeric_tokens(quote)
        missing = {d for d in sentence_digits - fact_digits if len(d) >= 2}
        if missing:
            issues.append({
                "type": "CITATION_MISMATCH", "section": row["section"],
                "quote": row["content"][:60],
                "note": f"句子数字 {sorted(missing)} 未在引用事实中出现,请核对",
            })

    # 4.5 具体化检查:引用事实/证据已有明确日期、文件名、材料名时,正文不应泛化。
    for row in rows:
        vague = [term for term in _VAGUE_TERMS if term in row["content"]]
        if not vague:
            continue
        try:
            refs = json.loads(row["source_refs"] or "{}")
        except (TypeError, ValueError):
            refs = {}
        fact_ids = [int(fid) for fid in refs.get("fact_ids") or [] if str(fid).isdigit()]
        evidence_text = []
        for fid in fact_ids:
            evidence_text.append(fact_by_id.get(fid, ""))
            evidence_text.extend(quote_by_fact.get(fid, []))
        concrete_hits = []
        for text in evidence_text:
            concrete_hits.extend(_SPECIFIC_PATTERN.findall(text or ""))
        if concrete_hits:
            issues.append({
                "type": "CONCRETENESS_ISSUE", "section": row["section"],
                "quote": row["content"][:60],
                "note": f"句中出现模糊表达 {vague[:3]}, 但引用证据包含具体信息 {concrete_hits[:5]}, 建议改写为明确时间、文件名或材料名",
            })

    # 4.6 结构化条目溯源粒度:条目必须有自己的 fact/inference 绑定,不能共用整段模糊引用。
    for row in rows:
        content = row["content"] or ""
        if _BULLET_PREFIX_PATTERN.match(content) and not allow_symbolic_lists:
            issues.append({
                "type": "FORMAT_STYLE_ISSUE", "section": row["section"],
                "quote": content[:60],
                "note": "当前模板未允许项目符号式前缀。请改为符合本报告体例的自然段、编号项或表格表达",
            })
        if not _STRUCTURED_ITEM_PATTERN.match(content):
            continue
        try:
            refs = json.loads(row["source_refs"] or "{}")
        except (TypeError, ValueError):
            refs = {}
        fact_ids = refs.get("fact_ids") or []
        inference_ids = refs.get("inference_ids") or []
        if refs.get("trace_granularity_warning"):
            issues.append({
                "type": "TRACE_GRANULARITY_ISSUE", "section": row["section"],
                "quote": row["content"][:60],
                "note": "该清单条目由多条清单文本兜底拆分而来,可能共用同一组引用;建议让每个条目单独绑定事实或推断",
            })
        elif len(fact_ids) + len(inference_ids) > 4:
            issues.append({
                "type": "TRACE_GRANULARITY_ISSUE", "section": row["section"],
                "quote": row["content"][:60],
                "note": "该清单条目绑定的依据过多,可能不是条目级精确溯源;建议拆分或收窄 fact_ids/inference_ids",
            })

    # 5. 信息密度:无事实/推断依据的句子占比过高 → 内容空洞(规则,零成本)
    from app.infrastructure.orm import ORMSentence
    from sqlalchemy import select
    with session_scope() as s:
        all_rows = s.execute(
            select(ORMSentence.c.source_refs)
            .where(ORMSentence.c.report_id == report_id, ORMSentence.c.selected == 1)
        ).mappings().all()
    referenced = 0
    for r in all_rows:
        try:
            refs = json.loads(r["source_refs"] or "{}")
        except (TypeError, ValueError):
            refs = {}
        if refs.get("fact_ids") or refs.get("inference_ids"):
            referenced += 1
    density = referenced / max(len(all_rows), 1)
    if len(all_rows) >= 4 and density < 0.6:
        issues.append({
            "type": "DENSITY_ISSUE", "section": "", "quote": "",
            "note": f"信息密度低:仅 {density:.0%} 的句子有事实/推断依据,建议压缩或合并",
        })

    # 6. 逻辑与引用一致性:LLM 检查
    if rows:
        try:
            qa_context = _build_qa_context(
                rows, fact_by_id=fact_by_id, inference_by_id=inference_by_id,
                evidence_by_fact=evidence_by_fact,
                fact_semantics_by_id=fact_semantics_by_id,
            )
            sentence_lines = qa_context["sentence_lines"]
            valid_sentence_ids = {int(row["id"]) for row in rows}
            sentence_by_id = {int(row["id"]): str(row.get("content") or "") for row in rows}
            prompt, _audit = build_prompt_from_sections("qa", [
                ContextSection("instruction", [
                    "检查报告正文和其绑定的材料事实、推断及原文依据。问题必须引用真实句子编号。",
                    "仅按实际展示的引用依据核验；某条证据未进入上下文时，不得据此断言它不存在或引用必错。",
                ], weight=6, required_items=2),
                ContextSection("report_sentences", sentence_lines, weight=5, required_items=1),
                ContextSection("cited_facts", qa_context["fact_lines"], weight=2, required_items=1),
                ContextSection("cited_inferences", qa_context["inference_lines"], weight=2, required_items=1),
                ContextSection("source_evidence", qa_context["evidence_lines"], weight=3, required_items=1),
            ], stage_input_budget_tokens("qa"))
            payload = _QualityAgent().generate_json(prompt)
            for item in payload.get("issues", []):
                sentence_ids = []
                for value in item.get("sentence_ids") or []:
                    try:
                        sentence_id = int(value)
                    except (TypeError, ValueError):
                        continue
                    if sentence_id in valid_sentence_ids:
                        sentence_ids.append(sentence_id)
                sentence_ids = list(dict.fromkeys(sentence_ids))
                quote = str(item.get("quote", ""))
                if quote and sentence_ids and not any(
                    quote in sentence_by_id.get(sentence_id, "") for sentence_id in sentence_ids
                ):
                    quote = ""
                target_type = str(item.get("target_type") or "").strip().lower()
                if target_type not in {"sentence", "paragraph", "section", "report"}:
                    target_type = "sentence" if len(sentence_ids) == 1 else "paragraph" if sentence_ids else "section" if item.get("section") else "report"
                support_status = str(item.get("support_status") or "").strip().upper()
                if support_status not in {
                    "PARTIALLY_SUPPORTED", "UNSUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE",
                }:
                    support_status = ""
                issues.append({
                    "type": str(item.get("type", "LOGIC_GAP")),
                    "target_type": target_type,
                    "sentence_ids": sentence_ids,
                    "sentence_id": sentence_ids[0] if len(sentence_ids) == 1 else None,
                    "section": str(item.get("section", "")),
                    "paragraph": _safe_int(item.get("paragraph"), default=None),
                    "quote": quote[:160],
                    "support_status": support_status,
                    "note": str(item.get("note", "")),
                    "location_source": "model_stable_id" if sentence_ids else "model_scope",
                })
        except Exception:
            logger.exception("LLM report quality check failed for report %s", report_id)
            issues.append({
                "type": "QA_CHECK_INCOMPLETE",
                "target_type": "report",
                "sentence_ids": [],
                "section": "",
                "quote": "",
                "note": "语义与引用核验未完成；本地规则检查结果仍保留，不能据此认为正文没有问题。",
                "location_source": "system_status",
            })

    # Bind each issue to a stable report location. Rule and LLM checks use a
    # human-readable quote, but the UI must not have to guess where it belongs.
    issues = attach_quality_issue_locations(report_id, issues, rows=rows)

    # 去重
    seen = set()
    result = []
    for issue in issues:
        key = (issue["type"], issue["section"], issue["quote"], issue["note"])
        if key not in seen:
            seen.add(key)
            result.append(_classify_issue(issue))
    return result


def attach_quality_issue_locations(report_id: int, issues: list[dict], *, rows=None,
                                   report_version_id: int | None = None) -> list[dict]:
    """Attach sentence/section anchors without changing QA semantics.

    New QA runs receive stable sentence ids. Historical QA records are resolved
    on read from their section and quote, so existing tasks remain navigable.
    """
    if rows is None:
        from app.infrastructure.orm import ORMSentence
        from sqlalchemy import select
        with session_scope() as s:
            rows = s.execute(
                select(ORMSentence.c.id, ORMSentence.c.section, ORMSentence.c.paragraph,
                       ORMSentence.c.content, ORMSentence.c.user_edit)
                .where(ORMSentence.c.report_id == int(report_id), ORMSentence.c.selected == 1)
                .order_by(ORMSentence.c.position)
            ).mappings().all()
    candidates = [
        dict(row) for row in rows
        if row.get("selected", 1) not in {0, False}
    ]
    by_id = {int(row["id"]): row for row in candidates}

    if report_version_id is None:
        try:
            from app.infrastructure.orm import ORMReportVersion
            from sqlalchemy import select
            with session_scope() as s:
                version = s.execute(
                    select(ORMReportVersion.c.id)
                    .where(ORMReportVersion.c.report_id == int(report_id))
                    .order_by(ORMReportVersion.c.version_no.desc())
                    .limit(1)
                ).first()
            report_version_id = int(version[0]) if version else None
        except Exception:
            report_version_id = None

    result = []
    for raw in issues or []:
        issue = dict(raw or {})
        original_quote = str(issue.get("quote") or "")
        valid_ids = []
        for value in list(issue.get("sentence_ids") or []) + [issue.get("sentence_id")]:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed in by_id and parsed not in valid_ids:
                valid_ids.append(parsed)
        if not valid_ids and original_quote:
            section = str(issue.get("section") or "").strip()
            paragraph = _safe_int(issue.get("paragraph"), default=None)
            matches = []
            for row in candidates:
                if section and str(row.get("section") or "").strip() != section:
                    continue
                if paragraph is not None and int(row.get("paragraph") or 0) != paragraph:
                    continue
                current_text = str(row.get("user_edit") or row.get("content") or "")
                if original_quote in current_text:
                    matches.append(row)
            if len(matches) == 1:
                valid_ids = [int(matches[0]["id"])]
                issue["location_source"] = "quote_exact"
        if valid_ids:
            issue["sentence_ids"] = valid_ids
            if len(valid_ids) == 1:
                issue["sentence_id"] = valid_ids[0]
            issue.setdefault("target_type", "sentence" if len(valid_ids) == 1 else "paragraph")
            issue.setdefault("location_confidence", "high")
            issue.setdefault("location_source", "stable_id")
        section = str(issue.get("section") or "").strip()
        if not valid_ids and (
            not issue.get("target_type") or issue.get("target_type") in {"sentence", "paragraph"}
        ):
            issue["target_type"] = "section" if section else "report"
            issue["location_confidence"] = "section" if section else "report"
            issue.setdefault("location_source", "scope_only")
        if not valid_ids:
            issue.setdefault("sentence_ids", [])

        anchor_rows = [by_id[value] for value in issue.get("sentence_ids") or [] if value in by_id]
        anchor_text = "\n".join(str(row.get("user_edit") or row.get("content") or "") for row in anchor_rows)
        current_fingerprint = hashlib.sha256(anchor_text.encode("utf-8")).hexdigest() if anchor_text else ""
        previous_fingerprint = str(issue.get("anchor_fingerprint") or "")
        if previous_fingerprint and current_fingerprint and previous_fingerprint != current_fingerprint:
            if str(issue.get("status") or "open") not in {"resolved", "ignored"}:
                issue["status"] = "stale"
            issue["location_confidence"] = "stale"
        elif current_fingerprint:
            issue.setdefault("anchor_fingerprint", current_fingerprint)

        if len(anchor_rows) == 1 and original_quote:
            current_text = str(anchor_rows[0].get("user_edit") or anchor_rows[0].get("content") or "")
            offset = current_text.find(original_quote)
            if offset >= 0:
                issue["quote_start"] = offset
                issue["quote_end"] = offset + len(original_quote)

        issue.setdefault("status", "open")
        issue.setdefault("report_version_id", report_version_id)
        issue.setdefault("issue_id", _quality_issue_id(report_id, issue))
        result.append(issue)
    return result


def _quality_issue_id(report_id: int, issue: dict) -> str:
    """Build a stable public identifier without exposing internal row ids."""
    parts = [
        str(report_id), str(issue.get("type") or issue.get("issue_type") or ""),
        str(issue.get("target_type") or ""),
        ",".join(str(value) for value in issue.get("sentence_ids") or []),
        str(issue.get("section") or ""), str(issue.get("paragraph") or ""),
        str(issue.get("quote") or ""), str(issue.get("note") or ""),
    ]
    return "qa_" + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _safe_int(value, default: int | None = 0) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _classify_issue(issue: dict) -> dict:
    """Attach repair routing; QA only classifies, workflow decides repair."""
    item = dict(issue)
    type_ = str(item.get("type", ""))
    if type_ in {"FORMAT_STYLE_ISSUE", "TRACE_GRANULARITY_ISSUE"}:
        item["severity"] = "low"
        item["repair_route"] = "program_deterministic"
    elif type_ in {"REDUNDANT", "CONCRETENESS_ISSUE", "DENSITY_ISSUE", "LOGIC_GAP"}:
        item["severity"] = "medium"
        item["repair_route"] = "writer_targeted_rewrite"
    elif type_ in {"MISSING_SECTION", "SECTION_ALIGNMENT_REVIEW", "CITATION_MISMATCH", "BUSINESS_MISMATCH"}:
        item["severity"] = "high"
        item["repair_route"] = "narrative_or_planner_rebuild"
    elif type_ == "QA_CHECK_INCOMPLETE":
        item["severity"] = "medium"
        item["repair_route"] = "manual_review"
    elif type_ in {"TERM_VIOLATION", "RULE_VIOLATION"}:
        item["severity"] = "high"
        item["repair_route"] = "manual_review"
    else:
        item.setdefault("severity", "medium")
        item.setdefault("repair_route", "manual_review")
    return item
