"""核心枚举:来源分级与任务阶段。"""
from enum import StrEnum


class SourceLevel(StrEnum):
    """来源分级(替代置信度)。名称固定,不得改动。"""

    MATERIAL_FACT = "MATERIAL_FACT"  # 材料事实:直接来自材料,可溯源
    MATERIAL_INFERENCE = "MATERIAL_INFERENCE"  # 材料推断:基于材料事实的综合判断
    EXTERNAL_INFORMATION = "EXTERNAL_INFORMATION"  # 非材料来源:模型常识/外部知识


class Stage(StrEnum):
    """任务阶段(Workflow 状态机)。"""

    PARSING = "parsing"
    MATERIAL_ANALYSIS = "material_analysis"
    REQUIREMENT_REVIEW = "requirement_review"
    PLANNING = "planning"
    EVIDENCE = "evidence"
    CONFLICT = "conflict"
    ANALYSIS = "analysis"
    FINAL_PLAN = "final_plan"
    DIRECTORY_REVIEW = "directory_review"
    NARRATIVE = "narrative"
    WRITING = "writing"
    QA = "qa"
    REVIEW = "review"
    DONE = "done"
