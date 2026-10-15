"""A source-bounded reading view of persisted material understanding."""
from __future__ import annotations


def _count(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def build_research_brief(task: dict) -> dict:
    material_ids = list(dict.fromkeys(
        int(value) for value in task.get("material_ids") or [] if str(value).isdigit()
    ))
    selected = set(material_ids)
    insight_by_material = {
        int(item["material_id"]): item
        for item in task.get("material_insights") or []
        if isinstance(item, dict) and str(item.get("material_id") or "").isdigit()
        and int(item["material_id"]) in selected
    }
    insights = [insight_by_material[material_id] for material_id in material_ids if material_id in insight_by_material]
    understood_ids = {int(item["material_id"]) for item in insights}
    parsed = task.get("parse_progress") or {}
    parsing_done = min(len(material_ids), _count(parsed.get("done")))
    context_limited = []
    context_unknown = []
    topics = []
    findings = []
    open_questions = []
    for item in insights:
        material_id = int(item["material_id"])
        source = {"material_id": material_id, "filename": str(item.get("filename") or "")}
        meta = item.get("context_meta") or {}
        if not isinstance(meta, dict):
            meta = {}
        if not meta:
            context_unknown.append(source)
        elif meta.get("truncated") or (
            meta.get("total_units") is not None and meta.get("selected_units") is not None
            and _count(meta["selected_units"]) < _count(meta["total_units"])
        ):
            context_limited.append(source)
        topic = str(item.get("topic") or "").strip()
        if topic and topic not in topics:
            topics.append(topic)
        points = item.get("key_points") if isinstance(item.get("key_points"), list) else []
        for point in points:
            text = str(point).strip()
            if text and not any(row["text"] == text and row["material_id"] == material_id for row in findings):
                findings.append({**source, "text": text})
        missing = item.get("missing_information") if isinstance(item.get("missing_information"), list) else []
        for point in missing:
            text = str(point).strip()
            if text and not any(row["text"] == text and row["material_id"] == material_id for row in open_questions):
                open_questions.append({**source, "text": text})
    total = len(material_ids)
    understood = len(understood_ids)
    analysis_status = task.get("material_analysis_status") or {}
    parsing_done = max(parsing_done, understood)
    unknown_count = len(context_unknown)
    limited_count = len(context_limited)
    not_understood = max(0, total - understood)
    if unknown_count or not_understood:
        completeness = "unknown"
        coverage_note = (
            f"{unknown_count} 份材料的历史理解结果未记录实际阅读范围；"
            f"{not_understood} 份材料尚无理解结果。覆盖完整性无法确认。"
        )
    elif limited_count:
        completeness = "partial"
        coverage_note = f"{limited_count} 份材料的理解输入未覆盖全部单元，当前理解不完整。"
    else:
        completeness = "known"
        coverage_note = "已记录的材料理解输入覆盖了各材料单元；具体事实仍需回到原文核验。"
    return {
        "focus": str(task.get("theme") or "").strip(),
        "topic_candidates": topics[:6],
        "findings": findings[:8],
        "open_questions": open_questions[:8],
        "representative_materials": [
            {"material_id": int(item["material_id"]),
             "filename": str(item.get("filename") or ""),
             "topic": str(item.get("topic") or "")}
            for item in insights[:5]
        ],
        "coverage": {
            "selected": total,
            "parsed": parsing_done,
            "understood": understood,
            "not_understood": not_understood,
            "limited_context": limited_count,
            "limited_materials": context_limited[:8],
            "unknown_context": unknown_count,
            "completeness": completeness,
            "coverage_note": coverage_note,
            "analysis_status": str(analysis_status.get("status") or "pending"),
        },
        "preliminary": understood < total or bool(context_limited) or bool(context_unknown),
    }
