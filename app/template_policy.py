"""Single source of truth for how a learned template may shape a report."""
from __future__ import annotations


_EXPLICIT_SOURCES = {"user", "human", "user_confirmed"}


def resolve_structure_policy(policy: dict | None, asset_roles: dict | None = None) -> dict:
    """Resolve template structure authority without inferring a hard constraint."""
    policy = policy if isinstance(policy, dict) else {}
    asset_roles = asset_roles if isinstance(asset_roles, dict) else {}
    requested = str(policy.get("mode") or "").strip().upper()
    source = str(policy.get("source") or "").strip().lower()
    confirmed = policy.get("confirmed") is True

    if requested == "HARD_STRUCTURE" and confirmed and source in _EXPLICIT_SOURCES:
        mode = "HARD_STRUCTURE"
    elif confirmed and requested in {"FORMAT_ONLY", "SOFT_STRUCTURE"}:
        mode = requested
    elif asset_roles.get("structural_reference"):
        mode = "SOFT_STRUCTURE"
    else:
        mode = "FORMAT_ONLY"

    return {
        "mode": mode,
        "source": source if confirmed else "uploaded_asset_roles",
        "confirmed": confirmed,
        "principle": (
            "只有用户明确确认的业务目录才是强约束；结构参考默认为弱参考，"
            "版式母版和文体样本不决定报告目录。"
        ),
    }


def structure_guidance(mode: str) -> str:
    mode = str(mode or "FORMAT_ONLY").upper()
    if mode == "HARD_STRUCTURE":
        return "用户已确认模板目录为强制业务结构，必须遵守确认后的目录。"
    if mode == "SOFT_STRUCTURE":
        return (
            "结构参考目录仅供参考，不得把模板示例目录直接作为新报告目录；"
            "最终结构由当前材料、分析结论和用户目标决定。"
        )
    return "模板只控制格式与呈现，不得把模板示例目录作为报告目录。"
