"""Presentation-only heading numbering helpers.

The report stores semantic heading text without generated numbers.  UI and DOCX
export can then render numbering consistently from the learned template while
keeping Writer/Planner free to focus on content.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_CJK_DIGITS = "零一二三四五六七八九十"
_PREFIX_PATTERNS = [
    re.compile(r"^第[一二三四五六七八九十百千0-9]+[章节篇部分]\s*"),
    re.compile(r"^[一二三四五六七八九十百千]+[、.．]\s*"),
    re.compile(r"^[（(][一二三四五六七八九十百千0-9]+[）)]\s*"),
    re.compile(r"^\d+(?:\.\d+)+[、.．]?\s*"),
    re.compile(r"^\d+[、.．]\s*"),
]


@dataclass(frozen=True)
class HeadingNumbering:
    level_formats: dict[int, str]


def detect_numbering_strategy(schema: dict[str, Any] | None, *, fallback_defaults: bool = False) -> HeadingNumbering:
    """Infer heading numbering style from template samples."""
    roles = ((schema or {}).get("style") or {}).get("roles") or {}
    numbering = ((schema or {}).get("style") or {}).get("numbering") or {}
    patterns = numbering.get("patterns") or []
    role_levels = numbering.get("role_levels") or {}
    level_formats: dict[int, str] = {}

    for level, role in ((1, "heading_1"), (2, "heading_2"), (3, "heading_3")):
        inferred = _infer_ooxml_format(role_levels.get(role), level)
        if inferred:
            level_formats[level] = inferred

    for level, role in ((1, "heading_1"), (2, "heading_2"), (3, "heading_3")):
        role_spec = roles.get(role) if isinstance(roles, dict) else {}
        samples = _role_samples(role_spec)
        for sample in samples:
            inferred = _infer_format(sample, level)
            if inferred:
                level_formats[level] = inferred
                break

    for item in patterns if isinstance(patterns, list) else []:
        fmt = str(item.get("format") or "")
        if fmt == "cjk_level_1":
            level_formats.setdefault(1, "cjk_comma")
        elif fmt == "cjk_level_2":
            level_formats.setdefault(2, "cjk_parenthesized")
        elif fmt == "arabic_level_3":
            level_formats.setdefault(3, "decimal_nested")
        elif fmt == "arabic_level_2":
            level_formats.setdefault(2, "decimal_nested")
        elif fmt == "arabic_level_1":
            level_formats.setdefault(1, "decimal_comma")

    # Missing learned numbering is not an instruction to turn every document
    # into a formal numbered report.
    if fallback_defaults and 1 not in level_formats:
        level_formats[1] = "cjk_comma"
    if fallback_defaults and 2 not in level_formats:
        level_formats[2] = "cjk_parenthesized"
    return HeadingNumbering(level_formats=level_formats)


def _infer_ooxml_format(level_spec: Any, level: int) -> str | None:
    if not isinstance(level_spec, dict):
        return None
    number_format = str(level_spec.get("number_format") or "").lower()
    level_text = str(level_spec.get("level_text") or "")
    if not level_text:
        return None
    if number_format in {"chinesecounting", "chineselegal", "ideographtraditional"}:
        if level_text.startswith("第"):
            return "cjk_chapter"
        return "cjk_parenthesized" if any(char in level_text for char in "（）()") else "cjk_comma"
    placeholders = len(re.findall(r"%\d+", level_text))
    if placeholders > 1:
        return "decimal_nested"
    if any(char in level_text for char in "（）()"):
        return "paren_decimal"
    if "、" in level_text:
        return "decimal_comma"
    if "." in level_text:
        return "decimal_dot" if level == 1 else "decimal_nested"
    if placeholders == 1 and number_format == "decimal":
        return "decimal_space"
    return None


def format_heading(level: int, path: list[int], title: str, strategy: HeadingNumbering) -> str:
    """Render a semantic heading with one deterministic numbering strategy."""
    clean_title = strip_heading_prefix(title)
    fmt = strategy.level_formats.get(level)
    prefix = _prefix_for(fmt, path)
    return f"{prefix}{clean_title}" if prefix else clean_title


def strip_heading_prefix(text: str) -> str:
    """Remove a heading number prefix without touching year-like titles."""
    value = str(text or "").strip()
    if re.match(r"^\d{4}(?:年|年度)", value):
        return value
    for pattern in _PREFIX_PATTERNS:
        stripped = pattern.sub("", value, count=1).strip()
        if stripped != value:
            return stripped
    return value


def has_heading_prefix(text: str) -> bool:
    value = str(text or "").strip()
    if re.match(r"^\d{4}(?:年|年度)", value):
        return False
    return any(pattern.match(value) for pattern in _PREFIX_PATTERNS)


def _role_samples(role_spec: Any) -> list[str]:
    if not isinstance(role_spec, dict):
        return []
    samples = role_spec.get("samples")
    if isinstance(samples, list):
        values = [str(item) for item in samples if str(item or "").strip()]
    else:
        values = []
    sample_text = str(role_spec.get("sample_text") or "").strip()
    if sample_text:
        values.insert(0, sample_text)
    return values


def _infer_format(sample: str, level: int) -> str | None:
    text = str(sample or "").strip()
    if re.match(r"^第[一二三四五六七八九十百千0-9]+[章节篇部分]", text):
        return "cjk_chapter"
    if re.match(r"^[一二三四五六七八九十百千]+、", text):
        return "cjk_comma"
    if re.match(r"^[（(][一二三四五六七八九十百千]+[）)]", text):
        return "cjk_parenthesized"
    if re.match(r"^[（(]\d+[）)]", text):
        return "paren_decimal"
    if re.match(r"^\d+(?:\.\d+)+", text):
        return "decimal_nested"
    if re.match(r"^\d+、", text):
        return "decimal_comma"
    if re.match(r"^\d+\.", text):
        return "decimal_dot" if level == 1 else "decimal_nested"
    return None


def _prefix_for(fmt: str | None, path: list[int]) -> str:
    if not fmt or not path:
        return ""
    current = path[-1]
    if fmt == "cjk_chapter":
        return f"第{_to_cjk(current)}章 "
    if fmt == "cjk_comma":
        return f"{_to_cjk(current)}、"
    if fmt == "cjk_parenthesized":
        return f"（{_to_cjk(current)}）"
    if fmt == "paren_decimal":
        return f"（{current}）"
    if fmt == "decimal_dot":
        return f"{current}. "
    if fmt == "decimal_space":
        return f"{current} "
    if fmt == "decimal_comma":
        return f"{current}、"
    if fmt == "decimal_nested":
        return ".".join(str(item) for item in path) + " "
    return ""


def _to_cjk(number: int) -> str:
    if number <= 0:
        return str(number)
    if number < 10:
        return _CJK_DIGITS[number]
    if number == 10:
        return "十"
    if number < 20:
        return "十" + _CJK_DIGITS[number - 10]
    if number < 100:
        tens, ones = divmod(number, 10)
        return _CJK_DIGITS[tens] + "十" + (_CJK_DIGITS[ones] if ones else "")
    return str(number)
