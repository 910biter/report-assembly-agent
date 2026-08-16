"""Unified material parser.

Production parsing is Docling-only. Legacy PDF/Office/Image parsers are not
used as fallback because mixed parser outputs break Unit/Evidence consistency.
"""
from __future__ import annotations

from pathlib import Path

from app.models import Unit
from app.parser.docling_adapter import (
    DOCLING_PARSER_VERSION,
    can_parse_with_docling,
    parse_with_docling,
)

PARSER_VERSION = DOCLING_PARSER_VERSION


def parse_file(path: str | Path) -> list[Unit]:
    """Parse a material file through the unified Docling backend."""
    units, _profile = parse_file_with_profile(path)
    return units


def parse_file_with_profile(path: str | Path) -> tuple[list[Unit], dict]:
    """Return Units plus Docling parse metadata.

    Failures are explicit: unsupported formats, Docling failures, and provider
    configuration errors are surfaced to the workflow instead of hidden behind
    legacy fallback.
    """
    source = Path(path)
    if not can_parse_with_docling(source):
        raise ValueError(f"UNSUPPORTED_FORMAT: {source.suffix.lower()}")
    try:
        return parse_with_docling(source)
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(f"DOCLING_PARSE_FAILED: {type(exc).__name__}: {exc}") from exc


__all__ = [
    "PARSER_VERSION",
    "DOCLING_PARSER_VERSION",
    "parse_file",
    "parse_file_with_profile",
]
