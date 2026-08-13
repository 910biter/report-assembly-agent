"""报告质量检查:生成后自动检查,结果供人工审核参考。

规则检查(本地零成本):重复内容(句间相似)、模板章节缺失/多余、标题正文错配、术语违规、
机构规则(必须/禁止/字数)、数字与事实一致性;
LLM 检查:逻辑跳跃、引用与内容一致性。结果统一为 qa_notes 列表。

自修分级由 workflow 控制:
确定性问题自动修;低风险问题可逆修;语义性问题仅提示人工/模型复核。
"""

import json
import re

from app.db import connect
from app.gateway import model_gateway

_QA_SYSTEM = """你是报告质量检查员。检查报告是否存在以下问题,严格输出 JSON:
{"issues": [{"type": "LOGIC_GAP|CITATION_MISMATCH|REDUNDANT", "section": "章节", "quote": "问题句片段", "note": "问题说明"}]}
没有问题时输出 {"issues": []}"""

_VAGUE_TERMS = (
    "相关文件", "有关文件", "有关要求", "相关材料", "规定时间", "指定时间",
    "一定数量", "相关部门", "有关人员", "按要求", "及时提交", "适时完成",
)
_SPECIFIC_PATTERN = re.compile(
    r"(\d{1,2}月\d{1,2}日|\d{4}年|《[^》]{2,}》|附件\d+|登记表|通讯稿|影像资料|视频|调研报告|访谈记录|系统|平台|渠道|截止|前提交)"
)
_BULLET_PREFIX_PATTERN = re.compile(r"^[•\-*]\s*")
_STRUCTURED_ITEM_PATTERN = re.compile(
    r"^(?:[•\-*]|[一二三四五六七八九十]+[、.]|\d+[、.]|"
    r"必交材料方面[，,]|选交材料方面[，,]|时间节点方面[，,]|操作流程方面[，,]|评审条件方面[，,]|提交要求方面[，,])\s*"
)


def _cosine_similarity(a: str, b: str) -> float:
    """极简重叠相似度:字符二元组 Jaccard(轻量去重检测,不引 numpy 重计算)。"""
    grams_a = {a[i:i + 2] for i in range(len(a) - 1)}
    grams_b = {b[i:i + 2] for i in range(len(b) - 1)}
    if not grams_a or not grams_b:
        return 0.0
    return len(grams_a & grams_b) / len(grams_a | grams_b)


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
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, section, paragraph, content, source_level, source_refs "
            "FROM report_sentences WHERE report_id=? AND selected=1 ORDER BY position",
            (report_id,),
        ).fetchall()
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

    # 2.5 标题正文错配:只提示,不自动改标题或正文。
    section_texts: dict[str, str] = {}
    for row in rows:
        section_texts[row["section"]] = section_texts.get(row["section"], "") + row["content"]
    for section, text in section_texts.items():
        if any(term in section for term in ("成效", "成果", "经验总结")) and not any(
            term in text for term in ("取得", "形成", "完成", "成效", "成果", "经验")
        ):
            issues.append({
                "type": "SECTION_ALIGNMENT_REVIEW",
                "section": section,
                "quote": text[:60],
                "note": "章节标题指向成效/经验,但正文更像规范要求或执行事项。请复核:是正文写偏,还是标题需要调整。",
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
    _digit_re = re.compile(r"\d+(?:\.\d+)?")
    with connect() as conn:
        fact_rows = conn.execute("SELECT id, content FROM facts").fetchall()
        ev_rows = conn.execute("SELECT fact_id, quote FROM evidence").fetchall()
    fact_by_id = {r["id"]: r["content"] for r in fact_rows}
    quote_by_fact: dict[int, list[str]] = {}
    for ev in ev_rows:
        quote_by_fact.setdefault(ev["fact_id"], []).append(ev["quote"])
    for row in rows:
        try:
            refs = json.loads(row["source_refs"] or "{}")
        except (TypeError, ValueError):
            continue
        fact_ids = refs.get("fact_ids") or []
        if not fact_ids:
            continue
        sentence_digits = set(_digit_re.findall(row["content"]))
        if not sentence_digits:
            continue
        fact_digits: set[str] = set()
        for fid in fact_ids:
            fact_digits |= set(_digit_re.findall(fact_by_id.get(int(fid), "")))
            for quote in quote_by_fact.get(int(fid), []):
                fact_digits |= set(_digit_re.findall(quote))
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
                "note": "正式报告正文不应使用“•/-/*”等项目符号式前缀,建议改为“必交材料方面，……”等自然中文表达",
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
    with connect() as conn:
        all_rows = conn.execute(
            "SELECT source_refs FROM report_sentences WHERE report_id=? AND selected=1", (report_id,)
        ).fetchall()
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
            block = "\n".join(
                f"[{row['section']}] ({row['source_level']}) {row['content']}"
                for row in rows[:40]
            )
            payload = model_gateway.generate_json(
                f"报告句子清单:\n{block}", system=_QA_SYSTEM
            )
            for item in payload.get("issues", []):
                issues.append({
                    "type": str(item.get("type", "LOGIC_GAP")),
                    "section": str(item.get("section", "")),
                    "quote": str(item.get("quote", ""))[:60],
                    "note": str(item.get("note", "")),
                })
        except Exception:
            pass  # LLM 检查失败不阻塞;规则检查结果保留

    # 去重
    seen = set()
    result = []
    for issue in issues:
        key = (issue["type"], issue["section"], issue["quote"], issue["note"])
        if key not in seen:
            seen.add(key)
            result.append(_classify_issue(issue))
    return result


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
    elif type_ in {"TERM_VIOLATION", "RULE_VIOLATION"}:
        item["severity"] = "high"
        item["repair_route"] = "manual_review"
    else:
        item.setdefault("severity", "medium")
        item.setdefault("repair_route", "manual_review")
    return item
