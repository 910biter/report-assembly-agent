"""记忆模型:短期任务上下文、长期机构信息、风格档案。"""
from dataclasses import dataclass, field

_SAMPLE_TYPES = ("opening", "fact", "analysis", "risk", "conclusion")


@dataclass
class StyleVariant:
    """机构风格库中的一个变体(对应一类报告:政策研究/情报快报/周报等)。

    structure/writing_style/terminology/format_spec 为结构化 JSON 字段,
    当前模板中心的主数据结构,聚合文档结构、写作风格、术语与格式画像。
    """

    library_id: int
    name: str = ""
    description: str = ""
    structure: dict = field(default_factory=dict)  # {sections:[{title,children}]} 等
    writing_style: dict = field(default_factory=dict)  # {tone, sentence_pattern, analysis_style}
    terminology: dict = field(default_factory=dict)  # {preferred:[], forbidden:[]}
    format_spec: dict = field(default_factory=dict)  # {dominant:{...}, alternatives:[...]}
    writing_patterns: dict = field(default_factory=dict)
    style_samples: list[dict] = field(default_factory=list)  # [{sample_type, content}]
    chapter_styles: list[dict] = field(default_factory=list)  # [{chapter_type, purpose, rules, examples}]
    reasoning_profile: dict = field(default_factory=dict)  # 分析逻辑层:章节分析框架(输入→输出)/推论展开/风险与建议表达
    institution_rules: dict = field(default_factory=dict)  # 机构规则层:必须出现/禁止出现/字数/推断比例/数据引用
    structure_policy: dict = field(default_factory=dict)  # FORMAT_ONLY / SOFT_STRUCTURE / HARD_STRUCTURE
    exemplar_bank: list[dict] = field(default_factory=list)  # 已审核的材料→成文配对范例
    learning_cases: list[dict] = field(default_factory=list)  # 画像来源及质量等级
    profile_confidence: dict = field(default_factory=dict)  # legacy storage field; new learning evidence lives in structure
    profile_version: int = 1
    source_reports: list[str] = field(default_factory=list)
    status: str = "draft"  # draft / confirmed / locked
    id: int | None = None

    def planner_prompt_block(self) -> str:
        """Expose the canonical structure policy; never infer hard structure."""
        from app.template_policy import resolve_structure_policy, structure_guidance

        structure = self.structure if isinstance(self.structure, dict) else {}
        roles = structure.get("asset_roles") or {}
        policy = resolve_structure_policy(self.structure_policy, roles)
        mode = policy["mode"]
        if mode == "FORMAT_ONLY":
            return f"模板结构策略：{mode}。{structure_guidance(mode)}"
        sections = structure.get("sections") or []
        reference = "、".join(str(item.get("title") or "") for item in sections if isinstance(item, dict))
        if mode == "HARD_STRUCTURE":
            return f"模板结构策略：{mode}。{structure_guidance(mode)}目录：{reference or '以已确认目录为准'}。"
        return (
            f"模板结构策略：{mode}。{structure_guidance(mode)}"
            + (f"参考章节类型：{reference}" if reference else "")
        )

    def writer_prompt_block(self, context: dict | None = None) -> str:
        """Editorial profile plus a few context-relevant, approved exemplars."""
        block = (
            f"机构风格变体「{self.name or '未命名'}」表达参考:\n"
            "只借鉴组织、句式、节奏和材料表达方式。样本的主题、立场、分析对象和结论不是本任务要求；"
            "不能据此补充业务事实、动机或因果。用户要求和当前材料的证据边界优先；不适用的表达可不采用。\n"
        )
        writing = self.writing_style or {}
        if writing:
            block += (
                f"- 语言正式程度: {writing.get('tone', '未指定')}\n"
                f"- 句式习惯: {writing.get('sentence_pattern', '未指定')}\n"
                f"- 样本的分析表达(非本任务论证要求): {writing.get('analysis_style', '未指定')}\n"
            )
        patterns = self.writing_patterns or {}
        if patterns:
            qualitative = {
                key: value for key, value in patterns.items()
                if key != "observed_metrics" and value not in (None, "", [], {})
            }
            if qualitative:
                block += f"- 可复核的行文模式: {json_dumps(qualitative, limit=900)}\n"
            observed = patterns.get("observed_metrics") or {}
            if not observed.get("editorial_contract") and self.exemplar_bank:
                # Existing templates predate the editorial-contract field. Build
                # it at read time so they improve without a destructive relearn.
                from app.memory.style_profile import aggregate_style_metrics

                observed = {**observed, **aggregate_style_metrics(self.exemplar_bank)}
            if observed:
                block += (
                    "- 历史句长统计(节奏参考，不作硬性要求): "
                    f"句长={json_dumps(observed.get('sentence_chars', {}), limit=180)}\n"
                )
                editorial = dict(observed.get("editorial_contract") or {})
                # Parsed text fragments are not confirmed natural paragraphs.
                # Keep learned data, but do not turn fragment length into prose
                # paragraph instructions at runtime.
                if observed.get("measurement_basis") != "natural_paragraphs":
                    editorial.pop("paragraph_rhythm", None)
                if editorial:
                    block += (
                        "- 编辑组织契约(仅指导段落推进，不是固定目录): "
                        f"{json_dumps(editorial, limit=420)}\n"
                    )
        terminology = self.terminology or {}
        if terminology.get("preferred"):
            block += "- 样本惯用表达(仅语境合适且依据充分时借鉴): " + "、".join(terminology["preferred"]) + "\n"
        if terminology.get("forbidden"):
            block += "- 避免表达: " + "、".join(terminology["forbidden"]) + "\n"
        samples = self.select_exemplars({**context, "match_dimension": "expression"}, limit=2) if context else self.style_samples[:2]
        if samples:
            block += "- 所选模板的表达范例(优先相近论证用途；主题不同仍可参考文体，不代表当前论证或事实):\n"
            for sample in samples:
                label = _SAMPLE_TYPES_label(sample.get("sample_type", ""))
                purpose = str(sample.get("purpose") or sample.get("section") or "").strip()
                purpose_text = f" / 用途:{purpose[:70]}" if purpose else ""
                realization = str(sample.get("realization_mode") or "").strip()
                realization_text = f" / 材料运用:{realization[:60]}" if realization else ""
                block += f"  [{label}{purpose_text}{realization_text}] {str(sample.get('content', ''))[:420]}\n"
                negative = str(sample.get("negative_content") or "").strip()
                if negative:
                    block += f"  [避免这种旧表达] {negative[:220]}\n"
        chapter_styles = self.chapter_styles or []
        if chapter_styles:
            block += "- 章节写作规范(不同类型章节写法不同):\n"
            for cs in chapter_styles[:6]:
                block += (
                    f"  {cs.get('chapter_type', '')}: 目的={cs.get('purpose', '')}"
                    f" 规则={str(cs.get('rules', ''))[:80]}\n"
                )
        reasoning = self.reasoning_profile or {}
        if reasoning:
            block += f"- 样本分析方式(仅当前证据支持时参考，不得继承样本结论): {json_dumps(reasoning, limit=600)}\n"
        return block.strip()

    def select_exemplars(self, context: dict, limit: int = 3) -> list[dict]:
        """Select purpose-matched examples without letting style affect evidence."""
        from app.memory.style_profile import select_exemplars

        return select_exemplars(self.exemplar_bank, context, limit=limit)


def _SAMPLE_TYPES_label(sample_type: str) -> str:
    return {
        "opening": "开篇",
        "fact": "事实描述",
        "analysis": "分析判断",
        "risk": "风险研判",
        "conclusion": "结论收尾",
    }.get(sample_type, "范例")


def json_dumps(value, limit: int | None = None) -> str:
    import json

    text = json.dumps(value, ensure_ascii=False)
    if not limit or len(text) <= limit:
        return text
    if isinstance(value, dict):
        selected = {}
        for key, item in value.items():
            candidate = {**selected, key: item}
            if len(json.dumps(candidate, ensure_ascii=False)) <= limit:
                selected[key] = item
        if len(selected) < len(value):
            selected["_omitted_fields"] = len(value) - len(selected)
        return json.dumps(selected, ensure_ascii=False)
    if isinstance(value, list):
        selected = []
        for item in value:
            candidate = [*selected, item]
            if len(json.dumps(candidate, ensure_ascii=False)) <= limit:
                selected.append(item)
        if len(selected) < len(value):
            selected.append({"_omitted_items": len(value) - len(selected)})
        return json.dumps(selected, ensure_ascii=False)
    return json.dumps(str(value)[:max(0, limit - 8)] + "…", ensure_ascii=False)


@dataclass
class ShortMemory:
    task_id: str
    payload: dict = field(default_factory=dict)  # theme/material_ids/stage/plan/facts/inferences/user_edits
