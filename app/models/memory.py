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
    source_reports: list[str] = field(default_factory=list)
    status: str = "draft"  # draft / confirmed / locked
    id: int | None = None

    def to_prompt_block(self) -> str:
        """注入 Writer 的风格约束:结构规则 + 语言规则 + 术语 + 分类型写作范例。"""
        block = f"机构风格变体「{self.name or '未命名'}」约束:\n"
        structure = self.structure or {}
        sections = structure.get("sections", [])
        if sections:
            block += "- 章节结构:\n" + "\n".join(
                f"  {s.get('title', '')}" + (f"({', '.join(s.get('children', []))})" if s.get("children") else "")
                for s in sections
            )
        else:
            block += f"- 章节结构: {structure.get('pattern', '未指定') or '未指定'}\n"
        if structure.get("summary_first") is not None:
            block += f"- 是否含摘要: {'是' if structure['summary_first'] else '否'}; 是否先结论后展开: {'是' if structure.get('conclusion_first') else '否'}\n"
        writing = self.writing_style or {}
        if writing:
            block += (
                f"- 语言正式程度: {writing.get('tone', '未指定')}\n"
                f"- 句式习惯: {writing.get('sentence_pattern', '未指定')}\n"
                f"- 分析风格: {writing.get('analysis_style', '未指定')}\n"
            )
        terminology = self.terminology or {}
        if terminology.get("preferred"):
            block += "- 惯用表达: " + "、".join(terminology["preferred"]) + "\n"
        if terminology.get("forbidden"):
            block += "- 避免表达: " + "、".join(terminology["forbidden"]) + "\n"
        samples = self.style_samples or []
        if samples:
            block += "- 行文范例(模仿其句式与用词):\n"
            for sample in samples:
                label = _SAMPLE_TYPES_label(sample.get("sample_type", ""))
                block += f"  [{label}] {str(sample.get('content', ''))[:120]}\n"
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
        if rules:
            block += f"- 机构规则(业务约束): {json_dumps(rules, limit=500)}\n"
        return block.strip()


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
