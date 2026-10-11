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
_STYLE_LEARNER_VERSION = "editorial-v7-transferable-style"
_DEFAULT_PROFILE_NAME = "综合报告风格"
_ASSET_ROLE_ORDER = ("editorial", "structure", "layout")
_ASSET_ROLE_LABELS = {
    "editorial": "文体参考",
    "structure": "结构参考",
    "layout": "Word 版式母版",
}
_REALIZATION_KEYS = (
    "fact_expression", "judgment_expression", "fact_judgment_transition",
    "information_compression", "attribution_style",
)


# ---------- 风格库(v2):逐份分析 → 聚类 → 变体 ----------

_HEADING_RE = re.compile(r"^(第?[一二三四五六七八九十百]+[章节部分]、|\([一二三四五六七八九十]+\)|\d+[.、])\s*\S+")

_FEATURE_PROMPT = """分析以下报告的体裁与风格特征,严格输出 JSON(不要任何解释):
{
  "topic_type": "用简短中文名概括报告体裁，只填写结果，不要复述字段说明或示例",
  "document_shape": "模型根据样例概括的开放文档形态标签；无法确定时写 unknown",
  "raw_document_shape": "模型原始判断，必须保留",
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
    # Validate the value as a compact identifier only; its meaning is learned
    # from the current reference set rather than a fixed business vocabulary.
    if (
        not label
        or len(label) > 24
        or any(char in label for char in "{}[]")
        or label.count("/") >= 2
    ):
        return fallback
    return label


def normalize_asset_roles(value) -> list[str]:
    """Normalize one source's explicit capabilities into a stable role list.

    The old scalar ``auto`` value is treated as editorial-only: it must never
    silently grant structural authority or become a Word export template.
    """
    if isinstance(value, dict):
        value = value.get("roles") or value.get("asset_roles")
    if value is None or value == "":
        values = ["editorial"]
    elif isinstance(value, str):
        values = ["editorial"] if value.strip().lower() == "auto" else [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        raise ValueError("INVALID_ASSET_ROLES")
    normalized = {str(item or "").strip().lower() for item in values if str(item or "").strip()}
    if not normalized or normalized - set(_ASSET_ROLE_ORDER):
        raise ValueError("INVALID_ASSET_ROLES")
    return [role for role in _ASSET_ROLE_ORDER if role in normalized]


def validate_asset_role_assignments(filenames: list[str], raw_roles_json: str) -> list[list[str]]:
    """Validate the one-to-one file/role contract for a learning submission."""
    try:
        submitted = json.loads(raw_roles_json)
    except (TypeError, ValueError) as exc:
        raise ValueError("INVALID_ASSET_ROLES_JSON") from exc
    if not isinstance(submitted, list) or len(submitted) != len(filenames):
        raise ValueError("ASSET_ROLE_COUNT_MISMATCH")
    roles_by_file = [normalize_asset_roles(roles) for roles in submitted]
    layout_indices = [i for i, roles in enumerate(roles_by_file) if "layout" in roles]
    if len(layout_indices) > 1:
        raise ValueError("ONLY_ONE_LAYOUT_MASTER_ALLOWED")
    if layout_indices and Path(str(filenames[layout_indices[0]] or "")).suffix.lower() != ".docx":
        raise ValueError("LAYOUT_MASTER_MUST_BE_DOCX")
    return roles_by_file


def _member_roles(member: dict) -> list[str]:
    return normalize_asset_roles(member.get("asset_roles", member.get("asset_role")))


def _validate_profile_payload(payload: dict, *, require_editorial: bool = True) -> None:
    if not require_editorial:
        return
    if not isinstance(payload.get("writing_style"), dict) or not payload.get("writing_style"):
        raise ValueError("STYLE_PROFILE_WRITING_STYLE_EMPTY")
    patterns = payload.get("writing_patterns")
    if not isinstance(patterns, dict) or not patterns:
        raise ValueError("STYLE_PROFILE_WRITING_PATTERNS_EMPTY")
    if sum(bool(patterns.get(key)) for key in _REALIZATION_KEYS) < 3:
        raise ValueError("STYLE_PROFILE_CONTENT_REALIZATION_INCOMPLETE")

_VARIANT_PROMPT = """以下是一次提交的模板学习来源，画像名称参考类型为 {type}，来源数为 {count}。

按来源用途提炼可观察的写作特征：文体参考用于语言、段落组织、文档形态的软信号与材料表达方式；结构参考用于标题层级与组织模式；Word 版式母版由程序从 DOCX 提取格式。样例中的业务事实只用于理解表达方式，未来报告的目录由任务目标和证据决定。缺少某项用途时，该用途字段留空或写 unknown。

{reports}

请根据上述用途提炼该类型报告的写作规范，严格输出以下 JSON：
{{
  "name": "报告类型名(沿用 {type})",
  "structure": {{
    "sections": [{{"title": "一、…", "children": ["二级标题…"]}}],
    "summary_first": true或false,
    "conclusion_first": true或false,
    "document_shape": {{
      "kind": "模型根据样例概括的开放文档形态标签；无法确定时写 unknown",
      "raw_kind": "模型的原始形态判断，必须保留",
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
    "transferable_style": {{
      "voice": "跨题材可复用的正式度与语气力度表达特征，不包含样例立场",
      "sentence_rhythm": "跨题材可复用的句长、节奏和句间连接方式，不含样例主体与结论",
      "paragraph_progression": "跨题材可复用的段落推进方式，不含样例事件或固定章节",
      "fact_expression": "如何在句中安放时间、主体、数值与来源，不含样例事实",
      "judgment_expression": "如何从证据过渡到解释并控制确定性，不继承样例立场",
      "attribution_style": "来源与判断主体如何归属，不含样例机构或人名"
    }},
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
    "fact_to_analysis": "事实如何支撑解释、判断、风险或建议，区分相关性与可证实的因果关系",
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
  "sample_annotations": [
    {{"sample_id": "输入中的样例编号", "sample_type": "模型判断的开放表达功能标签或 unknown", "rhetorical_role": "模型判断的开放修辞角色或 unknown", "purpose": "该段承担的表达任务", "realization_mode": "模型判断的开放实现方式", "discourse_moves": ["模型识别的论述动作"], "tags": ["可检索语义标签"]}}
  ]
}}"""

_STYLE_SAMPLE_NOTE = """以下是按章节与自然段抽取的候选样例。请提炼句式、节奏、论证推进与材料表达方式；业务事实仅作为样例语境：
{style_samples}
"""

def analyze_library(
    reports: list[dict],
    force: bool = False,
    progress_callback=None,
    owner_user_id: int | None = None,
) -> list[StyleVariant]:
    """从参考报告构建机构风格库(幂等:同一文件默认只产生一个稳定变体)。

    reports: [{"filename": str, "text": str, "path": str|None}]
    流程:逐份提取结构+多位置采样 → 按文件 hash 查已分析记录(有则复用,
    不重复生成)→ LLM 判定体裁 → 按体裁聚类 → 每组 LLM 提炼变体 → 落库。
    force=True 时忽略已有记录重新分析(显式"重新分析"才建新版本)。
    """
    if not reports:
        raise ValueError("NO_HISTORICAL_REPORTS")
    _require_owner(owner_user_id)
    library_id = _ensure_library()

    # 幂等:同一文件(hash)已分析过 → 直接复用已有变体(LLM 分类不稳定不改变模板身份)
    if not force:
        reused = _reuse_by_source_hash(reports, owner_user_id=owner_user_id)
        if reused is not None:
            _emit_progress(progress_callback, "profiling", 1, 1, "复用已学习画像")
            return reused

    features = []
    for report_index, report in enumerate(reports, start=1):
        text = report.get("text", "")
        roles = normalize_asset_roles(report.get("asset_roles", report.get("asset_role")))
        headings = _extract_headings(text, report.get("path"))
        samples = _sample_report(text)
        topic_type = _DEFAULT_PROFILE_NAME
        probe_audit = {}
        probe_attempted = bool({"editorial", "structure"}.intersection(roles))
        if probe_attempted:
            try:
                prompt, probe_audit = build_prompt_from_sections("style_probe", [
                    ContextSection("instruction", ["分析以下来源的报告体裁，不推断版式或强制目录。"], weight=5, required_items=1),
                    ContextSection("report_paragraphs", [item for item in text.splitlines() if item.strip()], weight=3, required_items=1),
                ], stage_input_budget_tokens("style_probe"))
                payload = _StyleProbeAgent().generate_json(
                    prompt, max_tokens=settings.style_probe_output_tokens,
                )
                topic_type = _normalize_profile_label(payload.get("topic_type"))
            except Exception:
                pass
        features.append({
            "filename": report.get("filename", ""),
            "text": text,
            "path": report.get("path"),
            "asset_roles": roles,
            "headings": headings,
            "samples": samples,
            "topic_type": topic_type,
            "topic_probe_attempted": probe_attempted,
            "topic_probe_audit": probe_audit,
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
    variants = [_build_variant(library_id, topic_type, features, owner_user_id=owner_user_id)]
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
            roles = normalize_asset_roles(report.get("asset_roles", report.get("asset_role")))
            digest.update(",".join(roles).encode())
            return digest.hexdigest()[:16]
    except Exception:
        pass
    roles = normalize_asset_roles(report.get("asset_roles", report.get("asset_role")))
    fallback = f"{report.get('filename') or ''}:{','.join(roles)}:{_STYLE_LEARNER_VERSION}"
    return hashlib.sha256(fallback.encode()).hexdigest()[:16]


def _require_owner(owner_user_id: int | None) -> None:
    if settings.multiuser_enabled and owner_user_id is None:
        raise ValueError("TEMPLATE_OWNER_REQUIRED")


def owner_for_task(task_id: str) -> int | None:
    if not settings.multiuser_enabled:
        return None
    task_table = Base.metadata.tables["short_memory"]
    with session_scope() as s:
        owner = s.execute(select(task_table.c.owner_user_id).where(
            task_table.c.task_id == task_id,
        )).scalar_one_or_none()
    _require_owner(owner)
    return owner


def _reuse_by_source_hash(reports: list[dict], owner_user_id: int | None = None) -> list[StyleVariant] | None:
    """同一源文件已分析过 → 复用已有变体(返回 None 表示需新分析)。"""
    if len(reports) != 1:
        return None
    _require_owner(owner_user_id)
    digest = _source_hash(reports[0])
    with session_scope() as s:
        query = select(ORMVariant.c.id).where(
                ORMVariant.c.source_hash == digest,
                ORMVariant.c.status != "deleted",  # 已被删除的旧变体不复用,重新学习应能重建
            )
        if owner_user_id is not None:
            query = query.where(ORMVariant.c.owner_user_id == owner_user_id)
        row = s.execute(query.order_by(ORMVariant.c.id).limit(1)).mappings().first()
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


def _build_variant(library_id: int, topic_type: str, members: list[dict],
                   owner_user_id: int | None = None) -> StyleVariant:
    """Build one aggregate profile while keeping each source's declared role isolated."""
    editorial_members = [item for item in members if "editorial" in _member_roles(item)]
    structure_members = [item for item in members if "structure" in _member_roles(item)]
    structure_evidence_members = [item for item in structure_members if item.get("headings")]
    layout_members = _eligible_layout_members(members)
    all_exemplars = build_report_exemplars(editorial_members)
    exemplar_bank = compact_exemplar_bank(all_exemplars, limit=200)

    editorial_blocks = [
        f"[{member['filename']}]\n开篇: {member['samples']['opening'][:150]}\n"
        f"正文: {member['samples']['middle'][:150]}\n结尾: {member['samples']['ending'][:150]}"
        for member in editorial_members
    ]
    structure_blocks = []
    for member in structure_evidence_members:
        heading_lines = []
        for heading in member.get("headings") or []:
            level = int(heading.get("level", 1)) if isinstance(heading, dict) else 1
            text = heading.get("text", "") if isinstance(heading, dict) else str(heading)
            heading_lines.append("  " * max(0, level - 1) + "- " + str(text))
        structure_blocks.append(
            f"[{member['filename']}]\n" + ("\n".join(heading_lines) or "(未识别到标题层级)")
        )

    exemplar_items = compact_exemplar_bank(all_exemplars, limit=24)
    sample_packet = [
        f"[{item.get('exemplar_id')}] 来源={item.get('source_report')} / 章节={item.get('section')} / "
        f"位置={item.get('structural_role')}\n{str(item.get('content') or '')[:700]}"
        for item in exemplar_items
    ]
    has_semantic_input = bool(editorial_members or structure_evidence_members)
    audit = {
        "stage": "style_profile",
        "budget_tokens": int(stage_input_budget_tokens("style_profile")),
        "actual_tokens": 0,
        "truncated": False,
        "sections": {},
    }
    payload: dict = {"name": topic_type}
    if has_semantic_input:
        instruction = (
            _VARIANT_PROMPT.replace("{type}", topic_type)
            .replace("{count}", str(len(members)))
            .replace("{reports}", "")
            + "\n\n"
            + (_STYLE_SAMPLE_NOTE.replace("{style_samples}", "") if editorial_members else "")
        )
        sections = [
            ContextSection("instruction", [instruction], weight=5, required_items=1),
            ContextSection("editorial_reference_samples", editorial_blocks, weight=3, required_items=1),
            ContextSection("structural_reference_headings", structure_blocks, weight=4, required_items=1),
            ContextSection("approved_editorial_exemplars", sample_packet, weight=4, required_items=1),
        ]
        prompt, audit = build_prompt_from_sections(
            "style_profile", sections, stage_input_budget_tokens("style_profile"),
        )
        payload = _StyleProfileAgent().generate_json(
            prompt, max_tokens=settings.style_profile_output_tokens,
        )
        _validate_profile_payload(payload, require_editorial=bool(editorial_members))

    structure = payload.get("structure") if isinstance(payload.get("structure"), dict) else {}
    from app.document_shape import normalize_document_shape
    if not has_semantic_input:
        structure["document_shape"] = normalize_document_shape({
            "kind": "unknown",
            "raw_kind": "unknown",
            "render_base": "selected_template" if layout_members else "blank_article",
        })
    else:
        structure["document_shape"] = normalize_document_shape(structure.get("document_shape"))
    writing = payload.get("writing_style") if isinstance(payload.get("writing_style"), dict) else {}
    writing_patterns = payload.get("writing_patterns") if isinstance(payload.get("writing_patterns"), dict) else {}
    material_realization = payload.get("material_realization") if isinstance(payload.get("material_realization"), dict) else {}
    if material_realization:
        writing_patterns = {**writing_patterns, "material_realization": material_realization}
    terminology = payload.get("terminology") if isinstance(payload.get("terminology"), dict) else {}
    chapter_styles = payload.get("chapter_styles") if isinstance(payload.get("chapter_styles"), list) else []
    reasoning = payload.get("reasoning_profile") if isinstance(payload.get("reasoning_profile"), dict) else {}
    # A model may describe observed patterns, but it cannot confirm business
    # rules from examples; only explicit user-confirmed rules are enforceable.
    institution_rules = {}
    annotations = payload.get("sample_annotations") if isinstance(payload.get("sample_annotations"), list) else []
    exemplar_bank = annotate_exemplars(exemplar_bank, annotations)
    observed_metrics = aggregate_style_metrics(all_exemplars)
    if observed_metrics:
        writing_patterns = {**writing_patterns, "observed_metrics": observed_metrics}
    samples = [
        {"sample_type": item.get("sample_type", "fact"), "content": item.get("content", "")}
        for item in exemplar_bank[:12]
    ] or _collect_samples(editorial_members)

    # A structural or layout source cannot silently train language behavior.
    if not editorial_members:
        writing = {}
        writing_patterns = {key: value for key, value in writing_patterns.items() if key == "observed_metrics"}
        terminology = {}
        chapter_styles = []
        reasoning = {}
        institution_rules = {}
        exemplar_bank = []
        samples = []
    if not structure_evidence_members:
        for key in ("sections", "summary_first", "conclusion_first", "heading_patterns"):
            structure.pop(key, None)
        structure["sections"] = []

    format_spec = _collect_format(layout_members)
    structure["source_assets"] = _source_asset_profiles(members)
    asset_roles = _aggregate_asset_roles(members, structure, format_spec)
    structure["asset_roles"] = asset_roles
    shape = normalize_document_shape(structure.get("document_shape"))
    if not format_spec:
        shape["render_base"] = (
            "default_structured"
            if shape["kind"] in {"structured_report", "research_review"}
            else "blank_article"
        )
    structure["document_shape"] = shape
    role_counts = {
        role: sum(role in _member_roles(item) for item in members)
        for role in _ASSET_ROLE_ORDER
    }
    probe_audits = [
        item.get("topic_probe_audit") or {} for item in members
        if item.get("topic_probe_attempted")
    ]
    structure["learning_evidence"] = {
        "source_count": len(members),
        "role_source_counts": role_counts,
        "sources": [
            {
                "filename": str(item.get("filename") or ""),
                "roles": _member_roles(item),
                "heading_count": len(item.get("headings") or []) if "structure" in _member_roles(item) else 0,
                "editorial_sample_count": sum(bool(value) for value in (item.get("samples") or {}).values())
                if "editorial" in _member_roles(item) else 0,
                "topic_probe": item.get("topic_probe_audit") or {},
            }
            for item in members
        ],
        "editorial": {
            "source_count": len(editorial_members),
            "candidate_paragraphs": len(all_exemplars),
            "stored_examples": len(exemplar_bank),
            "paragraph_metric_samples": int(observed_metrics.get("sample_count") or 0),
        },
        "structure": {
            "source_count": len(structure_members),
            "observed_headings": sum(len(item.get("headings") or []) for item in structure_members),
        },
        "topic_probe": {
            "attempts": sum(bool(item.get("topic_probe_attempted")) for item in members),
            "actual_tokens": sum(int(item.get("actual_tokens") or 0) for item in probe_audits),
            "budget_tokens": sum(int(item.get("budget_tokens") or 0) for item in probe_audits),
            "truncated_sources": sum(bool(item.get("truncated")) for item in probe_audits),
        },
        "layout": {
            "source_count": len(layout_members),
            "master_file": str(layout_members[0].get("filename") or "") if layout_members and format_spec else "",
            "extracted": bool(format_spec),
        },
        "profile_prompt": {
            "called": has_semantic_input,
            "tokenizer_method": audit.get("tokenizer_method", "not_called"),
            "budget_tokens": audit.get("budget_tokens", 0),
            "actual_tokens": audit.get("actual_tokens", 0),
            "truncated": bool(audit.get("truncated")),
            "sections": audit.get("sections", {}),
        },
    }
    from app.template_policy import resolve_structure_policy
    structure_policy = resolve_structure_policy({}, asset_roles)
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
        structure_policy=structure_policy,
        profile_confidence={},
        source_reports=[m["filename"] for m in members],
        status="draft",
    )
    return save_variant(variant, owner_user_id=owner_user_id)


def _eligible_layout_members(members: list[dict]) -> list[dict]:
    return [item for item in members if "layout" in _member_roles(item)]


def _source_asset_profiles(members: list[dict]) -> list[dict]:
    """Persist source capabilities, not an assumption that every input is DOCX."""
    result: list[dict] = []
    for item in members:
        path = str(item.get("path") or "")
        suffix = Path(path).suffix.lower().lstrip(".")
        headings = item.get("headings") or []
        roles = _member_roles(item)
        result.append({
            "filename": str(item.get("filename") or ""),
            "file_type": suffix or "unknown",
            "roles": roles,
            "can_learn_editorial": "editorial" in roles and bool(item.get("text")),
            "can_reference_structure": "structure" in roles and bool(headings),
            "can_be_layout_master": "layout" in roles and suffix == "docx",
        })
    return result


def _aggregate_asset_roles(members: list[dict], structure: dict, format_spec: dict) -> dict:
    assets = _source_asset_profiles(members)
    requested = {role for item in assets for role in item.get("roles", [])}
    structure_count = sum("structure" in item.get("roles", []) for item in assets)
    layout_count = sum("layout" in item.get("roles", []) for item in assets)
    editorial_count = sum("editorial" in item.get("roles", []) for item in assets)
    heading_count = sum(
        len(member.get("headings") or [])
        for member in members if "structure" in _member_roles(member)
    )
    dominant = format_spec.get("dominant") if isinstance(format_spec, dict) else {}
    dominant = dominant if isinstance(dominant, dict) else {}
    has_layout = bool(dominant.get("template_schema") or dominant.get("source_template_path"))
    return {
        "role_assignment_known": True,
        "editorial_reference": any(bool(item.get("can_learn_editorial")) for item in assets),
        "structural_reference": structure_count > 0,
        "structural_reference_available": heading_count > 0,
        "layout_master": has_layout,
        "layout_master_available": has_layout,
        "role_source_counts": {
            "editorial": editorial_count,
            "structure": structure_count,
            "layout": layout_count,
        },
        "observed_heading_count": heading_count,
        "note": _asset_role_note(requested, has_layout, heading_count),
    }


def _asset_role_note(roles: set[str], has_layout_master: bool, heading_count: int) -> str:
    if has_layout_master:
        return "已使用明确选定的 DOCX 母版；版式字段来自原始文档解析。"
    notes = []
    if "layout" not in roles:
        notes.append("未指定版式母版，Word 导出使用任务默认版式")
    if "structure" in roles and not heading_count:
        notes.append("已指定结构参考，但未识别到标题层级")
    return "；".join(notes) or "文体、结构和版式按所选来源用途分别处理。"


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
    """Extract the one explicitly chosen DOCX master without silent ranking."""
    if not members:
        return {}
    if len(members) != 1:
        raise ValueError("ONLY_ONE_LAYOUT_MASTER_ALLOWED")
    member = members[0]
    source = str(member.get("path") or "")
    if Path(source).suffix.lower() != ".docx":
        raise ValueError("LAYOUT_MASTER_MUST_BE_DOCX")
    source_path = _persist_template_source(source)
    dominant = extract_docx_format(source_path)
    if not dominant:
        raise ValueError("LAYOUT_MASTER_EXTRACTION_FAILED")
    conflicts = {}
    return {
        "dominant": dominant,
        "alternatives": [],
        "conflicts": conflicts,
        "template_profile": _template_profile(dominant, conflicts),
        "selection_strategy": {
            "mode": "explicit_single_master",
            "source": str(member.get("filename") or Path(source).name),
            "principle": "只使用用户明确指定的这一份原始 DOCX；不跨文档合并或自动择优。",
        },
        "sample_count": 1,
    }


def _template_profile(dominant: dict, conflicts: dict) -> dict:
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
    return {
        "learned_fields": learned_fields,
        "missing_critical_fields": missing,
        "conflict_fields": sorted(conflicts.keys()),
        "completeness": round((len(required) - len(missing)) / max(1, len(required)), 2),
        "render_ready": not missing,
        "recommendation": "可用于正式导出" if not missing else "建议人工复核缺失的模板字段",
    }


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


def save_variant(variant: StyleVariant, *, owner_user_id: int | None = None) -> StyleVariant:
    _require_owner(owner_user_id)
    with session_scope() as s:
        result = s.execute(
            ORMVariant.insert().values(
                owner_user_id=owner_user_id,
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


def get_variant(variant_id: int, *, owner_user_id: int | None = None) -> StyleVariant | None:
    with session_scope() as s:
        query = select(ORMVariant).where(ORMVariant.c.id == variant_id)
        if owner_user_id is not None:
            query = query.where(ORMVariant.c.owner_user_id == owner_user_id)
        row = s.execute(query).mappings().first()
    return _row_to_variant(dict(row)) if row else None


def list_variants(owner_user_id: int | None = None) -> list[StyleVariant]:
    _require_owner(owner_user_id)
    with session_scope() as s:
        query = select(ORMVariant).where(ORMVariant.c.status != "deleted")
        if owner_user_id is not None:
            query = query.where(ORMVariant.c.owner_user_id == owner_user_id)
        rows = s.execute(query.order_by(ORMVariant.c.id.desc())).mappings().all()
    return [_row_to_variant(dict(row)) for row in rows]


def get_locked_variant(owner_user_id: int | None = None) -> StyleVariant | None:
    _require_owner(owner_user_id)
    with session_scope() as s:
        query = select(ORMVariant).where(ORMVariant.c.status == "locked")
        if owner_user_id is not None:
            query = query.where(ORMVariant.c.owner_user_id == owner_user_id)
        row = s.execute(query.order_by(ORMVariant.c.id.desc()).limit(1)).mappings().first()
    return _row_to_variant(dict(row)) if row else None


def set_variant_status(variant_id: int, status: str) -> None:
    with session_scope() as s:
        if status == "locked":
            row = s.execute(
                select(ORMVariant.c.library_id, ORMVariant.c.owner_user_id).where(
                    ORMVariant.c.id == variant_id,
                )
            ).mappings().first()
            if row is not None:
                query = ORMVariant.update().where(
                    ORMVariant.c.library_id == row["library_id"],
                    ORMVariant.c.status == "locked",
                    ORMVariant.c.id != variant_id,
                )
                if row["owner_user_id"] is not None:
                    query = query.where(ORMVariant.c.owner_user_id == row["owner_user_id"])
                s.execute(query.values(status="confirmed"))
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
