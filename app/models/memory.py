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
    profile_confidence: dict = field(default_factory=dict)  # 分层置信度，不用单一总分掩盖缺失
    profile_version: int = 1
    source_reports: list[str] = field(default_factory=list)
    status: str = "draft"  # draft / confirmed / locked
    id: int | None = None

    def planner_prompt_block(self) -> str:
        """Planner only sees the declared structure policy, never DOCX layout."""
        policy = self.structure_policy or {"mode": "SOFT_STRUCTURE", "source": "inferred"}
        mode = str(policy.get("mode") or "SOFT_STRUCTURE").upper()
        if mode == "FORMAT_ONLY":
            return "模板结构策略：FORMAT_ONLY。模板只控制排版，不得继承其示例目录。"
        sections = (self.structure or {}).get("sections") or []
        reference = "、".join(str(item.get("title") or "") for item in sections if isinstance(item, dict))
        if mode == "HARD_STRUCTURE":
            return f"模板结构策略：HARD_STRUCTURE。必须遵守已确认目录：{reference or '以模板Schema为准'}。"
        return (
            "模板结构策略：SOFT_STRUCTURE。历史目录仅为弱参考，最终结构必须由当前事实、推论和用户目标决定。"
            + (f"参考章节类型：{reference}" if reference else "")
        )

    def writer_prompt_block(self, context: dict | None = None) -> str:
        """Editorial profile plus a few context-relevant, approved exemplars."""
        block = f"机构风格变体「{self.name or '未命名'}」约束:\n"
        block += "- 结构边界: " + self.planner_prompt_block() + "\n"
        writing = self.writing_style or {}
        if writing:
            block += (
                f"- 语言正式程度: {writing.get('tone', '未指定')}\n"
                f"- 句式习惯: {writing.get('sentence_pattern', '未指定')}\n"
                f"- 分析风格: {writing.get('analysis_style', '未指定')}\n"
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
            if observed:
                block += (
                    "- 历史成品统计(仅作为节奏参考,不得机械凑数): "
                    f"段落长度={json_dumps(observed.get('paragraph_chars', {}), limit=180)}; "
                    f"每段句数={json_dumps(observed.get('sentences_per_paragraph', {}), limit=180)}; "
                    f"句长={json_dumps(observed.get('sentence_chars', {}), limit=180)}\n"
                )
        terminology = self.terminology or {}
        if terminology.get("preferred"):
            block += "- 惯用表达: " + "、".join(terminology["preferred"]) + "\n"
        if terminology.get("forbidden"):
            block += "- 避免表达: " + "、".join(terminology["forbidden"]) + "\n"
        samples = self.select_exemplars(context, limit=3) if context else self.style_samples[:3]
        if samples:
            block += "- 匹配当前写作目的的已审核软范例(只借鉴组织与表达，不复制事实):\n"
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
            block += f"- 分析逻辑(本机构如何分析): {json_dumps(reasoning, limit=600)}\n"
        rules = self.institution_rules or {}
        if rules.get("confirmed") is True:
            block += f"- 机构规则(业务约束): {json_dumps(rules, limit=500)}\n"
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
    return text[:limit] + ("…" if limit and len(text) > limit else "")


@dataclass
class ShortMemory:
    task_id: str
    payload: dict = field(default_factory=dict)  # theme/material_ids/stage/plan/facts/inferences/user_edits
