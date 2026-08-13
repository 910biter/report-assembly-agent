"""Word export using JSON template schema + original DOCX template base.

The renderer does not rely on built-in Word Title/Heading styles. It creates
IRA_* semantic styles from computed template roles and applies direct paragraph
formatting so hidden built-in borders/colors do not leak into generated reports.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from docx import Document as DocxDocument
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from app.config import settings
from app.db import connect
from app.memory.style import get_locked_variant, get_variant
from app.template_engine import check_docx_conformance


ROLE_STYLE_NAMES = {
    "document_title": "IRA_DocumentTitle",
    "heading_1": "IRA_Heading1",
    "heading_2": "IRA_Heading2",
    "heading_3": "IRA_Heading3",
    "body": "IRA_Body",
    "caption": "IRA_Caption",
    "signature": "IRA_Signature",
}


def export_report(report_id: int) -> Path:
    """Export selected report sentences to DOCX and write a conformance sidecar."""
    with connect() as conn:
        report = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if report is None:
            raise ValueError("REPORT_NOT_FOUND")
        sentences = conn.execute(
            "SELECT * FROM report_sentences WHERE report_id=? AND selected=1 ORDER BY position",
            (report_id,),
        ).fetchall()
    variant = get_variant(report["style_profile_id"]) if report["style_profile_id"] else None
    if variant is None:
        variant = get_locked_variant()
    format_spec = _dominant_format(variant)
    schema = format_spec.get("template_schema") if isinstance(format_spec.get("template_schema"), dict) else {}

    doc = _open_render_base(format_spec, schema)
    _clear_body_keep_sections(doc)
    _apply_format(doc, format_spec, schema)
    _install_semantic_styles(doc, schema, format_spec)

    _add_role_paragraph(doc, report["title"], "document_title")
    current_section = None
    current_paragraph = None
    paragraph_buffer: list[str] = []
    for sentence in sentences:
        section_changed = sentence["section"] != current_section
        paragraph_changed = sentence["paragraph"] != current_paragraph
        if section_changed:
            _flush_paragraph(doc, paragraph_buffer)
            paragraph_buffer = []
            current_section = sentence["section"]
            _add_role_paragraph(doc, current_section, "heading_1")
        elif paragraph_changed:
            _flush_paragraph(doc, paragraph_buffer)
            paragraph_buffer = []
        current_paragraph = sentence["paragraph"]
        paragraph_buffer.append(sentence["user_edit"] or sentence["content"])
    _flush_paragraph(doc, paragraph_buffer)

    settings.ensure_dirs()
    out = settings.reports_dir / f"report_{report_id}.docx"
    doc.save(out)
    _write_conformance(out, schema)
    return out


def _dominant_format(variant) -> dict:
    if variant is None or not isinstance(variant.format_spec, dict):
        return {}
    dominant = variant.format_spec.get("dominant")
    return dominant if isinstance(dominant, dict) else {}


def _open_render_base(format_spec: dict, schema: dict):
    source = _source_template_path(format_spec, schema)
    if source and source.exists():
        return DocxDocument(source)
    return DocxDocument()


def _source_template_path(format_spec: dict, schema: dict) -> Path | None:
    candidates = [
        format_spec.get("source_template_path"),
        schema.get("source", {}).get("path") if isinstance(schema.get("source"), dict) else None,
    ]
    for item in candidates:
        if item:
            path = Path(item)
            if path.exists():
                return path
    return None


def _clear_body_keep_sections(doc) -> None:
    """Remove template sample content while retaining section properties."""
    body = doc._body._element
    sect_pr = body.sectPr
    for child in list(body):
        if child is sect_pr:
            continue
        body.remove(child)
    if sect_pr is not None and sect_pr.getparent() is None:
        body.append(sect_pr)


def _install_semantic_styles(doc, schema: dict, format_spec: dict) -> None:
    roles = _schema_roles(schema)
    fallback = _legacy_roles(format_spec)
    for role, style_name in ROLE_STYLE_NAMES.items():
        role_style = roles.get(role) or fallback.get(role) or {}
        _ensure_paragraph_style(doc, style_name, role_style)
    if not roles and not fallback:
        _ensure_paragraph_style(doc, ROLE_STYLE_NAMES["body"], {})


def _ensure_paragraph_style(doc, style_name: str, role_style: dict):
    try:
        style = doc.styles[style_name]
    except KeyError:
        style = doc.styles.add_style(style_name, WD_STYLE_TYPE.PARAGRAPH)
        try:
            style.base_style = doc.styles["Normal"]
        except Exception:
            pass
    run_spec = _role_run(role_style)
    paragraph_spec = _role_paragraph(role_style)
    _apply_font(style.font, run_spec)
    _set_east_asia(style, run_spec.get("font_east_asia") or run_spec.get("font_name"))
    _apply_paragraph_format(style.paragraph_format, paragraph_spec)
    _set_style_borders(style, paragraph_spec.get("borders"))
    return style


def _add_role_paragraph(doc, text: str, role: str):
    style_name = ROLE_STYLE_NAMES.get(role, ROLE_STYLE_NAMES["body"])
    paragraph = doc.add_paragraph()
    try:
        paragraph.style = doc.styles[style_name]
    except Exception:
        pass
    role_style = getattr(doc, "_ira_role_styles", {}).get(role, {})
    _apply_paragraph_format(paragraph.paragraph_format, _role_paragraph(role_style))
    _set_paragraph_borders(paragraph, _role_paragraph(role_style).get("borders"))
    run = paragraph.add_run(text)
    _apply_font(run.font, _role_run(role_style))
    return paragraph


def _flush_paragraph(doc, buffer: list[str]) -> None:
    normal_buffer: list[str] = []
    for text in buffer:
        if _is_list_item(text):
            if normal_buffer:
                _add_role_paragraph(doc, "".join(normal_buffer), "body")
                normal_buffer = []
            _add_list_paragraph(doc, text)
        else:
            normal_buffer.append(text)
    if normal_buffer:
        _add_role_paragraph(doc, "".join(normal_buffer), "body")


def _is_list_item(text: str) -> bool:
    return bool(re.match(r"^(?:[•\-*]|[一二三四五六七八九十]+[、.]|\d+[、.])\s*", str(text).strip()))


def _add_list_paragraph(doc, text: str):
    paragraph = _add_role_paragraph(doc, re.sub(r"^(?:[•\-*])\s*", "", text.strip()), "body")
    paragraph.paragraph_format.first_line_indent = None
    paragraph.paragraph_format.left_indent = Cm(0.74)
    return paragraph


def _schema_roles(schema: dict) -> dict:
    roles = schema.get("style", {}).get("roles", {}) if isinstance(schema, dict) else {}
    return roles if isinstance(roles, dict) else {}


def _legacy_roles(format_spec: dict) -> dict:
    doc_format = format_spec.get("document_format") if isinstance(format_spec.get("document_format"), dict) else {}
    typography = doc_format.get("typography") if isinstance(doc_format.get("typography"), dict) else {}
    return {
        "document_title": _legacy_role(typography.get("title") or format_spec.get("title")),
        "heading_1": _legacy_role(typography.get("heading1") or format_spec.get("heading1")),
        "heading_2": _legacy_role(typography.get("heading2") or format_spec.get("heading2")),
        "heading_3": _legacy_role(typography.get("heading3") or format_spec.get("heading3")),
        "body": _legacy_role(typography.get("body") or format_spec.get("body_paragraph") or format_spec.get("normal") or format_spec),
    }


def _legacy_role(spec) -> dict:
    spec = spec if isinstance(spec, dict) else {}
    line_spacing = {}
    if spec.get("line_spacing_pt") is not None:
        line_spacing = {"type": "exact_pt", "value": spec.get("line_spacing_pt")}
    elif spec.get("line_spacing") is not None:
        line_spacing = {"type": "multiple", "value": spec.get("line_spacing")}
    return {
        "paragraph": {
            "alignment": spec.get("alignment"),
            "first_line_indent_cm": spec.get("first_line_indent_cm"),
            "left_indent_cm": spec.get("left_indent_cm"),
            "right_indent_cm": spec.get("right_indent_cm"),
            "space_before_pt": spec.get("space_before_pt"),
            "space_after_pt": spec.get("space_after_pt"),
            "line_spacing": line_spacing or "unknown",
            "borders": spec.get("borders") or {"top": None, "bottom": None, "left": None, "right": None},
        },
        "run": {
            "font_east_asia": spec.get("font_name"),
            "font_ascii": spec.get("font_name"),
            "font_size_pt": spec.get("font_size_pt"),
            "font_color": spec.get("font_color_rgb") or "000000",
            "bold": spec.get("bold"),
            "italic": spec.get("italic"),
        },
    }


def _role_paragraph(role_style: dict) -> dict:
    paragraph = role_style.get("paragraph") if isinstance(role_style, dict) else {}
    return paragraph if isinstance(paragraph, dict) else {}


def _role_run(role_style: dict) -> dict:
    run = role_style.get("run") if isinstance(role_style, dict) else {}
    return run if isinstance(run, dict) else {}


def _apply_font(font, spec: dict) -> None:
    if not isinstance(spec, dict):
        return
    font_name = spec.get("font_east_asia") or spec.get("font_name") or spec.get("font_ascii")
    if font_name and font_name != "unknown":
        font.name = font_name
    if spec.get("font_size_pt") not in (None, "", "unknown"):
        font.size = Pt(float(spec["font_size_pt"]))
    color = spec.get("font_color") or spec.get("font_color_rgb")
    if color and color != "unknown":
        try:
            font.color.rgb = RGBColor.from_string(str(color).replace("#", ""))
        except ValueError:
            pass
    if spec.get("bold") not in (None, "", "unknown"):
        font.bold = bool(spec["bold"])
    if spec.get("italic") not in (None, "", "unknown"):
        font.italic = bool(spec["italic"])
    if spec.get("underline") not in (None, "", "unknown"):
        font.underline = bool(spec["underline"])


def _apply_paragraph_format(paragraph_format, spec: dict) -> None:
    if not isinstance(spec, dict):
        return
    line_spacing = spec.get("line_spacing")
    if isinstance(line_spacing, dict):
        if line_spacing.get("type") == "exact_pt" and line_spacing.get("value") not in (None, "unknown"):
            paragraph_format.line_spacing = Pt(float(line_spacing["value"]))
        elif line_spacing.get("type") == "multiple" and line_spacing.get("value") not in (None, "unknown"):
            paragraph_format.line_spacing = float(line_spacing["value"])
    elif spec.get("line_spacing_pt") not in (None, "", "unknown"):
        paragraph_format.line_spacing = Pt(float(spec["line_spacing_pt"]))
    elif spec.get("line_spacing") not in (None, "", "unknown"):
        paragraph_format.line_spacing = float(spec["line_spacing"])
    if spec.get("first_line_indent_cm") not in (None, "", "unknown"):
        paragraph_format.first_line_indent = Cm(float(spec["first_line_indent_cm"]))
    if spec.get("space_before_pt") not in (None, "", "unknown"):
        paragraph_format.space_before = Pt(float(spec["space_before_pt"]))
    if spec.get("space_after_pt") not in (None, "", "unknown"):
        paragraph_format.space_after = Pt(float(spec["space_after_pt"]))
    if spec.get("left_indent_cm") not in (None, "", "unknown"):
        paragraph_format.left_indent = Cm(float(spec["left_indent_cm"]))
    if spec.get("right_indent_cm") not in (None, "", "unknown"):
        paragraph_format.right_indent = Cm(float(spec["right_indent_cm"]))
    if spec.get("alignment") not in (None, "", "unknown"):
        paragraph_format.alignment = _alignment_value(spec["alignment"])
    if spec.get("keep_with_next") not in (None, "", "unknown"):
        paragraph_format.keep_with_next = bool(spec["keep_with_next"])
    if spec.get("keep_together") not in (None, "", "unknown"):
        paragraph_format.keep_together = bool(spec["keep_together"])
    if spec.get("page_break_before") not in (None, "", "unknown"):
        paragraph_format.page_break_before = bool(spec["page_break_before"])


def _alignment_value(value):
    text = str(value).lower()
    if "center" in text:
        return WD_ALIGN_PARAGRAPH.CENTER
    if "right" in text:
        return WD_ALIGN_PARAGRAPH.RIGHT
    if "justify" in text:
        return WD_ALIGN_PARAGRAPH.JUSTIFY
    return WD_ALIGN_PARAGRAPH.LEFT


def _set_east_asia(style, font_name: str | None) -> None:
    if not font_name or font_name == "unknown":
        return
    try:
        rpr = style.element.get_or_add_rPr()
        rfonts = rpr.rFonts
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        rfonts.set(qn("w:eastAsia"), font_name)
        rfonts.set(qn("w:ascii"), font_name)
        rfonts.set(qn("w:hAnsi"), font_name)
    except Exception:
        pass


def _set_style_borders(style, borders) -> None:
    try:
        ppr = style.element.get_or_add_pPr()
        _replace_pbdr(ppr, borders)
    except Exception:
        pass


def _set_paragraph_borders(paragraph, borders) -> None:
    try:
        ppr = paragraph._p.get_or_add_pPr()
        _replace_pbdr(ppr, borders)
    except Exception:
        pass


def _replace_pbdr(ppr, borders) -> None:
    old = ppr.find(qn("w:pBdr"))
    if old is not None:
        ppr.remove(old)
    if not isinstance(borders, dict):
        return
    if not any(borders.get(side) for side in ("top", "bottom", "left", "right")):
        return
    pbdr = OxmlElement("w:pBdr")
    for side in ("top", "bottom", "left", "right"):
        spec = borders.get(side)
        if not isinstance(spec, dict):
            continue
        node = OxmlElement(f"w:{side}")
        node.set(qn("w:val"), str(spec.get("style") or spec.get("val") or "single"))
        if spec.get("color"):
            node.set(qn("w:color"), str(spec["color"]))
        if spec.get("width") or spec.get("sz"):
            node.set(qn("w:sz"), str(spec.get("width") or spec.get("sz")))
        if spec.get("space"):
            node.set(qn("w:space"), str(spec["space"]))
        pbdr.append(node)
    if len(pbdr):
        ppr.append(pbdr)


def _apply_format(doc, spec: dict, schema: dict) -> None:
    """Apply page/header/footer and expose role styles for paragraph creation."""
    roles = _schema_roles(schema) or _legacy_roles(spec)
    doc._ira_role_styles = roles
    try:
        page = schema.get("document", {}).get("page", {}) if schema else {}
        margins = schema.get("document", {}).get("margins", {}) if schema else {}
        if not page:
            page = spec.get("page_cm", {})
        if not margins:
            margins = spec.get("margins_cm", {})
        section = doc.sections[0]
        if page.get("width_cm") and page.get("height_cm"):
            section.page_width = Cm(float(page["width_cm"]))
            section.page_height = Cm(float(page["height_cm"]))
        elif page.get("width") and page.get("height"):
            section.page_width = Cm(float(page["width"]))
            section.page_height = Cm(float(page["height"]))
        margin_map = {
            "top_margin": margins.get("top_cm", margins.get("top")),
            "bottom_margin": margins.get("bottom_cm", margins.get("bottom")),
            "left_margin": margins.get("left_cm", margins.get("left")),
            "right_margin": margins.get("right_cm", margins.get("right")),
        }
        for attr, value in margin_map.items():
            if value not in (None, "", "unknown"):
                setattr(section, attr, Cm(float(value)))
    except Exception:
        pass


def _write_conformance(out: Path, schema: dict) -> None:
    if not schema:
        return
    result = check_docx_conformance(out, schema)
    sidecar = out.with_suffix(".conformance.json")
    sidecar.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
