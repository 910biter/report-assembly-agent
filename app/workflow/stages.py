"""Canonical workflow stage protocol shared by orchestration and UI context.

The values in this module are workflow protocol values, not domain semantics.
Presentation layers may group stages for readability, but they must not invent
another execution order or rename a persisted stage.
"""
from __future__ import annotations

from app.models.enums import Stage


STAGE_ORDER: tuple[Stage, ...] = (
    Stage.PARSING,
    Stage.MATERIAL_ANALYSIS,
    Stage.REQUIREMENT_REVIEW,
    Stage.PLANNING,
    Stage.EVIDENCE,
    Stage.CONFLICT,
    Stage.ANALYSIS,
    Stage.FINAL_PLAN,
    Stage.DIRECTORY_REVIEW,
    Stage.NARRATIVE,
    Stage.WRITING,
    Stage.QA,
    Stage.REVIEW,
    Stage.DONE,
)

STAGE_LABELS: dict[str, str] = {
    "created": "等待开始",
    "parsing": "材料解析",
    "material_analysis": "材料理解",
    "requirement_review": "需求讨论",
    "planning": "分析规划",
    "evidence": "事实与证据",
    "conflict": "冲突核验",
    "analysis": "综合分析",
    "final_plan": "目录生成",
    "directory_review": "目录讨论",
    "narrative": "成文组织",
    "writing": "报告生成",
    "qa": "质量检查",
    "review": "等待审核",
    "done": "已完成",
    "paused": "已暂停",
    "failed": "运行异常",
    "queued": "排队中",
    "running": "运行中",
}

_WORKFLOW_MAP: tuple[dict[str, str], ...] = (
    {"stage": "parsing", "purpose": "将材料转换为带来源位置的内容单元", "artifact": "Units"},
    {"stage": "material_analysis", "purpose": "判断材料角色、可证明范围和信息缺口", "artifact": "MaterialInsight"},
    {"stage": "requirement_review", "purpose": "基于材料理解与用户共同确定报告目标和要求", "artifact": "TaskRequirements"},
    {"stage": "planning", "purpose": "规划需要回答的问题和证据需求", "artifact": "AnalysisPlan"},
    {"stage": "evidence", "purpose": "提取事实并绑定原始证据", "artifact": "Fact/Evidence"},
    {"stage": "conflict", "purpose": "核验多来源对同一事项的矛盾", "artifact": "Conflict"},
    {"stage": "analysis", "purpose": "基于事实形成带依据和置信度的推论", "artifact": "Inference"},
    {"stage": "final_plan", "purpose": "根据事实和推论形成最终内容结构", "artifact": "FinalReportPlan"},
    {"stage": "directory_review", "purpose": "让用户审阅并调整最终目录", "artifact": "FinalReportPlan"},
    {"stage": "narrative", "purpose": "组织章节主线、话题和逻辑顺序", "artifact": "NarrativePlan"},
    {"stage": "writing", "purpose": "按叙事计划自然成文并绑定来源", "artifact": "Report/ChapterDraft"},
    {"stage": "qa", "purpose": "发现事实、结构、语言和格式问题", "artifact": "QAResult"},
)


def stage_rank(stage: Stage | str | None) -> int:
    """Return a stable ordinal for checkpoint resume decisions."""
    value = stage.value if isinstance(stage, Stage) else str(stage or "")
    try:
        return STAGE_ORDER.index(Stage(value)) + 1
    except (ValueError, TypeError):
        return 0


def workflow_map() -> list[dict[str, str]]:
    """Return copies so callers cannot mutate the canonical protocol."""
    return [dict(item) for item in _WORKFLOW_MAP]
