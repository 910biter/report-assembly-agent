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
from app.db import session_scope
from app.infrastructure.orm import ORMVariant, Base
from sqlalchemy import select
from app.gateway import model_gateway
from app.llm_scheduler import invoke
from app.models import StyleVariant
from app.memory.style_profile import (
    aggregate_style_metrics,
    annotate_exemplars,
    build_report_exemplars,
    compact_exemplar_bank,
)
from app.template_engine import compile_template

_MAX_CHARS_PER_REPORT = 3000
_SAMPLE_LENGTH = 150
_STYLE_LEARNER_VERSION = "editorial-v3"
_DEFAULT_PROFILE_NAME = "综合报告风格"
_REALIZATION_KEYS = (
    "fact_expression", "judgment_expression", "fact_judgment_transition",
    "information_compression", "attribution_style",
)

# ---------- 风格库(v2):逐份分析 → 聚类 → 变体 ----------

_HEADING_RE = re.compile(r"^(第?[一二三四五六七八九十百]+[章节部分]、|\([一二三四五六七八九十]+\)|\d+[.、])\s*\S+")

_FEATURE_PROMPT = """分析以下报告的体裁与风格特征,严格输出 JSON(不要任何解释):
{
  "topic_type": "用简短中文名概括报告体裁，只填写结果，不要复述字段说明或示例",
  "structure_notes": "章节组织特点(是否先结论后展开、典型章节顺序)",
  "language_notes": "语言特点(正式程度、句式、数据使用)"
}"""


def _normalize_profile_label(value, fallback: str = _DEFAULT_PROFILE_NAME) -> str:
    """Accept a concise model label, but never persist prompt/schema text as data."""
    label = re.sub(r"\s+", "", str(value or "").strip().strip('"\''))
    protocol_markers = ("报告类型,", "报告类型名", "用2-4字", "用简短中文名", "如:", "如：", "沿用{")
    if (
        not label
        or len(label) > 24
        or any(marker in label for marker in protocol_markers)
        or label.count("/") >= 2
    ):
        return fallback
    return label


def _editorial_confidence(
    writing: dict, writing_patterns: dict, member_count: int, exemplar_count: int,
) -> str:
    semantic_patterns = {key: value for key, value in writing_patterns.items() if key != "observed_metrics" and value}
    if not writing or not semantic_patterns:
        return "low"
    return "high" if member_count >= 2 and exemplar_count >= 12 else "medium"


def _validate_profile_payload(payload: dict) -> None:
    if not isinstance(payload.get("writing_style"), dict) or not payload.get("writing_style"):
        raise ValueError("STYLE_PROFILE_WRITING_STYLE_EMPTY")
    patterns = payload.get("writing_patterns")
    if not isinstance(patterns, dict) or not patterns:
        raise ValueError("STYLE_PROFILE_WRITING_PATTERNS_EMPTY")
    if sum(bool(patterns.get(key)) for key in _REALIZATION_KEYS) < 3:
        raise ValueError("STYLE_PROFILE_CONTENT_REALIZATION_INCOMPLETE")

_VARIANT_PROMPT = """以下为同一机构、同一类型({type})的 {count} 份报告(每份含章节结构与片段采样):

{reports}

请提炼该类型报告的写作规范,严格输出 JSON(不要任何解释):
{{
  "name": "报告类型名(沿用 {type})",
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
  "writing_patterns": {{
    "information_progression": "段落通常如何从已知信息推进到判断",
    "paragraph_architecture": "段内常见组织方式,不要写固定句数",
    "sentence_rhythm": "长短句、复句和承接方式",
    "transition_style": "章节和段落之间如何衔接",
    "specificity_preference": "具体事实与抽象概括如何取舍",
    "fact_expression": "名称、数字、时间、行动和结果等事实通常如何进入句子",
    "judgment_expression": "结论、风险、趋势和建议通常如何措辞并控制确定性",
    "fact_judgment_transition": "文章如何从事实陈述自然过渡到解释或判断",
    "information_compression": "并列事实何时展开、合并、概括或列举",
    "attribution_style": "来源、依据和主体通常如何在正文中表述",
    "stance_and_modality": "确定、审慎、风险与建议分别如何措辞",
    "opening_pattern": "开篇通常完成什么表达任务",
    "closing_pattern": "收束通常完成什么表达任务",
    "list_table_preference": "何时使用自然段、清单或表格"
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
  }},
  "sample_annotations": [
    {{"sample_id": "输入中的样例编号", "sample_type": "opening/fact/analysis/risk/conclusion/transition", "purpose": "该段承担的表达任务", "tags": ["可检索语义标签"]}}
  ]
}}"""

_STYLE_SAMPLE_NOTE = """以下是按章节与自然段抽取的候选样例。请分析表达方式，不要复述或泛化其中的业务事实：
{style_samples}
"""

def analyze_library(
    reports: list[dict],
    force: bool = False,
    progress_callback=None,
) -> list[StyleVariant]:
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
            _emit_progress(progress_callback, "profiling", 1, 1, "复用已学习画像")
            return reused

    features = []
    for report_index, report in enumerate(reports, start=1):
        text = report.get("text", "")
        headings = _extract_headings(text, report.get("path"))
        samples = _sample_report(text)
        try:
            payload = invoke(
                "template", model_gateway.generate_json,
                f"报告文本:\n{text[:_MAX_CHARS_PER_REPORT]}",
                system=_FEATURE_PROMPT,
                think=False,
                max_tokens=settings.style_probe_output_tokens,
            )
            topic_type = _normalize_profile_label(payload.get("topic_type"))
        except Exception:
            topic_type = _DEFAULT_PROFILE_NAME
        features.append({
            "filename": report.get("filename", ""),
            "text": text,
            "path": report.get("path"),
            "headings": headings,
            "samples": samples,
            "topic_type": topic_type,
        })
        _emit_progress(
            progress_callback, "classifying", report_index, len(reports),
            str(report.get("filename") or ""),
        )
    # 聚类:按体裁分组;体裁不明的归入"其他"组按结构再拆
    groups: dict[str, list[dict]] = {}
    for feature in features:
        groups.setdefault(feature["topic_type"], []).append(feature)
    variants = []
    group_items = list(groups.items())
    for group_index, (topic_type, members) in enumerate(group_items, start=1):
        _emit_progress(
            progress_callback, "profiling", group_index - 1, len(group_items), topic_type,
        )
        variants.append(_build_variant(library_id, topic_type, members))
        _emit_progress(
            progress_callback, "profiling", group_index, len(group_items), topic_type,
        )
    _record_source_hash(reports, variants)
    return variants


def _emit_progress(callback, phase: str, current: int, total: int, label: str = "") -> None:
    if callback is None:
        return
    try:
        callback({"phase": phase, "current": current, "total": total, "label": label})
    except Exception:
        pass


def _source_hash(report: dict) -> str:
    """Source content plus learner version, so upgraded profiles are relearned."""
    import hashlib
    path = report.get("path")
    try:
        if path and Path(path).exists():
            digest = hashlib.sha256(Path(path).read_bytes())
            digest.update(_STYLE_LEARNER_VERSION.encode())
            return digest.hexdigest()[:16]
    except Exception:
        pass
    fallback = f"{report.get('filename') or ''}:{_STYLE_LEARNER_VERSION}"
    return hashlib.sha256(fallback.encode()).hexdigest()[:16]


def _reuse_by_source_hash(reports: list[dict]) -> list[StyleVariant] | None:
    """同一源文件已分析过 → 复用已有变体(返回 None 表示需新分析)。"""
    if len(reports) != 1:
        return None
    digest = _source_hash(reports[0])
    with session_scope() as s:
        row = s.execute(
            select(ORMVariant.c.id).where(
                ORMVariant.c.source_hash == digest,
                ORMVariant.c.status != "deleted",  # 已被删除的旧变体不复用,重新学习应能重建
            )
            .order_by(ORMVariant.c.id).limit(1)
        ).mappings().first()
    if row is None:
        return None
    variant = get_variant(row["id"])
    return [variant] if variant else None


def _record_source_hash(reports: list[dict], variants: list[StyleVariant]) -> None:
    """分析完成后记录源文件 hash 到变体(幂等身份)。"""
    if len(reports) != 1 or not variants:
        return
    digest = _source_hash(reports[0])
    with session_scope() as s:
        s.execute(
            ORMVariant.update().where(ORMVariant.c.id == variants[0].id).values(source_hash=digest)
        )


def _ensure_library() -> int:
    library_table = Base.metadata.tables["style_library"]
    with session_scope() as s:
        row = s.execute(select(library_table.c.id).order_by(library_table.c.id).limit(1)).first()
        if row:
            return int(row[0])
        result = s.execute(library_table.insert().values(institution=""))
        return int(result.inserted_primary_key[0])


def _extract_headings(text: str, path=None) -> list[dict]:
    """提取标题序列(带层级 level)。docx 优先用标题样式,否则按增强编号正则。
    返回 [{"text": str, "level": int}],保留层级供结构学习。"""
    headings: list[dict] = []
    if path and str(path).lower().endswith(".docx"):
        try:
            from docx import Document

            doc = Document(path)
            for para in doc.paragraphs:
                if not para.text.strip():
                    continue
                style = (para.style.name or "").lower()
                if style.startswith(("heading", "标题")):
                    headings.append({"text": para.text.strip(), "level": _heading_level_of_text(para.text.strip())})
        except Exception:
            headings = []
    if not headings:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            level = _heading_level_of_text(line)
            if level:
                headings.append({"text": line, "level": level})
    return headings[:30]


def _heading_level_of_text(line: str) -> int:
    """按增强编号正则推断标题层级(1/2/3),非标题返回 0。
    与 compiler 的正则口径一致,并用负向前瞻避免把 "1.1"/"1.1.1" 误判为低层级。"""
    if re.match(r"^(?:[一二三四五六七八九十]+[、.]|第[一二三四五六七八九十\d]+[章节部分])\s*\S+", line) or \
       re.match(r"^\d+(?:\.(?!\d)|[、\s])\s*\S+", line):
        return 1
    if re.match(r"^[（(][一二三四五六七八九十\d]+[)）]\s*\S+", line) or \
       re.match(r"^\d+\.\d+(?:\.(?!\d)|[、\s])\s*\S+", line):
        return 2
    if re.match(r"^\d+\.\d+\.\d+[、\s]?\s*\S+", line):
        return 3
    return 0


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
    all_exemplars = build_report_exemplars(members)
    exemplar_bank = compact_exemplar_bank(all_exemplars, limit=200)
    report_blocks = []
    for member in members:
        heading_lines = []
        for _h in (member.get("headings") or []):
            _lv = 1
            _txt = str(_h)
            if isinstance(_h, dict):
                try:
                    _lv = int(_h.get("level", 1))
                except Exception:
                    _lv = 1
                _txt = _h.get("text") or ""
            heading_lines.append("  " * max(0, _lv - 1) + "- " + _txt)
        headings = "\n".join(heading_lines) or "(未识别标题)"
        report_blocks.append(
            f"[{member['filename']}]\n章节结构: {headings}\n"
            f"开篇: {member['samples']['opening'][:150]}\n"
            f"正文: {member['samples']['middle'][:150]}\n"
            f"结尾: {member['samples']['ending'][:150]}"
        )
    sample_packet = "\n\n".join(
        f"[{item.get('exemplar_id')}] 来源={item.get('source_report')} / 章节={item.get('section')} / "
        f"位置={item.get('structural_role')}\n{str(item.get('content') or '')[:700]}"
        for item in compact_exemplar_bank(all_exemplars, limit=24)
    ) or "(没有可用的自然段样例)"
    payload = invoke(
        "template", model_gateway.generate_json,
        _VARIANT_PROMPT.replace("{type}", topic_type)
        .replace("{reports}", "\n\n---\n\n".join(report_blocks))
        + "\n\n"
        + _STYLE_SAMPLE_NOTE.replace("{style_samples}", sample_packet),
        system="你是机构报告风格分析师。每个字段只写可由样例支持的简洁结论，不输出空泛解释。",
        think=False,
        max_tokens=settings.style_profile_output_tokens,
    )
    _validate_profile_payload(payload)
    structure = payload.get("structure") if isinstance(payload.get("structure"), dict) else {}
    writing = payload.get("writing_style") if isinstance(payload.get("writing_style"), dict) else {}
    writing_patterns = payload.get("writing_patterns") if isinstance(payload.get("writing_patterns"), dict) else {}
    terminology = payload.get("terminology") if isinstance(payload.get("terminology"), dict) else {}
    chapter_styles = payload.get("chapter_styles") if isinstance(payload.get("chapter_styles"), list) else []
    reasoning = payload.get("reasoning_profile") if isinstance(payload.get("reasoning_profile"), dict) else {}
    institution_rules = payload.get("institution_rules") if isinstance(payload.get("institution_rules"), dict) else {}
    annotations = payload.get("sample_annotations") if isinstance(payload.get("sample_annotations"), list) else []
    exemplar_bank = annotate_exemplars(exemplar_bank, annotations)
    observed_metrics = aggregate_style_metrics(all_exemplars)
    if observed_metrics:
        writing_patterns = {**writing_patterns, "observed_metrics": observed_metrics}
    samples = [
        {"sample_type": item.get("sample_type", "fact"), "content": item.get("content", "")}
        for item in exemplar_bank[:12]
    ] or _collect_samples(members)
    format_spec = _collect_format(members)
    variant = StyleVariant(
        library_id=library_id,
        name=_normalize_profile_label(payload.get("name"), fallback=topic_type),
        description="",
        structure=structure,
        writing_style=writing,
        writing_patterns=writing_patterns,
        terminology=terminology,
        format_spec=format_spec,
        style_samples=samples,
        chapter_styles=chapter_styles,
        reasoning_profile=reasoning,
        institution_rules=institution_rules,
        exemplar_bank=exemplar_bank[:200],
        structure_policy={
            "mode": "SOFT_STRUCTURE",
            "source": "inferred_from_reports",
            "confirmed": False,
            "principle": "历史目录仅作参考；内容结构由当前任务的事实和分析决定。",
        },
        profile_confidence={
            "document_format": "high" if format_spec else "unavailable",
            "content_structure": "medium" if structure else "low",
            "editorial_style": _editorial_confidence(
                writing, writing_patterns, len(members), len(exemplar_bank),
            ),
        },
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
    """Keep complete DOCX variants; never build a field-level hybrid template."""
    specs: list[dict] = []
    for member in members:
        if member.get("path") and str(member["path"]).lower().endswith(".docx"):
            source_path = _persist_template_source(member["path"])
            spec = extract_docx_format(source_path)
            if spec:
                specs.append(spec)
    if not specs:
        return {}
    # A renderer must use one coherent OOXML template.  Mixing the title from
    # one file with body/numbering from another creates a document that never
    # existed and cannot be reliably validated.
    dominant = max(specs, key=_value_completeness)
    _unused, conflicts = _merge_format_specs(specs)
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
            "mode": "whole_template_variant",
            "principle": "始终选择一份完整原始 DOCX 作为渲染母版，不跨模板拼接格式字段。",
            "tie_breakers": [
                "优先用户明确选择或锁定的模板",
                "未指定时优先结构化字段更完整的模板",
                "其余模板作为完整 alternatives 保留",
            ],
            "conflict_policy": [
                "格式冲突不自动投票合并，必须选择完整模板变体。",
                "结构策略与文档版式分开确认，FORMAT_ONLY 不继承示例目录。",
            ],
        },
        "sample_count": len(specs),
    }


def _strip_merge_noise(value):
    """投票前剥离非格式动态元数据(compiled_at / source.*),避免多数投票因每次编译时间戳
    不同而从不相等、投票退化成不稳定 tie-break。只影响判别,不影响保留的完整值。"""
    if isinstance(value, dict):
        v = dict(value)
        v.pop("compiled_at", None)
        v.pop("source", None)
        return v
    return value


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
            frozen = _freeze_value(_strip_merge_noise(value))
            counts[frozen] = counts.get(frozen, 0) + 1
            score = (counts[frozen], _value_completeness(value))
            if score > best_score:
                best_value = value
                best_score = score
        dominant[key] = best_value
        unique_values = []
        for value in values:
            if all(_freeze_value(_strip_merge_noise(value)) != _freeze_value(_strip_merge_noise(other))
                   for other in unique_values):
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
    with session_scope() as s:
        result = s.execute(
            ORMVariant.insert().values(
                library_id=variant.library_id, name=variant.name,
                description=variant.description,
                structure_json=json.dumps(variant.structure, ensure_ascii=False),
                writing_style_json=json.dumps(variant.writing_style, ensure_ascii=False),
                terminology_json=json.dumps(variant.terminology, ensure_ascii=False),
                format_spec_json=json.dumps(variant.format_spec, ensure_ascii=False),
                writing_patterns_json=json.dumps(variant.writing_patterns, ensure_ascii=False),
                style_samples_json=json.dumps(variant.style_samples, ensure_ascii=False),
                chapter_styles_json=json.dumps(variant.chapter_styles, ensure_ascii=False),
                reasoning_profile_json=json.dumps(variant.reasoning_profile, ensure_ascii=False),
                institution_rules_json=json.dumps(variant.institution_rules, ensure_ascii=False),
                structure_policy_json=json.dumps(variant.structure_policy, ensure_ascii=False),
                evidence_usage_profile_json=json.dumps(variant.evidence_usage_profile, ensure_ascii=False),
                exemplar_bank_json=json.dumps(variant.exemplar_bank, ensure_ascii=False),
                learning_cases_json=json.dumps(variant.learning_cases, ensure_ascii=False),
                profile_confidence_json=json.dumps(variant.profile_confidence, ensure_ascii=False),
                profile_version=int(variant.profile_version or 1),
                source_reports=json.dumps(variant.source_reports, ensure_ascii=False),
                confidence=0.0, status=variant.status,
            )
        )
        variant.id = int(result.inserted_primary_key[0])
    return variant


def get_variant(variant_id: int) -> StyleVariant | None:
    with session_scope() as s:
        row = s.execute(select(ORMVariant).where(ORMVariant.c.id == variant_id)).mappings().first()
    return _row_to_variant(dict(row)) if row else None


def list_variants() -> list[StyleVariant]:
    with session_scope() as s:
        rows = s.execute(
            select(ORMVariant).where(ORMVariant.c.status != "deleted")
            .order_by(ORMVariant.c.id.desc())
        ).mappings().all()
    return [_row_to_variant(dict(row)) for row in rows]


def get_locked_variant() -> StyleVariant | None:
    with session_scope() as s:
        row = s.execute(
            select(ORMVariant).where(ORMVariant.c.status == "locked")
            .order_by(ORMVariant.c.id.desc()).limit(1)
        ).mappings().first()
    return _row_to_variant(dict(row)) if row else None


def set_variant_status(variant_id: int, status: str) -> None:
    with session_scope() as s:
        if status == "locked":
            row = s.execute(
                select(ORMVariant.c.library_id).where(ORMVariant.c.id == variant_id)
            ).first()
            if row is not None:
                s.execute(
                    ORMVariant.update()
                    .where(ORMVariant.c.library_id == row[0],
                           ORMVariant.c.status == "locked",
                           ORMVariant.c.id != variant_id)
                    .values(status="confirmed")
                )
        s.execute(ORMVariant.update().where(ORMVariant.c.id == variant_id).values(status=status))


def update_variant(variant_id: int, name: str | None = None, description: str | None = None) -> None:
    values: dict = {}
    if name is not None:
        values["name"] = name
    if description is not None:
        values["description"] = description
    if values:
        with session_scope() as s:
            s.execute(ORMVariant.update().where(ORMVariant.c.id == variant_id).values(**values))


def update_profile(variant_id: int, payload: dict) -> StyleVariant:
    """Update explicit, user-reviewable profile layers without touching OOXML."""
    allowed = {
        "writing_style": "writing_style_json",
        "writing_patterns": "writing_patterns_json",
        "terminology": "terminology_json",
        "chapter_styles": "chapter_styles_json",
        "structure_policy": "structure_policy_json",
        "exemplar_bank": "exemplar_bank_json",
        "profile_confidence": "profile_confidence_json",
    }
    values: dict = {}
    for key, column in allowed.items():
        if key in payload:
            values[column] = json.dumps(payload[key], ensure_ascii=False)
    if not values:
        variant = get_variant(variant_id)
        if variant is None:
            raise ValueError("VARIANT_NOT_FOUND")
        return variant
    current = get_variant(variant_id)
    if current is None:
        raise ValueError("VARIANT_NOT_FOUND")
    values["profile_version"] = int(current.profile_version or 1) + 1
    with session_scope() as s:
        s.execute(ORMVariant.update().where(ORMVariant.c.id == variant_id).values(**values))
    return get_variant(variant_id)


def delete_variant(variant_id: int) -> bool:
    """Soft-delete a template/style variant from the template center.

    Historical reports may still reference this row for export, so we keep the
    structured style data and simply hide it from management lists.
    """
    if get_variant(variant_id) is None:
        return False
    with session_scope() as s:
        s.execute(ORMVariant.update().where(ORMVariant.c.id == variant_id).values(status="deleted"))
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
        structure_policy=_loads(row.get("structure_policy_json", "{}")),
        evidence_usage_profile=_loads(row.get("evidence_usage_profile_json", "{}")),
        exemplar_bank=_loads_samples(row.get("exemplar_bank_json", "[]")),
        learning_cases=_loads_samples(row.get("learning_cases_json", "[]")),
        profile_confidence=_loads(row.get("profile_confidence_json", "{}")),
        profile_version=int(row.get("profile_version") or 1),
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
        # 兼容中文/自定义标题样式名:同为标题,但命名来源不同——内置中文本地化("标题 1")、
        # 模板自定义("一级标题")、英文("Heading 1")等,逐个尝试。
        _ALIASES = {
            "Heading 1": ["Heading 1", "标题 1", "一级标题", "标题一"],
            "Heading 2": ["Heading 2", "标题 2", "二级标题", "标题二"],
            "Heading 3": ["Heading 3", "标题 3", "三级标题", "标题三"],
            "Title": ["Title", "标题", "文档标题", "主标题"],
            "Normal": ["Normal", "正文"],
        }
        style = None
        for _name in _ALIASES.get(style_name, [style_name]):
            try:
                style = doc.styles[_name]
                break
            except Exception:
                style = None
        if style is None:
            return snapshot
        try:
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
