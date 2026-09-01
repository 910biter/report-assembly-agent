"""Domain-neutral editorial style learning and runtime matching.

This module deliberately operates on presentation and discourse features. It
does not decide which facts are true, relevant, or safe to use.
"""
from __future__ import annotations

from collections import Counter
import math
import re


_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}")
_PLACEHOLDER_RE = re.compile(
    r"(?:[×Xx]{2,}|_{3,}|\{\{.+?\}\}|<[^>]{1,40}>|【(?:填写|说明|占位)[^】]*】)"
)
_CONCRETE_RE = re.compile(
    r"(?:\d+(?:\.\d+)?(?:%|亿元|万元|人|项|个|年|月|日|小时|分钟|公里|米)?|"
    r"《[^》]{2,60}》|(?:委员会|办公室|公司|部门|机构|平台|系统))"
)
_ATTRIBUTION_RE = re.compile(r"(?:根据|依据|材料显示|数据显示|报告指出|文件明确|统计表明)")
_JUDGMENT_RE = re.compile(r"(?:表明|说明|意味着|反映出|由此可见|预计|可能|风险|建议|需要|应当)")


def build_report_exemplars(members: list[dict], per_report_limit: int = 512) -> list[dict]:
    """Build paragraph-level, auditable exemplars from approved source reports."""
    result: list[dict] = []
    for report_index, member in enumerate(members, start=1):
        filename = str(member.get("filename") or f"report-{report_index}")
        sections = _split_sections(str(member.get("text") or ""), member.get("headings") or [])
        candidates: list[dict] = []
        for section_index, section in enumerate(sections, start=1):
            paragraphs = section["paragraphs"]
            for paragraph_index, paragraph in enumerate(paragraphs, start=1):
                text = _clean_text(paragraph)
                if not _usable_exemplar(text):
                    continue
                structural_role = _structural_role(
                    section_index, len(sections), paragraph_index, len(paragraphs)
                )
                candidates.append({
                    "exemplar_id": f"R{report_index}-S{section_index}-P{paragraph_index}",
                    "sample_type": structural_role,
                    "source_report": filename,
                    "section": section["title"],
                    "chapter_type": section["title"],
                    "purpose": "",
                    "tags": [],
                    "structural_role": structural_role,
                    "section_index": section_index,
                    "paragraph_index": paragraph_index,
                    "content": text[:1600],
                    "metrics": paragraph_metrics(text),
                    "approved": True,
                    "provenance": "historical_report_upload",
                })
        result.extend(_diverse_sample(candidates, per_report_limit))
    return result


def compact_exemplar_bank(exemplars: list[dict], limit: int = 200) -> list[dict]:
    """Keep broad report/section coverage when persistence or prompts need a cap."""
    if len(exemplars) <= limit:
        return list(exemplars)
    groups: dict[tuple[str, str], list[dict]] = {}
    for item in exemplars:
        key = (str(item.get("source_report") or ""), str(item.get("section") or ""))
        groups.setdefault(key, []).append(item)
    selected: list[dict] = []
    while groups and len(selected) < limit:
        for key in list(groups):
            values = groups[key]
            selected.append(values.pop(0))
            if not values:
                groups.pop(key, None)
            if len(selected) >= limit:
                break
    return selected


def annotate_exemplars(exemplars: list[dict], annotations: list[dict]) -> list[dict]:
    """Attach LLM semantic labels while retaining deterministic provenance."""
    by_id = {
        str(item.get("sample_id") or item.get("exemplar_id") or ""): item
        for item in annotations if isinstance(item, dict)
    }
    for exemplar in exemplars:
        annotation = by_id.get(str(exemplar.get("exemplar_id") or ""), {})
        purpose = str(annotation.get("purpose") or "").strip()
        sample_type = str(annotation.get("sample_type") or "").strip().lower()
        tags = annotation.get("tags") if isinstance(annotation.get("tags"), list) else []
        realization_mode = str(annotation.get("realization_mode") or "").strip()
        if purpose:
            exemplar["purpose"] = purpose[:160]
        if sample_type in {"opening", "fact", "analysis", "risk", "conclusion", "transition"}:
            exemplar["sample_type"] = sample_type
        exemplar["tags"] = [str(tag)[:40] for tag in tags[:8] if str(tag).strip()]
        if realization_mode:
            exemplar["realization_mode"] = realization_mode[:80]
    return exemplars


def aggregate_style_metrics(exemplars: list[dict]) -> dict:
    """Produce explainable distribution statistics instead of vague adjectives."""
    usable = [item for item in exemplars if item.get("content")]
    if not usable:
        return {}
    paragraph_lengths = [len(str(item["content"])) for item in usable]
    sentence_lengths: list[int] = []
    sentence_counts: list[int] = []
    punctuation = Counter()
    for item in usable:
        text = str(item["content"])
        sentences = _sentences(text)
        sentence_counts.append(len(sentences))
        sentence_lengths.extend(len(sentence) for sentence in sentences)
        punctuation.update(char for char in text if char in "，。；：！？、（）")
    total_chars = max(1, sum(paragraph_lengths))
    concrete_count = sum(bool(_CONCRETE_RE.search(str(item["content"]))) for item in usable)
    attribution_count = sum(bool(_ATTRIBUTION_RE.search(str(item["content"]))) for item in usable)
    judgment_count = sum(bool(_JUDGMENT_RE.search(str(item["content"]))) for item in usable)
    fact_judgment_count = sum(
        bool(_CONCRETE_RE.search(str(item["content"]))) and bool(_JUDGMENT_RE.search(str(item["content"])))
        for item in usable
    )
    return {
        "sample_count": len(usable),
        "paragraph_chars": _distribution(paragraph_lengths),
        "sentences_per_paragraph": _distribution(sentence_counts),
        "sentence_chars": _distribution(sentence_lengths),
        "punctuation_per_1000_chars": {
            key: round(value * 1000 / total_chars, 2) for key, value in punctuation.items()
        },
        "structural_roles": dict(Counter(str(item.get("structural_role") or "body") for item in usable)),
        "source_report_count": len({str(item.get("source_report") or "") for item in usable}),
        "material_realization": {
            "concrete_detail_ratio": round(concrete_count / len(usable), 3),
            "explicit_attribution_ratio": round(attribution_count / len(usable), 3),
            "judgment_paragraph_ratio": round(judgment_count / len(usable), 3),
            "fact_judgment_combination_ratio": round(fact_judgment_count / len(usable), 3),
        },
    }


def select_exemplars(bank: list[dict], context: dict, limit: int = 3) -> list[dict]:
    """Hybrid metadata/lexical retrieval with source and purpose diversity.

    Semantic labels are produced during learning; lexical overlap is only a
    fallback signal. No domain vocabulary is embedded in this algorithm.
    """
    candidates = [
        item for item in bank
        if item.get("approved", True) and str(item.get("content") or "").strip()
    ]
    if not candidates:
        return []
    title = str(context.get("title") or "")
    purpose = str(context.get("purpose") or "")
    report_type = str(context.get("report_type") or "")
    desired_role = str(context.get("sample_type") or context.get("rhetorical_role") or "").lower()
    keywords = [str(item) for item in context.get("keywords") or []]
    query = " ".join([title, purpose, report_type, *keywords]).strip()
    query_terms = _terms(query)
    target_words = int(context.get("target_words") or 0)
    scored: list[tuple[float, int, dict]] = []
    for index, item in enumerate(candidates):
        metadata = " ".join(str(item.get(key) or "") for key in (
            "chapter_type", "purpose", "sample_type", "structural_role", "tags"
        ))
        candidate_terms = _terms(metadata + " " + str(item.get("content") or "")[:400])
        overlap = _jaccard(query_terms, candidate_terms)
        score = overlap * 5.0
        if desired_role and desired_role == str(item.get("sample_type") or "").lower():
            score += 3.0
        if purpose and purpose in metadata:
            score += 2.0
        if title and (title in metadata or str(item.get("section") or "") in title):
            score += 1.5
        if target_words:
            paragraph_words = max(1, len(str(item.get("content") or "")))
            expected_paragraph = max(120, min(700, target_words // 3))
            score += max(0.0, 1.0 - abs(paragraph_words - expected_paragraph) / expected_paragraph)
        scored.append((score, -index, item))
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    best_score = scored[0][0] if scored else 0.0
    selected: list[dict] = []
    seen_sections: set[tuple[str, str]] = set()
    minimum_score = 1.0 if (query_terms or desired_role) else float("inf")
    for _score, _index, item in scored:
        if _score < minimum_score:
            break
        section_key = (str(item.get("source_report") or ""), str(item.get("section") or ""))
        if section_key in seen_sections and len(selected) + 1 < limit:
            continue
        selected.append(item)
        seen_sections.add(section_key)
        if len(selected) >= max(1, limit):
            break
    return selected


def assess_style_alignment(text: str, writing_patterns: dict, terminology: dict | None = None) -> dict:
    """Low-cost style diagnostic. It never edits prose or factual lineage."""
    metrics = paragraph_metrics(text)
    learned = (writing_patterns or {}).get("observed_metrics") or {}
    issues: list[dict] = []
    score = 100
    target_sentence = (learned.get("sentence_chars") or {}).get("p50")
    if target_sentence and metrics["sentence_chars"]["p50"]:
        ratio = metrics["sentence_chars"]["p50"] / max(1, target_sentence)
        if ratio < 0.45 or ratio > 2.2:
            score -= 18
            issues.append({"code": "SENTENCE_RHYTHM_DEVIATION", "severity": "medium"})
    target_count = (learned.get("sentences_per_paragraph") or {}).get("p50")
    if target_count and metrics["sentence_count"]:
        ratio = metrics["sentence_count"] / max(1, target_count)
        if ratio < 0.4 or ratio > 2.5:
            score -= 14
            issues.append({"code": "PARAGRAPH_DENSITY_DEVIATION", "severity": "medium"})
    forbidden = [str(item) for item in (terminology or {}).get("forbidden") or [] if str(item)]
    hits = [item for item in forbidden if item in text]
    if hits:
        score -= min(30, 8 * len(hits))
        issues.append({"code": "FORBIDDEN_TERMINOLOGY", "severity": "high", "matches": hits[:8]})
    return {"score": max(0, score), "issues": issues, "metrics": metrics, "auto_rewrite": False}


def paragraph_metrics(text: str) -> dict:
    sentences = _sentences(_clean_text(text))
    lengths = [len(item) for item in sentences]
    return {
        "chars": len(_clean_text(text)),
        "sentence_count": len(sentences),
        "sentence_chars": _distribution(lengths),
    }


def _split_sections(text: str, headings: list[dict]) -> list[dict]:
    heading_map = {}
    for item in headings:
        if isinstance(item, dict):
            heading_map[_normalize_heading(str(item.get("text") or ""))] = str(item.get("text") or "").strip()
        else:
            heading_map[_normalize_heading(str(item))] = str(item).strip()
    sections = [{"title": "正文", "paragraphs": []}]
    for raw in re.split(r"[\r\n]+", text):
        value = _clean_text(raw)
        if not value:
            continue
        heading = heading_map.get(_normalize_heading(value))
        if heading:
            if not sections[-1]["paragraphs"] and sections[-1]["title"] == "正文":
                sections[-1]["title"] = heading
            else:
                sections.append({"title": heading, "paragraphs": []})
            continue
        sections[-1]["paragraphs"].append(value)
    return [section for section in sections if section["paragraphs"]]


def _diverse_sample(items: list[dict], limit: int) -> list[dict]:
    if len(items) <= limit:
        return items
    anchors = {0, len(items) - 1}
    step = (len(items) - 1) / max(1, limit - 1)
    anchors.update(min(len(items) - 1, round(index * step)) for index in range(limit))
    return [items[index] for index in sorted(anchors)[:limit]]


def _structural_role(section_index: int, section_count: int, paragraph_index: int, paragraph_count: int) -> str:
    if section_index == 1 and paragraph_index == 1:
        return "opening"
    if section_index == section_count and paragraph_index == paragraph_count:
        return "conclusion"
    if paragraph_index == 1:
        return "section_opening"
    if paragraph_index == paragraph_count:
        return "section_closing"
    return "body"


def _usable_exemplar(text: str) -> bool:
    if len(text) < 24 or _PLACEHOLDER_RE.search(text):
        return False
    visible = sum(1 for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")
    return visible / max(1, len(text)) >= 0.45


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _normalize_heading(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def _sentences(text: str) -> list[str]:
    values = [item.strip() for item in _SENTENCE_SPLIT.split(text) if item.strip()]
    return values or ([text] if text else [])


def _terms(text: str) -> set[str]:
    result: set[str] = set()
    for token in _TOKEN_RE.findall(str(text or "").lower()):
        result.add(token)
        if re.fullmatch(r"[\u4e00-\u9fff]+", token) and len(token) > 2:
            result.update(token[index:index + 2] for index in range(len(token) - 1))
    return result


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def _distribution(values: list[int]) -> dict:
    ordered = sorted(int(value) for value in values if value is not None)
    if not ordered:
        return {"min": 0, "p25": 0, "p50": 0, "p75": 0, "max": 0, "mean": 0}
    return {
        "min": ordered[0],
        "p25": _percentile(ordered, 0.25),
        "p50": _percentile(ordered, 0.50),
        "p75": _percentile(ordered, 0.75),
        "max": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 1),
    }


def _percentile(values: list[int], quantile: float) -> int:
    if not values:
        return 0
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return int(values[lower])
    fraction = position - lower
    return round(values[lower] * (1 - fraction) + values[upper] * fraction)
