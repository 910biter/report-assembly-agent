"""Domain-neutral document-shape contract.

The workflow may use semantic units internally for retrieval and provenance,
but whether those units become visible headings is an editorial decision. This
module keeps that decision explicit across planning, writing, API rendering,
and DOCX export.
"""
from __future__ import annotations

from typing import Any


_KINDS = {
    "structured_report", "research_review", "article_sections",
    "continuous_article", "message_push", "news_release",
}
_HEADING = {"numbered", "plain", "none"}
_SECTION = {"required", "optional", "hidden"}
_RENDER_BASE = {"selected_template", "default_structured", "blank_article"}
_COMPOSITION = {"chaptered", "article_beats"}


def normalize_document_shape(value: Any, *, fallback: dict | None = None) -> dict:
    """Validate a model-selected shape without injecting business semantics."""
    raw = dict(value) if isinstance(value, dict) else {}
    base = dict(fallback) if isinstance(fallback, dict) else {}
    raw_kind = str(raw.get("raw_kind") or raw.get("kind") or base.get("raw_kind") or base.get("kind") or "structured_report").strip()
    kind = raw_kind if raw_kind in _KINDS else "unknown"
    heading_policy = str(raw.get("heading_policy") or base.get("heading_policy") or "").strip()
    section_policy = str(raw.get("section_policy") or base.get("section_policy") or "").strip()
    subheading_policy = str(raw.get("subheading_policy") or base.get("subheading_policy") or "").strip()
    render_base = str(raw.get("render_base") or base.get("render_base") or "selected_template").strip()
    defaults = {
        "structured_report": ("numbered", "required", "numbered"),
        "research_review": ("plain", "required", "plain"),
        "article_sections": ("plain", "optional", "plain"),
        "continuous_article": ("none", "hidden", "hidden"),
        "message_push": ("plain", "optional", "plain"),
        "news_release": ("none", "hidden", "hidden"),
        "unknown": ("none", "optional", "hidden"),
    }
    default_heading, default_section, default_subheading = defaults[kind]
    if heading_policy not in _HEADING:
        heading_policy = default_heading
    if section_policy not in _SECTION:
        section_policy = default_section
    if subheading_policy not in _HEADING:
        subheading_policy = default_subheading
    if section_policy == "hidden":
        heading_policy = "none"
    if render_base not in _RENDER_BASE:
        render_base = "selected_template"
    return {
        "kind": kind,
        "raw_kind": raw_kind,
        "heading_policy": heading_policy,
        "section_policy": section_policy,
        "subheading_policy": subheading_policy,
        "render_base": render_base,
        "opening": str(raw.get("opening") or base.get("opening") or "title_only"),
        "closing": str(raw.get("closing") or base.get("closing") or "natural"),
        "rationale": str(raw.get("rationale") or base.get("rationale") or ""),
    }


def visible_sections(shape: dict | None) -> bool:
    return normalize_document_shape(shape).get("section_policy") != "hidden"


def normalize_composition_mode(value: Any, *, shape: dict | None = None) -> str:
    """Keep editorial composition separate from heading visibility.

    ``article_beats`` means the final plan remains available for evidence
    allocation, but the Writer receives one document-level narrative contract.
    No domain or template name is encoded in this decision.
    """
    kind = normalize_document_shape(shape).get("kind")
    # ``continuous_article`` and ``news_release`` are semantic promises to the
    # reader, not merely a request to hide headings after drafting. A genuinely
    # sectioned article should be represented as ``article_sections`` instead.
    # This also repairs legacy plans where a default ``chaptered`` value was
    # persisted before composition became an explicit contract.
    if kind in {"continuous_article", "news_release"}:
        return "article_beats"
    mode = str(value or "").strip()
    if mode in _COMPOSITION:
        return mode
    return "article_beats" if kind in {"continuous_article", "message_push", "news_release", "unknown"} else "chaptered"


def visible_subheadings(shape: dict | None) -> bool:
    return normalize_document_shape(shape).get("subheading_policy") != "hidden"


def wants_numbering(shape: dict | None, level: int) -> bool:
    normalized = normalize_document_shape(shape)
    policy = normalized["heading_policy"] if level == 1 else normalized["subheading_policy"]
    return policy == "numbered"
