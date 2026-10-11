"""Open-world source-claim semantics shared across evidence and writing.

Classification axes and labels are intentionally extensible. The application
validates the record shape, not the model's domain vocabulary.
"""
from __future__ import annotations

import json
from typing import Any

ASSERTION_MODE_PROTOCOL = frozenset({
    "reported", "prescriptive", "prospective", "conditional", "opinion", "unknown",
})
PROFILE_VERSION = 1


def normalize_assertion_mode(value: object) -> str:
    """Legacy coarse protocol projection; never infer it from domain words."""
    mode = str(value or "").strip().lower() if isinstance(value, str) else ""
    return mode if mode in ASSERTION_MODE_PROTOCOL else "unknown"


def normalize_assertion_mode_raw(value: object, fallback: str = "unknown") -> str:
    text = value.strip() if isinstance(value, str) else ""
    return text[:240] or str(fallback or "unknown")[:240]


def normalize_assertion_semantics(mode_value: object, raw_value: object = None) -> tuple[str, str]:
    """Return the legacy protocol plus its loss-preserving open description."""
    mode = normalize_assertion_mode(mode_value)
    raw = raw_value
    if not isinstance(raw, str) or not raw.strip():
        if isinstance(mode_value, str) and mode == "unknown" and mode_value.strip().lower() != "unknown":
            raw = mode_value
        else:
            raw = mode
    return mode, normalize_assertion_mode_raw(raw, mode)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    elif value is None:
        values = []
    else:
        values = [str(value)]
    result: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text[:500])
    return result


def normalize_semantic_profile(value: object) -> dict:
    """Validate an extensible, multi-axis profile without closing its taxonomy.

    ``facets`` may use any axis and label. Unknown axes and extra source
    descriptors are retained under ``additional`` rather than collapsed.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = {}
    if not isinstance(value, dict):
        return {"schema_version": PROFILE_VERSION, "facets": [], "additional": {}}

    facets: list[dict[str, str]] = []
    raw_facets = value.get("facets")
    if isinstance(raw_facets, list):
        for item in raw_facets:
            if isinstance(item, dict):
                axis = str(item.get("axis") or "unclassified").strip()
                label = str(item.get("label") or "").strip()
                detail = str(item.get("detail") or "").strip()
                if label:
                    facets.append({"axis": axis[:120], "label": label[:240], "detail": detail[:1000]})

    known = {"schema_version", "facets", "additional"}
    additional = dict(value.get("additional")) if isinstance(value.get("additional"), dict) else {}
    for key, raw in value.items():
        if key in known or raw is None or raw == "" or raw == [] or raw == {}:
            continue
        # Accept the common axis->labels shape as well as facets, and preserve
        # future producer fields instead of silently dropping them.
        if isinstance(raw, dict) and ("labels" in raw or "label" in raw):
            labels = _strings(raw.get("labels", raw.get("label")))
            detail = str(raw.get("detail") or "").strip()
            for label in labels:
                facets.append({"axis": str(key)[:120], "label": label[:240], "detail": detail[:1000]})
        elif key in {"conditions", "scope_qualifiers", "qualifiers"}:
            for label in _strings(raw):
                facets.append({"axis": str(key)[:120], "label": label[:240], "detail": ""})
        else:
            additional[str(key)[:120]] = raw

    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for facet in facets:
        key = (facet["axis"], facet["label"], facet["detail"])
        if key not in seen:
            unique.append(facet)
            seen.add(key)
    return {
        "schema_version": PROFILE_VERSION,
        "facets": unique,
        "additional": additional,
    }


def semantic_profile_json(value: object) -> str:
    profile = normalize_semantic_profile(value)
    if not profile["facets"] and not profile["additional"]:
        return "{}"
    return json.dumps(profile, ensure_ascii=False, separators=(",", ":"))


def semantic_profile_line(value: object, mode_value: object = None, raw_value: object = None) -> str:
    profile = normalize_semantic_profile(value)
    if not profile["facets"] and not profile["additional"]:
        mode, raw = normalize_assertion_semantics(mode_value, raw_value)
        profile["facets"] = [{"axis": "legacy_assertion_mode", "label": mode, "detail": raw}]
    return json.dumps(profile, ensure_ascii=False, separators=(",", ":"))


def source_semantics_for_facts(fact_ids: list[int], facts: list[dict] | None) -> list[dict]:
    by_id = {
        int(item["id"]): item
        for item in facts or []
        if isinstance(item, dict) and str(item.get("id", "")).isdigit()
    }
    result = []
    for fact_id in dict.fromkeys(int(value) for value in fact_ids if str(value).isdigit()):
        item = by_id.get(fact_id)
        if not item:
            continue
        result.append({
            "fact_id": fact_id,
            "profile": normalize_semantic_profile(item.get("semantic_profile")),
            "legacy_assertion_mode": normalize_assertion_semantics(
                item.get("assertion_mode"), item.get("assertion_mode_raw"),
            )[0],
            "legacy_assertion_detail": normalize_assertion_semantics(
                item.get("assertion_mode"), item.get("assertion_mode_raw"),
            )[1],
        })
    return result


def source_semantics_line(value: object) -> str:
    """Compactly render inherited fact profiles without losing their open labels."""
    if not isinstance(value, list):
        return "[]"
    rendered = []
    for item in value:
        if not isinstance(item, dict):
            continue
        rendered.append({
            "fact_id": item.get("fact_id"),
            "profile": normalize_semantic_profile(item.get("profile")),
            "legacy_assertion_mode": item.get("legacy_assertion_mode") or "",
            "legacy_assertion_detail": item.get("legacy_assertion_detail") or "",
        })
    return json.dumps(rendered, ensure_ascii=False, separators=(",", ":"))
