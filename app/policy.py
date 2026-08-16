"""Policy layering for report planning, writing, and QA.

Hard guardrails protect truthfulness and provenance. Softer policies describe
preferences that Planner/Writer may adapt to the task, material, and template.
"""
from __future__ import annotations

import json
from typing import Any


def build_report_policy(task: dict | None = None, template_variant: Any = None,
                        profile: dict | None = None) -> dict:
    task = task or {}
    profile = profile or task.get("task_profile") or {}
    template_policy = _template_policy(template_variant)
    domain_policy = _domain_policy(profile)
    return {
        "hard_guardrails": [
            "不得虚构事实、成果、数据、日期、文件名、人员、地点或来源。",
            "事实性句子必须可追溯到 fact_ids;分析判断必须可追溯到 inference_ids 或其依据事实。",
            "不得伪造来源、页码、原文引文或把模板示例当作已发生事实。",
            "证据不足时应明确边界、风险或待补材料,不得用套话补齐缺失事实。",
        ],
        "domain_policy": domain_policy,
        "template_policy": template_policy,
        "planner_policy": [
            "章节数量、章节标题、段落布局和表格/自然段/分项表达由 Planner 结合用户要求、材料信息量、业务域和模板风格决定。",
            "执行性信息可集中呈现,也可拆入相关章节;选择标准是可读性、可执行性和模板一致性,不是固定格式。",
            "同一事实应确定 primary_chapter,其他章节只做必要承接;但 required facts、关键时间、风险事项不得因去重而丢失。",
        ],
        "writer_policy": [
            "表达形式服从 ChapterPlan 和模板风格;清单、表格、短段、长段都是可选手段,不是全局硬规则。",
            "当证据中存在具体日期、文件名、材料名、系统名、数量、渠道时,优先写具体值;证据不明确时不要猜测。",
            "若采用分项表达,尽量让每个事实性分项独立成句并绑定自己的 fact_ids/inference_ids。",
            "除非模板明确采用符号列表,正式报告正文宜使用自然中文句式,避免界面化或 Markdown 化行首。",
        ],
        "qa_policy": [
            "QA 负责提示重复、模糊表达、引用粒度和格式风格风险,不应替代 Planner/Writer 做所有表达决策。",
            "具体化提示只在引用证据中存在明确值时触发;证据本身不明确时不强制具体化。",
        ],
    }


def policy_prompt_block(policy: dict) -> str:
    """Compact policy block for Planner/Writer prompts."""
    if not policy:
        return ""
    labels = {
        "hard_guardrails": "硬性红线",
        "domain_policy": "业务域策略",
        "template_policy": "模板策略",
        "planner_policy": "规划策略",
        "writer_policy": "写作策略",
        "qa_policy": "质检策略",
    }
    lines = ["分层策略:"]
    for key, label in labels.items():
        value = policy.get(key)
        if not value:
            continue
        lines.append(f"{label}:")
        if isinstance(value, dict):
            lines.append(json.dumps(value, ensure_ascii=False))
        else:
            lines.extend(f"- {item}" for item in value)
    return "\n".join(lines)


def _domain_policy(profile: dict) -> list[str]:
    if not profile:
        return []
    policies: list[str] = []
    if profile.get("support_counts"):
        policies.append(
            "材料角色、证据边界和缺失信息均来自本次材料理解;不得套用预置行业假设。"
        )
    if not profile.get("has_actual_evidence", True):
        policies.append("当前未识别到实际过程/结果类证据时,不得生成已完成成果、成效提升或量化业绩。")
    for boundary in profile.get("evidence_boundaries") or []:
        policies.append(str(boundary))
    for rule in profile.get("business_guardrails") or []:
        if rule not in policies:
            policies.append(str(rule))
    return policies


def _template_policy(template_variant: Any) -> dict:
    if not template_variant:
        return {}
    structure = getattr(template_variant, "structure", {}) or {}
    format_spec = getattr(template_variant, "format_spec", {}) or {}
    institution_rules = getattr(template_variant, "institution_rules", {}) or {}
    structure_type = _structure_type(format_spec, institution_rules)
    return {
        "structure_type": structure_type,
        "structure_hint": structure if structure_type != "FORMAT_ONLY" else {},
        "institution_rules": institution_rules,
        "format_summary": _format_summary(format_spec),
        "guidance": (
            "模板只控制格式与导出呈现,不得把模板目录作为报告目录。"
            if structure_type == "FORMAT_ONLY"
            else "模板目录可参考,但最终结构仍由材料事实和分析结论决定。"
            if structure_type == "SOFT_STRUCTURE"
            else "模板目录为强制业务结构,最终报告应严格遵守。"
        ),
    }


def _structure_type(format_spec: dict, institution_rules: dict) -> str:
    candidates = []
    if isinstance(institution_rules, dict):
        candidates.extend([
            institution_rules.get("template_structure_type"),
            institution_rules.get("structure_type"),
        ])
    if isinstance(format_spec, dict):
        candidates.extend([
            format_spec.get("template_structure_type"),
            format_spec.get("structure_type"),
        ])
        dominant = format_spec.get("dominant")
        if isinstance(dominant, dict):
            candidates.extend([
                dominant.get("template_structure_type"),
                dominant.get("structure_type"),
            ])
            schema = dominant.get("template_schema")
            if isinstance(schema, dict):
                candidates.extend([
                    schema.get("structure_type"),
                    schema.get("template_structure_type"),
                ])
    for item in candidates:
        value = str(item or "").upper()
        if value in {"FORMAT_ONLY", "SOFT_STRUCTURE", "HARD_STRUCTURE"}:
            return value
    return "FORMAT_ONLY"


def _format_summary(format_spec: dict) -> dict:
    dominant = format_spec.get("dominant") if isinstance(format_spec, dict) else {}
    document_format = dominant.get("document_format") if isinstance(dominant, dict) else {}
    if isinstance(document_format, dict):
        return {
            "has_template_schema": bool(dominant.get("template_schema")),
            "layout": document_format.get("layout", {}),
            "export_hints": document_format.get("export_hints", {}),
        }
    return {}
