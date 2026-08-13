"""DOCX template compiler, renderer helpers, and conformance checks."""

from app.template_engine.compiler import compile_template
from app.template_engine.conformance import check_docx_conformance

__all__ = ["compile_template", "check_docx_conformance"]
