"""DOCX conformance checks against compiled template schema."""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

from app.template_engine.compiler import compile_template


def check_docx_conformance(docx_path, template_schema: dict) -> dict:
    """Compare generated DOCX against the structured template schema."""
    path = Path(docx_path)
    if not path.exists() or not template_schema:
        return {"status": "unknown", "issues": [{"field": "input", "message": "缺少输出文档或模板 Schema"}]}
    generated_schema = compile_template(path)
    issues = []
    expected_roles = template_schema.get("style", {}).get("roles", {})
    actual_roles = generated_schema.get("style", {}).get("roles", {})

    _compare_page(template_schema, generated_schema, issues)
    for role in ("document_title", "heading_1", "heading_2", "body"):
        expected = expected_roles.get(role)
        actual = actual_roles.get(role)
        if expected and actual:
            _compare_role(role, expected, actual, issues)
    _check_actual_title_paragraph_border(path, expected_roles.get("document_title", {}), issues)
    status = "pass" if not issues else "fail"
    return {
        "status": status,
        "issues": issues,
        "checked_fields": [
            "page_size", "margins", "title_font", "title_size", "title_color",
            "title_alignment", "title_border", "heading_styles", "body_indent",
            "body_line_spacing", "body_spacing",
        ],
    }


def _compare_page(expected_schema: dict, actual_schema: dict, issues: list) -> None:
    expected_doc = expected_schema.get("document", {})
    actual_doc = actual_schema.get("document", {})
    for field in ("page", "margins"):
        expected = expected_doc.get(field, {})
        actual = actual_doc.get(field, {})
        for key, value in expected.items():
            _compare_value(f"document.{field}.{key}", value, actual.get(key), issues, tolerance=0.05)


def _compare_role(role: str, expected: dict, actual: dict, issues: list) -> None:
    for key in ("font_east_asia", "font_size_pt", "font_color", "bold"):
        _compare_value(f"{role}.run.{key}", expected.get("run", {}).get(key), actual.get("run", {}).get(key), issues)
    for key in ("alignment", "first_line_indent_cm", "space_after_pt"):
        _compare_value(f"{role}.paragraph.{key}", expected.get("paragraph", {}).get(key), actual.get("paragraph", {}).get(key), issues, tolerance=0.05)
    _compare_line_spacing(role, expected.get("paragraph", {}).get("line_spacing"), actual.get("paragraph", {}).get("line_spacing"), issues)
    _compare_borders(role, expected.get("paragraph", {}).get("borders"), actual.get("paragraph", {}).get("borders"), issues)


def _compare_line_spacing(role: str, expected, actual, issues: list) -> None:
    if _unknown(expected):
        return
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        if expected != actual:
            issues.append({"field": f"{role}.paragraph.line_spacing", "expected": expected, "actual": actual, "message": "行距不一致"})
        return
    _compare_value(f"{role}.paragraph.line_spacing.type", expected.get("type"), actual.get("type"), issues)
    _compare_value(f"{role}.paragraph.line_spacing.value", expected.get("value"), actual.get("value"), issues, tolerance=0.03)


def _compare_borders(role: str, expected, actual, issues: list) -> None:
    if not isinstance(expected, dict):
        return
    actual = actual if isinstance(actual, dict) else {}
    for side in ("top", "bottom", "left", "right"):
        exp = expected.get(side)
        act = actual.get(side)
        if exp is None and act is not None:
            issues.append({
                "field": f"{role}.paragraph.borders.{side}",
                "expected": None,
                "actual": act,
                "message": "模板无边框但输出出现边框",
            })
        elif isinstance(exp, dict) and isinstance(act, dict):
            for key in ("style", "width", "color", "space"):
                _compare_value(f"{role}.paragraph.borders.{side}.{key}", exp.get(key), act.get(key), issues)


def _check_actual_title_paragraph_border(path: Path, title_role: dict, issues: list) -> None:
    expected_bottom = title_role.get("paragraph", {}).get("borders", {}).get("bottom") if title_role else None
    if expected_bottom is not None:
        return
    doc = Document(path)
    title = next((p for p in doc.paragraphs if p.text.strip()), None)
    if title is None:
        return
    bottom = _paragraph_bottom_border(title)
    if bottom is not None:
        issues.append({
            "field": "document_title.paragraph.borders.bottom",
            "expected": None,
            "actual": bottom,
            "message": "模板标题无下边框，但输出标题段落存在下边框",
        })


def _paragraph_bottom_border(para):
    try:
        ppr = para._p.pPr
        pbdr = ppr.find(qn("w:pBdr")) if ppr is not None else None
        bottom = pbdr.find(qn("w:bottom")) if pbdr is not None else None
        if bottom is None and para.style is not None:
            ppr = para.style.element.pPr
            pbdr = ppr.find(qn("w:pBdr")) if ppr is not None else None
            bottom = pbdr.find(qn("w:bottom")) if pbdr is not None else None
        if bottom is None:
            return None
        return {
            "style": bottom.get(qn("w:val"), ""),
            "width": bottom.get(qn("w:sz"), ""),
            "color": bottom.get(qn("w:color"), ""),
        }
    except Exception:
        return None


def _compare_value(field: str, expected, actual, issues: list, tolerance: float = 0) -> None:
    if _unknown(expected):
        return
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)) and tolerance:
        if abs(float(expected) - float(actual)) <= tolerance:
            return
    if expected != actual:
        issues.append({"field": field, "expected": expected, "actual": actual, "message": "格式属性不一致"})


def _unknown(value) -> bool:
    return value in (None, "", "unknown", [], {})
