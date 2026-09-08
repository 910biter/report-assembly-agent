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

from app.agents.base import BaseAgent
from app.config import settings
from app.context_budget import ContextSection, build_prompt_from_sections
from app.db import session_scope
from app.infrastructure.orm import ORMVariant, Base
from sqlalchemy import select
from app.models import StyleVariant
from app.runtime_profiles import stage_input_budget_tokens
from app.memory.style_profile import (
    aggregate_style_metrics,
    annotate_exemplars,
    build_report_exemplars,
    compact_exemplar_bank,
)
from app.template_engine import compile_template

_SAMPLE_LENGTH = 150
_STYLE_LEARNER_VERSION = "editorial-v5-document-shape"
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
  "document_shape": "formal_report/research_review/article_sections/continuous_article/message_push/news_release",
  "structure_notes": "章节组织特点(是否先结论后展开、典型章节顺序)",
  "language_notes": "语言特点(正式程度、句式、数据使用)"
}"""


class _StyleProbeAgent(BaseAgent):
    name = "style_probe"
    role = _FEATURE_PROMPT


class _StyleProfileAgent(BaseAgent):
    name = "style_profile"
    role = "你是机构报告风格分析师。每个字段只写可由样例支持的简洁结论，不输出空泛解释。"


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

请提炼该类型报告的写作规范,严格输出 JSON(不要任何解释)。来源用途是能力边界：文体参考只学习表达，结构参考才可提供组织建议，只有 DOCX 版式母版可用于 Word 版式复用；不得因为样例含目录就默认冻结未来报告目录:
{{
  "name": "报告类型名(沿用 {type})",
  "structure": {{
    "sections": [{{"title": "一、…", "children": ["二级标题…"]}}],
    "summary_first": true或false,
    "conclusion_first": true或false,
    "document_shape": {{
      "kind": "structured_report/research_review/article_sections/continuous_article/message_push/news_release",
      "heading_policy": "numbered/plain/none",
      "section_policy": "required/optional/hidden",
      "subheading_policy": "numbered/plain/hidden",
      "render_base": "selected_template/default_structured/blank_article",
      "opening": "title_only/lead/summary",
      "closing": "natural/conclusion/signature",
      "rationale": "样例中可观察到的形态依据"
    }}
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
  "material_realization": {{
    "fact_selection": "一段中如何选择主事实、背景事实和限定事实",
    "detail_retention": "时间、数字、主体、文件名等具体信息通常保留到什么程度",
    "multi_source_synthesis": "多来源一致、互补或差异信息如何合并表达",
    "fact_to_analysis": "事实如何支撑解释、判断、风险或建议，不得把相关性写成因果",
    "boundary_expression": "证据不足、仅能部分证明或存在不确定性时如何表述",
    "structure_choice": "何时采用自然段、清单或表格以承载材料信息"
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
    {{"sample_id": "输入中的样例编号", "sample_type": "opening/fact/analysis/risk/conclusion/transition", "rhetorical_role": "opening/fact/analysis/risk/conclusion/transition", "purpose": "该段承担的表达任务", "realization_mode": "单事实展开/多事实综合/事实到判断/风险边界/建议形成", "discourse_moves": ["事实引入", "背景补充", "影响判断"], "tags": ["可检索语义标签"]}}
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
            prompt, _audit = build_prompt_from_sections("style_probe", [
                ContextSection("instruction", ["分析以下报告的体裁与风格特征。"], weight=5, required_items=1),
                ContextSection("report_paragraphs", [item for item in text.splitlines() if item.strip()], weight=3, required_items=1),
            ], stage_input_budget_tokens("style_probe"))
            payload = _StyleProbeAgent().generate_json(
                prompt, max_tokens=settings.style_probe_output_tokens,
            )
            topic_type = _normalize_profile_label(payload.get("topic_type"))
        except Exception:
            topic_type = _DEFAULT_PROFILE_NAME
        features.append({
            "filename": report.get("filename", ""),
            "text": text,
            "path": report.get("path"),
            "asset_role": report.get("asset_role") or "auto",
            "headings": headings,
            "samples": samples,
            "topic_type": topic_type,
        })
        _emit_progress(
            progress_callback, "classifying", report_index, len(reports),
            str(report.get("filename") or ""),
        )
    # A user upload is one deliberate reference set, not an instruction to
    # create several templates merely because source documents have different
    # genres.  Preserve per-source signals inside the profile, then produce
    # one coherent, selectable template image for this submission.
    topic_type = _combined_topic_type(features)
    _emit_progress(progress_callback, "profiling", 0, 1, topic_type)
    variants = [_build_variant(library_id, topic_type, features)]
    _emit_progress(progress_callback, "profiling", 1, 1, topic_type)
    _record_source_hash(reports, variants)
    return variants


def _combined_topic_type(features: list[dict]) -> str:
    """Name a submitted reference set without splitting it into variants."""
    labels = [str(item.get("topic_type") or "").strip() for item in features]
    labels = [label for label in labels if label and label != _DEFAULT_PROFILE_NAME]
    if not labels:
        return _DEFAULT_PROFILE_NAME
    counts = {label: labels.count(label) for label in set(labels)}
    dominant = max(counts, key=lambda label: (counts[label], len(label)))
    # A single topical outlier should not rename a coherent reference set.
    return dominant if counts[dominant] * 2 >= len(features) else _DEFAULT_PROFILE_NAME


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
            digest.update(str(report.get("asset_role") or "auto").encode())
            return digest.hexdigest()[:16]
    except Exception:
        pass
    fallback = f"{report.get('filename') or ''}:{report.get('asset_role') or 'auto'}:{_STYLE_LEARNER_VERSION}"
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
            schema = compile_template(path)
            headings = [
                {"text": str(item.get("text_pattern") or ""), "level": int(item.get("level") or 1)}
                for item in schema.get("structure", {}).get("heading_patterns", [])
                if str(item.get("text_pattern") or "").strip()
            ]
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
            f"[{member['filename']}]\n来源用途: {_asset_role_label(member.get('asset_role'))}\n章节结构: {headings}\n"
            f"开篇: {member['samples']['opening'][:150]}\n"
            f"正文: {member['samples']['middle'][:150]}\n"
            f"结尾: {member['samples']['ending'][:150]}"
        )
    sample_packet = "\n\n".join(
        f"[{item.get('exemplar_id')}] 来源={item.get('source_report')} / 章节={item.get('section')} / "
        f"位置={item.get('structural_role')}\n{str(item.get('content') or '')[:700]}"
        for item in compact_exemplar_bank(all_exemplars, limit=24)
    ) or "(没有可用的自然段样例)"
    instruction = (_VARIANT_PROMPT.replace("{type}", topic_type).replace("{reports}", "")
                   + "\n\n" + _STYLE_SAMPLE_NOTE.replace("{style_samples}", ""))
    prompt, _audit = build_prompt_from_sections("style_profile", [
        ContextSection("instruction", [instruction], weight=5, required_items=1),
        ContextSection("report_summaries", report_blocks, weight=3, required_items=1),
        ContextSection("style_exemplars", sample_packet.split("\n\n"), weight=4, required_items=1),
    ], stage_input_budget_tokens("style_profile"))
    payload = _StyleProfileAgent().generate_json(
        prompt, max_tokens=settings.style_profile_output_tokens,
    )
    _validate_profile_payload(payload)
    structure = payload.get("structure") if isinstance(payload.get("structure"), dict) else {}
    # The learner describes a reusable editorial form. It is a hint for the
    # Final Planner, never a frozen directory copied into a new task.
    from app.document_shape import normalize_document_shape
    structure["document_shape"] = normalize_document_shape(structure.get("document_shape"))
    writing = payload.get("writing_style") if isinstance(payload.get("writing_style"), dict) else {}
    writing_patterns = payload.get("writing_patterns") if isinstance(payload.get("writing_patterns"), dict) else {}
    material_realization = payload.get("material_realization") if isinstance(payload.get("material_realization"), dict) else {}
    if material_realization:
        writing_patterns = {**writing_patterns, "material_realization": material_realization}
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
    # A DOCX may be deliberately supplied as an editorial or structural
    # reference. Only explicit layout assets (or automatic mode) may become
    # a Word export base.
    layout_members = _eligible_layout_members(members)
    format_spec = _collect_format(layout_members)
    structure["source_assets"] = _source_asset_profiles(members)
    structure["asset_roles"] = _aggregate_asset_roles(members, structure, format_spec)
    # Editorial-only inputs may have OCR headings, but they do not grant a
    # reusable directory. Keep their discourse value and remove false structure.
    if not structure["asset_roles"].get("structural_reference"):
        structure["sections"] = []
    shape = normalize_document_shape(structure.get("document_shape"))
    # A non-DOCX reference can teach how to write, but it cannot faithfully be
    # reused as a Word layout base. Preserve its editorial value and let the
    # Renderer select a structured default or a clean article document.
    if not format_spec:
        shape["render_base"] = (
            "default_structured"
            if shape["kind"] in {"structured_report", "research_review"}
            else "blank_article"
        )
    structure["document_shape"] = shape
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
            "mode": "FORMAT_ONLY",
            "source": "safe_default",
            "confirmed": False,
            "principle": "模板默认只约束呈现和成文风格，当前报告结构由任务事实与分析决定。",
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


def _asset_role_label(value) -> str:
    return {
        "editorial": "文体参考",
        "structure": "结构参考",
        "layout": "Word 版式母版",
    }.get(str(value or "").strip().lower(), "自动识别")


def _eligible_layout_members(members: list[dict]) -> list[dict]:
    return [
        item for item in members
        if str(item.get("asset_role") or "auto").lower() in {"auto", "layout"}
    ]


def _source_asset_profiles(members: list[dict]) -> list[dict]:
    """Persist source capabilities, not an assumption that every input is DOCX."""
    result: list[dict] = []
    for item in members:
        path = str(item.get("path") or "")
        suffix = Path(path).suffix.lower().lstrip(".")
        headings = item.get("headings") or []
        result.append({
            "filename": str(item.get("filename") or ""),
            "file_type": suffix or "unknown",
            "requested_role": str(item.get("asset_role") or "auto"),
            "can_learn_editorial": bool(item.get("text")),
            "can_reference_structure": bool(headings),
            "can_be_layout_master": suffix == "docx",
        })
    return result


def _aggregate_asset_roles(members: list[dict], structure: dict, format_spec: dict) -> dict:
    assets = _source_asset_profiles(members)
    requested = {str(item.get("requested_role") or "auto") for item in assets}
    has_headings = any(
        bool(item.get("can_reference_structure"))
        and str(item.get("requested_role") or "auto") == "structure"
        for item in assets
    )
    return {
        "editorial_reference": any(bool(item.get("can_learn_editorial")) for item in assets),
        "structural_reference": has_headings or "structure" in requested,
        "layout_master": bool(format_spec) and ("layout" in requested or "auto" in requested),
        "layout_master_available": bool(format_spec),
        "note": _asset_role_note(assets, bool(format_spec)),
    }


def _asset_role_note(assets: list[dict], has_layout_master: bool) -> str:
    if has_layout_master:
        return "该画像包含可复用的原始 DOCX 版式母版。"
    if any(item.get("requested_role") == "layout" for item in assets):
        return "未找到可用的 DOCX 版式母版；该批文件仍会学习文体和结构，Word 导出将使用默认版式。"
    return "非 Word 文件或仅参考用途的 Word 文件不会作为导出母版；Word 导出将按任务选择默认版式。"


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
    schema = dominant.get("template_schema") if isinstance(dominant.get("template_schema"), dict) else {}
    roles = schema.get("style", {}).get("roles", {}) if schema else {}
    learned_fields = sorted(k for k, v in dominant.items() if v not in (None, "", [], {}))
    required = {
        "document.page": schema.get("document", {}).get("page"),
        "document.margins": schema.get("document", {}).get("margins"),
        "role.document_title": roles.get("document_title"),
        "role.heading_1": roles.get("heading_1"),
        "role.body": roles.get("body"),
        "render_contract": schema.get("structure", {}).get("render_contract"),
    }
    missing = [field for field, value in required.items() if value in (None, "", [], {})]
    low_confidence = [field for field, score in confidence.items() if score < 0.67]
    return {
        "learned_fields": learned_fields,
        "missing_critical_fields": missing,
        "conflict_fields": sorted(conflicts.keys()),
        "low_confidence_fields": low_confidence,
        "completeness": round((len(required) - len(missing)) / max(1, len(required)), 2),
        "render_ready": not missing,
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
        exemplar_bank=_loads_samples(row.get("exemplar_bank_json", "[]")),
        learning_cases=_loads_samples(row.get("learning_cases_json", "[]")),
        profile_confidence=_loads(row.get("profile_confidence_json", "{}")),
        profile_version=int(row.get("profile_version") or 1),
        source_reports=_loads_list(row["source_reports"]),
        status=row["status"],
    )


def extract_docx_format(path) -> dict:
    """Compile one DOCX into the only supported template representation."""
    if not str(path).lower().endswith(".docx"):
        return {}
    template_schema = compile_template(path)
    if not template_schema:
        return {}
    _write_template_schema_file(template_schema)
    return {
        "source_template_path": str(path),
        "template_schema": template_schema,
    }


def _looks_like_heading(text: str) -> bool:
    stripped = text.strip()
    return bool(_HEADING_RE.match(stripped)) or len(stripped) <= 25 and stripped.endswith(("：", ":"))


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
