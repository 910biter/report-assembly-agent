"""模板与写作风格学习:参考报告/模板 → Style Profile 草案 → 确认 → 锁定。

Style Profile 要素:structure_pattern / writing_style / terminology /
format_rule(文本描述)+ format_spec(硬排版格式,从 docx 模板真实提取)
+ style_samples(行文范例,供 Writer few-shot 模仿句式与用词)。
锁定后写入长期记忆,日常任务自动套用,不再回看参考报告原文。
"""
import json
import re
import shutil
from pathlib import Path

from app.config import settings
from app.db import connect
from app.gateway import model_gateway
from app.llm_scheduler import invoke
from app.models import StyleVariant
from app.template_engine import compile_template

_MAX_CHARS_PER_REPORT = 3000
_SAMPLE_LENGTH = 150

# ---------- 风格库(v2):逐份分析 → 聚类 → 变体 ----------

_HEADING_RE = re.compile(r"^(第?[一二三四五六七八九十百]+[章节部分]、|\([一二三四五六七八九十]+\)|\d+[.、])\s*\S+")

_FEATURE_PROMPT = """分析以下报告的体裁与风格特征,严格输出 JSON(不要任何解释):
{
  "topic_type": "报告类型,用 2-4 字简称,如:政策研究/情报快报/专题分析/风险研判/周报月报/工作总结/其他",
  "structure_notes": "章节组织特点(是否先结论后展开、典型章节顺序)",
  "language_notes": "语言特点(正式程度、句式、数据使用)"
}"""

_VARIANT_PROMPT = """以下为同一机构、同一类型({type})的 {count} 份报告(每份含章节结构与片段采样):

{reports}

请提炼该类型报告的写作规范,严格输出 JSON(不要任何解释):
{{
  "name": "报告类型名(沿用 {type})",
  "description": "一句话描述该类型报告的定位与用途",
  "structure": {{
    "sections": [{{"title": "一、…", "children": ["二级标题…"]}}],
    "summary_first": true或false,
    "conclusion_first": true或false
  }},
  "writing_style": {{
    "tone": "正式程度",
    "sentence_pattern": "句式习惯",
    "analysis_style": "先事实后判断等"
  }},
  "terminology": {{
    "preferred": ["惯用表达"],
    "forbidden": ["避免的表达"]
  }},
  "chapter_styles": [
    {{"chapter_type": "背景/过程/成效/研判/结论等章节类型", "purpose": "该章节写作目的", "rules": "写法规则", "examples": "1-2 句典型写法"}}
  ],
  "reasoning_profile": {{
    "analysis_framework": "本机构的分析展开方式,如:事实→影响→风险,或 背景→多方观点→趋势判断",
    "chapter_inputs": [{{"chapter_type": "风险研判", "inputs": ["事实", "趋势信息"], "outputs": ["综合影响判断", "风险等级"]}}],
    "risk_expression": "风险表达方式(如:结合外部环境分析,存在…风险)",
    "suggestion_style": "建议形成方式(如:面向中长期,建议…)"
  }},
  "institution_rules": {{
    "must_include": ["必须出现的内容"],
    "forbidden": ["禁止出现的内容"],
    "word_count": "字数要求或区间",
    "inference_ratio": "推断与事实的比例限制说明",
    "data_requirements": "数据引用要求(如:关键数字必须注明来源)"
  }}
}}"""


def analyze_library(reports: list[dict], force: bool = False) -> list[StyleVariant]:
    """从参考报告构建机构风格库(幂等:同一文件默认只产生一个稳定变体)。

    reports: [{"filename": str, "text": str, "path": str|None}]
    流程:逐份提取结构+多位置采样 → 按文件 hash 查已分析记录(有则复用,
    不重复生成)→ LLM 判定体裁 → 按体裁聚类 → 每组 LLM 提炼变体 → 落库。
    force=True 时忽略已有记录重新分析(显式"重新分析"才建新版本)。
    """
    if not reports:
        raise ValueError("NO_HISTORICAL_REPORTS")
    library_id = _ensure_library()

    # 幂等:同一文件(hash)已分析过 → 直接复用已有变体(LLM 分类不稳定不改变模板身份)
    if not force:
        reused = _reuse_by_source_hash(reports)
        if reused is not None:
            return reused

    features = []
    for report in reports:
        text = report.get("text", "")
        headings = _extract_headings(text, report.get("path"))
        samples = _sample_report(text)
        try:
            payload = invoke(
                "template", model_gateway.generate_json,
                f"报告文本:\n{text[:_MAX_CHARS_PER_REPORT]}",
                system=_FEATURE_PROMPT,
            )
            topic_type = str(payload.get("topic_type", "其他")).strip() or "其他"
        except Exception:
            topic_type = "其他"
        features.append({
            "filename": report.get("filename", ""),
            "text": text,
            "path": report.get("path"),
            "headings": headings,
            "samples": samples,
            "topic_type": topic_type,
        })
    # 聚类:按体裁分组;体裁不明的归入"其他"组按结构再拆
    groups: dict[str, list[dict]] = {}
    for feature in features:
        groups.setdefault(feature["topic_type"], []).append(feature)
    variants = []
    for topic_type, members in groups.items():
        variants.append(_build_variant(library_id, topic_type, members))
    _record_source_hash(reports, variants)
    return variants


def _source_hash(report: dict) -> str:
    """模板源文件内容 hash(稳定模板身份;无 path 时退回 filename)。"""
    import hashlib
    path = report.get("path")
    try:
        if path and Path(path).exists():
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except Exception:
        pass
    return hashlib.sha256((report.get("filename") or "").encode()).hexdigest()[:16]


def _reuse_by_source_hash(reports: list[dict]) -> list[StyleVariant] | None:
    """同一源文件已分析过 → 复用已有变体(返回 None 表示需新分析)。"""
    if len(reports) != 1:
        return None
    digest = _source_hash(reports[0])
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM style_variants WHERE source_hash=? ORDER BY id LIMIT 1",
            (digest,),
        ).fetchone()
    if row is None:
        return None
    variant = get_variant(row["id"])
    return [variant] if variant else None


def _record_source_hash(reports: list[dict], variants: list[StyleVariant]) -> None:
    """分析完成后记录源文件 hash 到变体(幂等身份)。"""
    if len(reports) != 1 or not variants:
        return
    digest = _source_hash(reports[0])
    with connect() as conn:
        conn.execute(
            "UPDATE style_variants SET source_hash=? WHERE id=?",
            (digest, variants[0].id),
        )


def _ensure_library() -> int:
    with connect() as conn:
        row = conn.execute("SELECT id FROM style_library ORDER BY id LIMIT 1").fetchone()
        if row:
            return row["id"]
        cur = conn.execute("INSERT INTO style_library(institution) VALUES('')")
        return cur.lastrowid


def _extract_headings(text: str, path=None) -> list[str]:
    """提取标题序列:docx 优先用标题样式,否则按编号正则。"""
    headings: list[str] = []
    if path and str(path).lower().endswith(".docx"):
        try:
            from docx import Document

            doc = Document(path)
            for para in doc.paragraphs:
                style = (para.style.name or "").lower()
                if para.text.strip() and (style.startswith("heading") or style.startswith("标题")):
                    headings.append(para.text.strip())
        except Exception:
            headings = []
    if not headings:
        for line in text.splitlines():
            line = line.strip()
            if _HEADING_RE.match(line) and len(line) <= 40:
                headings.append(line)
    return headings[:30]


def _sample_report(text: str) -> dict:
    """多位置采样:开头/正文/结尾。"""
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    n = len(paragraphs)
    return {
        "opening": "\n".join(paragraphs[:2])[:_SAMPLE_LENGTH * 2],
        "middle": "\n".join(paragraphs[n // 3: n // 3 + 2])[:_SAMPLE_LENGTH * 2] if n > 3 else "",
        "ending": "\n".join(paragraphs[-2:])[:_SAMPLE_LENGTH * 2] if n > 1 else "",
    }


def _build_variant(library_id: int, topic_type: str, members: list[dict]) -> StyleVariant:
    """提炼一个变体:LLM 结构/语言/术语 + 规则格式(dominant/alternatives)+ 分类型范例。"""
    report_blocks = []
    for member in members:
        headings = " / ".join(member["headings"][:6]) or "(未识别标题)"
        report_blocks.append(
            f"[{member['filename']}]\n章节结构: {headings}\n"
            f"开篇: {member['samples']['opening'][:150]}\n"
            f"正文: {member['samples']['middle'][:150]}\n"
            f"结尾: {member['samples']['ending'][:150]}"
        )
    try:
        payload = invoke(
            "template", model_gateway.generate_json,
            _VARIANT_PROMPT.replace("{type}", topic_type)
            .replace("{reports}", "\n\n---\n\n".join(report_blocks)),
            system="你是机构报告风格分析师。",
        )
    except Exception:
        payload = {}
    structure = payload.get("structure") if isinstance(payload.get("structure"), dict) else {}
    writing = payload.get("writing_style") if isinstance(payload.get("writing_style"), dict) else {}
    terminology = payload.get("terminology") if isinstance(payload.get("terminology"), dict) else {}
    chapter_styles = payload.get("chapter_styles") if isinstance(payload.get("chapter_styles"), list) else []
    reasoning = payload.get("reasoning_profile") if isinstance(payload.get("reasoning_profile"), dict) else {}
    institution_rules = payload.get("institution_rules") if isinstance(payload.get("institution_rules"), dict) else {}
    samples = _collect_samples(members)
    format_spec = _collect_format(members)
    variant = StyleVariant(
        library_id=library_id,
        name=str(payload.get("name") or topic_type),
        description=str(payload.get("description", "")),
        structure=structure,
        writing_style=writing,
        terminology=terminology,
        format_spec=format_spec,
        style_samples=samples,
        chapter_styles=chapter_styles,
        reasoning_profile=reasoning,
        institution_rules=institution_rules,
        source_reports=[m["filename"] for m in members],
        status="draft",
    )
    return save_variant(variant)


def _collect_samples(members: list[dict]) -> list[dict]:
    """分类型范例:开篇/事实描述/分析判断/结论收尾(每类 ≤3 条)。"""
    samples: list[dict] = []
    for member in members:
        samples.append({"sample_type": "opening", "content": member["samples"]["opening"]})
        if member["samples"]["middle"]:
            samples.append({"sample_type": "fact", "content": member["samples"]["middle"]})
        if member["samples"]["ending"]:
            samples.append({"sample_type": "conclusion", "content": member["samples"]["ending"]})
    seen: set[tuple] = set()
    result = []
    for sample in samples:
        key = (sample["sample_type"], sample["content"])
        if key in seen or not sample["content"]:
            continue
        seen.add(key)
        count = sum(1 for s in result if s["sample_type"] == sample["sample_type"])
        if count < 3:
            result.append(sample)
    return result


def _collect_format(members: list[dict]) -> dict:
    """Merge DOCX template layout tokens, retaining conflicts and alternatives."""
    specs: list[dict] = []
    for member in members:
        if member.get("path") and str(member["path"]).lower().endswith(".docx"):
            source_path = _persist_template_source(member["path"])
            spec = extract_docx_format(source_path)
            if spec:
                specs.append(spec)
    if not specs:
        return {}
    dominant, conflicts = _merge_format_specs(specs)
    field_confidence = _field_confidence(specs, dominant)
    alternatives = []
    seen = {_freeze_spec(dominant)}
    for spec in specs:
        frozen = _freeze_spec(spec)
        if frozen not in seen:
            seen.add(frozen)
            alternatives.append(spec)
    return {
        "dominant": dominant,
        "alternatives": alternatives[:3],
        "conflicts": conflicts,
        "field_confidence": field_confidence,
        "template_profile": _template_profile(dominant, conflicts, field_confidence),
        "selection_strategy": {
            "mode": "token_majority_vote",
            "principle": "按字段独立投票，优先选择出现频次最高且信息更完整的版式 token。",
            "tie_breakers": [
                "优先非空值",
                "优先覆盖字段更多的模板",
                "若票数相同则采用首个完整度更高的候选",
            ],
            "conflict_policy": [
                "页面、正文、标题等硬格式按字段多数决，不强行整份模板覆盖。",
                "封面、目录、页眉页脚等结构性差异保留冲突，导出时优先采用锁定变体的 dominant。",
                "同票冲突时优先选择字段更完整、来源模板更接近当前报告体裁的候选。",
            ],
        },
        "sample_count": len(specs),
    }


def _merge_format_specs(specs: list[dict]) -> tuple[dict, dict]:
    dominant: dict = {}
    conflicts: dict = {}
    keys = sorted({key for spec in specs for key in spec.keys()})
    for key in keys:
        values = [spec.get(key) for spec in specs if spec.get(key) not in (None, "", [], {})]
        if not values:
            continue
        counts: dict[str, int] = {}
        best_value = values[0]
        best_score = (0, 0)
        for value in values:
            frozen = _freeze_value(value)
            counts[frozen] = counts.get(frozen, 0) + 1
            score = (counts[frozen], _value_completeness(value))
            if score > best_score:
                best_value = value
                best_score = score
        dominant[key] = best_value
        unique_values = []
        for value in values:
            if all(_freeze_value(value) != _freeze_value(other) for other in unique_values):
                unique_values.append(value)
        if len(unique_values) > 1:
            conflicts[key] = unique_values[:4]
    return dominant, conflicts


def _field_confidence(specs: list[dict], dominant: dict) -> dict:
    confidence = {}
    for key, value in dominant.items():
        if value in (None, "", [], {}):
            continue
        same = sum(1 for spec in specs if _freeze_value(spec.get(key)) == _freeze_value(value))
        present = sum(1 for spec in specs if spec.get(key) not in (None, "", [], {}))
        confidence[key] = round(same / max(present, 1), 2)
    return confidence


def _template_profile(dominant: dict, conflicts: dict, confidence: dict) -> dict:
    learned_fields = sorted(k for k, v in dominant.items() if v not in (None, "", [], {}))
    critical = [
        "normal", "title", "heading1", "heading2", "margins_cm", "page_cm",
        "body_paragraph", "numbering_patterns", "table_style", "image_rules",
        "header_text", "footer_text",
    ]
    missing = [field for field in critical if field not in dominant]
    low_confidence = [field for field, score in confidence.items() if score < 0.67]
    return {
        "learned_fields": learned_fields,
        "missing_critical_fields": missing,
        "conflict_fields": sorted(conflicts.keys()),
        "low_confidence_fields": low_confidence,
        "completeness": round((len(critical) - len(missing)) / len(critical), 2),
        "recommendation": "可用于正式导出" if not missing and not low_confidence else "建议人工复核冲突或缺失的模板字段",
    }


def _value_completeness(value) -> int:
    if isinstance(value, dict):
        return sum(1 for item in value.values() if item not in (None, "", [], {}))
    if isinstance(value, (list, tuple)):
        return len(value)
    return 1


def _freeze_value(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _freeze_spec(spec: dict) -> str:
    return _freeze_value(spec)


def _persist_template_source(path) -> Path:
    """Keep a stable copy of the original DOCX for template-based rendering."""
    source = Path(path)
    settings.ensure_dirs()
    if not source.exists():
        return source
    import hashlib

    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    target = settings.templates_dir / f"{digest}_{source.name}"
    if not target.exists():
        shutil.copy2(source, target)
    return target


def _write_template_schema_file(schema: dict) -> None:
    if not schema:
        return
    source = schema.get("source", {}) if isinstance(schema.get("source"), dict) else {}
    path = source.get("path")
    if not path:
        return
    try:
        out = Path(path).with_suffix(".template_schema.json")
        out.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def save_variant(variant: StyleVariant) -> StyleVariant:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO style_variants(library_id, name, description, structure_json, "
            "writing_style_json, terminology_json, format_spec_json, writing_patterns_json, "
            "style_samples_json, chapter_styles_json, reasoning_profile_json, institution_rules_json, "
            "source_reports, confidence, status) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (variant.library_id, variant.name, variant.description,
             json.dumps(variant.structure, ensure_ascii=False),
             json.dumps(variant.writing_style, ensure_ascii=False),
             json.dumps(variant.terminology, ensure_ascii=False),
             json.dumps(variant.format_spec, ensure_ascii=False),
             json.dumps(variant.writing_patterns, ensure_ascii=False),
             json.dumps(variant.style_samples, ensure_ascii=False),
             json.dumps(variant.chapter_styles, ensure_ascii=False),
             json.dumps(variant.reasoning_profile, ensure_ascii=False),
             json.dumps(variant.institution_rules, ensure_ascii=False),
             json.dumps(variant.source_reports, ensure_ascii=False),
             0.0, variant.status),
        )
        variant.id = cur.lastrowid
    return variant


def get_variant(variant_id: int) -> StyleVariant | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM style_variants WHERE id=?", (variant_id,)).fetchone()
    return _row_to_variant(row) if row else None


def list_variants() -> list[StyleVariant]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM style_variants WHERE status!='deleted' ORDER BY id DESC"
        ).fetchall()
    return [_row_to_variant(row) for row in rows]


def get_locked_variant() -> StyleVariant | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM style_variants WHERE status='locked' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return _row_to_variant(row) if row else None


def set_variant_status(variant_id: int, status: str) -> None:
    with connect() as conn:
        if status == "locked":
            row = conn.execute(
                "SELECT library_id FROM style_variants WHERE id=?", (variant_id,)
            ).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE style_variants SET status='confirmed' "
                    "WHERE library_id=? AND status='locked' AND id!=?",
                    (row["library_id"], variant_id),
                )
        conn.execute("UPDATE style_variants SET status=? WHERE id=?", (status, variant_id))


def update_variant(variant_id: int, name: str | None = None, description: str | None = None) -> None:
    sets: list[str] = []
    params: list = []
    if name is not None:
        sets.append("name=?")
        params.append(name)
    if description is not None:
        sets.append("description=?")
        params.append(description)
    if sets:
        params.append(variant_id)
        with connect() as conn:
            conn.execute(f"UPDATE style_variants SET {', '.join(sets)} WHERE id=?", params)


def delete_variant(variant_id: int) -> bool:
    """Soft-delete a template/style variant from the template center.

    Historical reports may still reference this row for export, so we keep the
    structured style data and simply hide it from management lists.
    """
    if get_variant(variant_id) is None:
        return False
    with connect() as conn:
        conn.execute("UPDATE style_variants SET status='deleted' WHERE id=?", (variant_id,))
    return True


def confirm_variant(variant_id: int) -> None:
    set_variant_status(variant_id, "confirmed")


def lock_variant(variant_id: int) -> None:
    set_variant_status(variant_id, "locked")


def _row_to_variant(row) -> StyleVariant:
    return StyleVariant(
        id=row["id"],
        library_id=row["library_id"],
        name=row["name"],
        description=row["description"],
        structure=_loads(row["structure_json"]),
        writing_style=_loads(row["writing_style_json"]),
        terminology=_loads(row["terminology_json"]),
        format_spec=_loads(row["format_spec_json"]),
        writing_patterns=_loads(row["writing_patterns_json"]),
        style_samples=_loads_samples(row["style_samples_json"]),
        chapter_styles=_loads_samples(row["chapter_styles_json"]),
        reasoning_profile=_loads(row["reasoning_profile_json"]),
        institution_rules=_loads(row["institution_rules_json"]),
        source_reports=_loads_list(row["source_reports"]),
        status=row["status"],
    )


def extract_docx_format(path) -> dict:
    """Extract layout tokens from a DOCX template."""
    if not str(path).lower().endswith(".docx"):
        return {}
    from docx import Document
    from docx.shared import Length
    from docx.oxml.ns import qn

    doc = Document(path)
    template_schema = compile_template(path)
    _write_template_schema_file(template_schema)
    spec: dict = {
        "source_template_path": str(path),
        "template_schema": template_schema,
    }

    def _font_name(font, style=None):
        if getattr(font, "name", None):
            return font.name
        try:
            rpr = style.element.rPr if style is not None else font._element.rPr
            if rpr is not None and rpr.rFonts is not None:
                return rpr.rFonts.get(qn("w:eastAsia")) or rpr.rFonts.get(qn("w:ascii")) or ""
        except Exception:
            return ""
        return ""

    def _font_size(font):
        return round(font.size.pt, 1) if getattr(font, "size", None) else None

    def _font_color(font):
        color = getattr(getattr(font, "color", None), "rgb", None)
        return str(color) if color else ""

    def _alignment(value):
        return str(value) if value is not None else ""

    def _border_snapshot(element) -> dict:
        borders: dict = {}
        try:
            ppr = element.pPr if hasattr(element, "pPr") else element.get_or_add_pPr()
            pbdr = ppr.find(qn("w:pBdr")) if ppr is not None else None
            if pbdr is None:
                return {}
            for name in ("top", "left", "bottom", "right", "between", "bar"):
                node = pbdr.find(qn(f"w:{name}"))
                if node is None:
                    continue
                borders[name] = {
                    "val": node.get(qn("w:val"), ""),
                    "color": node.get(qn("w:color"), ""),
                    "sz": node.get(qn("w:sz"), ""),
                    "space": node.get(qn("w:space"), ""),
                }
        except Exception:
            return {}
        return borders

    def _paragraph_snapshot(para) -> dict:
        pf = para.paragraph_format
        snapshot = {
            "style_name": para.style.name if para.style is not None else "",
            "alignment": _alignment(para.alignment or pf.alignment),
            "text_length": len(para.text.strip()),
        }
        if pf.left_indent is not None:
            snapshot["left_indent_cm"] = round(pf.left_indent.cm, 2)
        if pf.right_indent is not None:
            snapshot["right_indent_cm"] = round(pf.right_indent.cm, 2)
        if pf.first_line_indent is not None:
            snapshot["first_line_indent_cm"] = round(pf.first_line_indent.cm, 2)
        if pf.space_before is not None:
            snapshot["space_before_pt"] = round(pf.space_before.pt, 1)
        if pf.space_after is not None:
            snapshot["space_after_pt"] = round(pf.space_after.pt, 1)
        if pf.line_spacing is not None:
            if isinstance(pf.line_spacing, Length):
                snapshot["line_spacing_pt"] = round(pf.line_spacing.pt, 1)
            elif isinstance(pf.line_spacing, float):
                snapshot["line_spacing"] = round(pf.line_spacing, 2)
        if pf.keep_with_next is not None:
            snapshot["keep_with_next"] = bool(pf.keep_with_next)
        if pf.keep_together is not None:
            snapshot["keep_together"] = bool(pf.keep_together)
        if pf.page_break_before is not None:
            snapshot["page_break_before"] = bool(pf.page_break_before)
        borders = _border_snapshot(para._p)
        if borders:
            snapshot["borders"] = borders
        for run in para.runs:
            if run.text.strip():
                if _font_name(run.font):
                    snapshot["font_name"] = _font_name(run.font)
                if _font_size(run.font):
                    snapshot["font_size_pt"] = _font_size(run.font)
                if _font_color(run.font):
                    snapshot["font_color_rgb"] = _font_color(run.font)
                if run.bold is not None:
                    snapshot["bold"] = bool(run.bold)
                break
        return snapshot

    def _style_snapshot(style_name: str) -> dict:
        snapshot: dict = {}
        try:
            style = doc.styles[style_name]
            font = style.font
            pf = style.paragraph_format
            if _font_name(font, style):
                snapshot["font_name"] = _font_name(font, style)
            if _font_size(font):
                snapshot["font_size_pt"] = _font_size(font)
            if _font_color(font):
                snapshot["font_color_rgb"] = _font_color(font)
            if getattr(font, "bold", None) is not None:
                snapshot["bold"] = bool(font.bold)
            if getattr(font, "italic", None) is not None:
                snapshot["italic"] = bool(font.italic)
            if pf.line_spacing is not None:
                if isinstance(pf.line_spacing, Length):
                    snapshot["line_spacing_pt"] = round(pf.line_spacing.pt, 1)
                elif isinstance(pf.line_spacing, float):
                    snapshot["line_spacing"] = round(pf.line_spacing, 2)
            if pf.first_line_indent is not None:
                snapshot["first_line_indent_cm"] = round(pf.first_line_indent.cm, 2)
            if pf.left_indent is not None:
                snapshot["left_indent_cm"] = round(pf.left_indent.cm, 2)
            if pf.right_indent is not None:
                snapshot["right_indent_cm"] = round(pf.right_indent.cm, 2)
            if pf.space_before is not None:
                snapshot["space_before_pt"] = round(pf.space_before.pt, 1)
            if pf.space_after is not None:
                snapshot["space_after_pt"] = round(pf.space_after.pt, 1)
            if pf.alignment is not None:
                snapshot["alignment"] = _alignment(pf.alignment)
            if pf.keep_with_next is not None:
                snapshot["keep_with_next"] = bool(pf.keep_with_next)
            if pf.keep_together is not None:
                snapshot["keep_together"] = bool(pf.keep_together)
            if pf.page_break_before is not None:
                snapshot["page_break_before"] = bool(pf.page_break_before)
            borders = _border_snapshot(style.element)
            if borders:
                snapshot["borders"] = borders
        except Exception:
            return {}
        return snapshot

    try:
        spec["normal"] = _style_snapshot("Normal")
        normal = spec["normal"]
        if normal.get("font_name"):
            spec["font_name"] = normal["font_name"]
        if normal.get("font_size_pt"):
            spec["font_size_pt"] = normal["font_size_pt"]
        if normal.get("line_spacing_pt"):
            spec["line_spacing_pt"] = normal["line_spacing_pt"]
        elif normal.get("line_spacing"):
            spec["line_spacing"] = normal["line_spacing"]
        if normal.get("first_line_indent_cm") is not None:
            spec["first_line_indent_cm"] = normal["first_line_indent_cm"]
        if normal.get("space_before_pt") is not None:
            spec["space_before_pt"] = normal["space_before_pt"]
        if normal.get("space_after_pt") is not None:
            spec["space_after_pt"] = normal["space_after_pt"]
        if normal.get("alignment"):
            spec["alignment"] = normal["alignment"]
        if normal.get("font_color_rgb"):
            spec["font_color_rgb"] = normal["font_color_rgb"]
    except Exception:
        pass

    try:
        spec["title"] = _style_snapshot("Title")
        spec["heading1"] = _style_snapshot("Heading 1")
        spec["heading2"] = _style_snapshot("Heading 2")
        spec["heading3"] = _style_snapshot("Heading 3")
        if spec["heading1"].get("font_name"):
            spec["heading_font"] = spec["heading1"]["font_name"]
        if spec["heading1"].get("font_size_pt"):
            spec["heading_size_pt"] = spec["heading1"]["font_size_pt"]
    except Exception:
        pass

    try:
        section = doc.sections[0]
        spec["margins_cm"] = {
            "top": round(section.top_margin.cm, 2),
            "bottom": round(section.bottom_margin.cm, 2),
            "left": round(section.left_margin.cm, 2),
            "right": round(section.right_margin.cm, 2),
        }
        spec["page_cm"] = {
            "width": round(section.page_width.cm, 2),
            "height": round(section.page_height.cm, 2),
        }
        header_text = "".join(p.text for p in section.header.paragraphs).strip() if section.header else ""
        footer_text = "".join(p.text for p in section.footer.paragraphs).strip() if section.footer else ""
        if header_text:
            spec["header_text"] = header_text[:80]
        if footer_text:
            spec["footer_text"] = footer_text[:80]
        if section.header and section.header.paragraphs:
            spec["header_style"] = {"alignment": _alignment(section.header.paragraphs[0].alignment)}
        if section.footer and section.footer.paragraphs:
            spec["footer_style"] = {"alignment": _alignment(section.footer.paragraphs[0].alignment)}
    except Exception:
        pass

    try:
        first_para = next((para for para in doc.paragraphs if para.text.strip()), None)
        if first_para is not None:
            spec["first_paragraph_style"] = _paragraph_snapshot(first_para)
    except Exception:
        pass

    try:
        body_para = next((para for para in doc.paragraphs if para.text.strip() and not _looks_like_heading(para.text)), None)
        if body_para is not None:
            spec["body_paragraph"] = _paragraph_snapshot(body_para)
    except Exception:
        pass

    try:
        patterns = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            pattern = _numbering_pattern(text)
            if pattern and pattern not in patterns:
                patterns.append(pattern)
        if patterns:
            spec["numbering_patterns"] = patterns[:8]
    except Exception:
        pass

    try:
        if doc.tables:
            table = doc.tables[0]
            spec["table_style"] = {
                "rows": len(table.rows),
                "cols": len(table.columns),
                "style_name": table.style.name if table.style is not None else "",
            }
            for row in table.rows[:2]:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        for run in para.runs:
                            if run.text.strip():
                                if _font_name(run.font):
                                    spec["table_style"]["font_name"] = _font_name(run.font)
                                if _font_size(run.font):
                                    spec["table_style"]["font_size_pt"] = _font_size(run.font)
                                break
                        if spec["table_style"].get("font_name"):
                            break
                    if spec["table_style"].get("font_name"):
                        break
                if spec["table_style"].get("font_name"):
                    break
    except Exception:
        pass

    try:
        inline_shapes = getattr(doc, "inline_shapes", [])
        if inline_shapes:
            shape = inline_shapes[0]
            spec["image_rules"] = {
                "count": len(inline_shapes),
                "first_width_cm": round(shape.width.cm, 2) if shape.width else None,
                "first_height_cm": round(shape.height.cm, 2) if shape.height else None,
            }
    except Exception:
        pass

    spec["document_format"] = _document_format(spec)
    return spec


def _document_format(spec: dict) -> dict:
    """Canonical structured style model for export and UI display."""
    schema = spec.get("template_schema") if isinstance(spec.get("template_schema"), dict) else {}
    roles = schema.get("style", {}).get("roles", {}) if schema else {}
    document = schema.get("document", {}) if schema else {}
    role_body = _legacy_role_style(roles.get("body"))
    role_title = _legacy_role_style(roles.get("document_title"))
    role_h1 = _legacy_role_style(roles.get("heading_1"))
    role_h2 = _legacy_role_style(roles.get("heading_2"))
    role_h3 = _legacy_role_style(roles.get("heading_3"))
    return {
        "page": {
            "size_cm": spec.get("page_cm", {}) or _legacy_page_size(document.get("page", {})),
            "margins_cm": spec.get("margins_cm", {}) or _legacy_margins(document.get("margins", {})),
            "header": {"text": spec.get("header_text", ""), "style": spec.get("header_style", {})},
            "footer": {"text": spec.get("footer_text", ""), "style": spec.get("footer_style", {})},
        },
        "typography": {
            "body": role_body or spec.get("body_paragraph") or spec.get("normal", {}),
            "title": role_title or spec.get("title", {}),
            "heading1": role_h1 or spec.get("heading1", {}),
            "heading2": role_h2 or spec.get("heading2", {}),
            "heading3": role_h3 or spec.get("heading3", {}),
        },
        "structure": {
            "numbering_patterns": spec.get("numbering_patterns", []),
            "first_paragraph": spec.get("first_paragraph_style", {}),
            "table": spec.get("table_style", {}),
            "image": spec.get("image_rules", {}),
        },
        "export_hints": {
            "clear_builtin_heading_borders": True,
            "use_template_heading_borders": any(
                (spec.get(name) or {}).get("borders") for name in ("title", "heading1", "heading2", "heading3")
            ),
        },
    }


def _legacy_role_style(role_style: dict | None) -> dict:
    if not isinstance(role_style, dict):
        return {}
    paragraph = role_style.get("paragraph", {}) if isinstance(role_style.get("paragraph"), dict) else {}
    run = role_style.get("run", {}) if isinstance(role_style.get("run"), dict) else {}
    merged = {
        "style_name": role_style.get("source_style", ""),
        "alignment": paragraph.get("alignment", ""),
        "left_indent_cm": paragraph.get("left_indent_cm"),
        "right_indent_cm": paragraph.get("right_indent_cm"),
        "first_line_indent_cm": paragraph.get("first_line_indent_cm"),
        "space_before_pt": paragraph.get("space_before_pt"),
        "space_after_pt": paragraph.get("space_after_pt"),
        "borders": paragraph.get("borders"),
        "font_name": run.get("font_east_asia") or run.get("font_ascii"),
        "font_size_pt": run.get("font_size_pt"),
        "font_color_rgb": run.get("font_color"),
        "bold": run.get("bold"),
        "italic": run.get("italic"),
    }
    line_spacing = paragraph.get("line_spacing")
    if isinstance(line_spacing, dict):
        if line_spacing.get("type") == "multiple":
            merged["line_spacing"] = line_spacing.get("value")
        elif line_spacing.get("type") == "exact_pt":
            merged["line_spacing_pt"] = line_spacing.get("value")
    return {k: v for k, v in merged.items() if v not in (None, "", "unknown", {})}


def _legacy_page_size(page: dict) -> dict:
    if not isinstance(page, dict):
        return {}
    return {"width": page.get("width_cm"), "height": page.get("height_cm")}


def _legacy_margins(margins: dict) -> dict:
    if not isinstance(margins, dict):
        return {}
    return {
        "top": margins.get("top_cm"),
        "bottom": margins.get("bottom_cm"),
        "left": margins.get("left_cm"),
        "right": margins.get("right_cm"),
    }


def _looks_like_heading(text: str) -> bool:
    stripped = text.strip()
    return bool(_HEADING_RE.match(stripped)) or len(stripped) <= 25 and stripped.endswith(("：", ":"))


def _numbering_pattern(text: str) -> str:
    if re.match(r"^第[一二三四五六七八九十百]+[章节部分]", text):
        return "第X章节式"
    if re.match(r"^[一二三四五六七八九十]+、", text):
        return "中文顿号一级标题"
    if re.match(r"^（[一二三四五六七八九十]+）", text) or re.match(r"^\([一二三四五六七八九十]+\)", text):
        return "中文括号二级标题"
    if re.match(r"^\d+[.、]", text):
        return "阿拉伯数字编号"
    return ""


def _loads(text: str) -> dict:
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _loads_list(text: str) -> list[str]:
    try:
        value = json.loads(text or "[]")
        return [str(item) for item in value] if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def _loads_samples(text: str) -> list[dict]:
    try:
        value = json.loads(text or "[]")
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []
