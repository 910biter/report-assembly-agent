"""报告规划、报告与句级内容模型。"""
from dataclasses import dataclass, field


@dataclass
class ReportPlan:
    title: str
    objective: str = ""
    audience: str = ""
    report_type: str = ""
    core_question: str = ""  # 报告要回答的核心问题
    core_judgment: str = ""  # 报告核心判断/主线结论
    narrative_logic: str = ""  # 总体叙事逻辑(章节如何递进)
    structure: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    evidence_needs: list[dict] = field(default_factory=list)  # [{need, dimension, priority}]:待证实信息需求
    required_facts: list[str] = field(default_factory=list)
    budget: dict = field(default_factory=dict)  # ReportBudget: target_words/soft_max_words/hard_max_words/summary_budget
    document_shape: dict = field(default_factory=dict)  # semantic form + visible-heading policy
    # Rendering and composition are independent: a document may hide headings
    # while still needing chaptered generation, or keep optional headings while
    # being written as one continuous article.
    composition_mode: str = "chaptered"  # chaptered/article_beats
    chapter_plans: list[dict] = field(default_factory=list)  # [{title, questions, judgment, relation_to_prev, required_facts, required_inferences, exclude, next_bridge, target_words, importance, evidence_density}]
    user_requirements: str = ""
    plan_stage: str = "analysis"
    plan_version: int = 1
    analysis_plan_json: dict = field(default_factory=dict)
    final_plan_json: dict = field(default_factory=dict)
    id: int | None = None


@dataclass
class Report:
    plan_id: int
    title: str
    style_profile_id: int | None = None
    status: str = "draft"
    id: int | None = None


@dataclass
class ReportSentence:
    report_id: int
    section: str
    position: int
    content: str
    source_level: str
    source_refs: dict = field(default_factory=dict)  # {fact_ids, inference_ids, evidence_ids}
    paragraph: int = 1
    selected: int = 1
    user_edit: str | None = None
    edit_history: list = field(default_factory=list)
    id: int | None = None
