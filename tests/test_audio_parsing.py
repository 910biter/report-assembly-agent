from pathlib import Path
from types import SimpleNamespace

import pytest

from app.parsing import docling_adapter


@pytest.mark.parametrize("suffix", [".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"])
def test_supported_audio_formats_use_asr_pipeline(suffix):
    assert docling_adapter.can_parse_with_docling(Path("recording" + suffix))
    assert suffix in docling_adapter._AUDIO_EXTS


def test_empty_audio_transcript_is_not_accepted(monkeypatch):
    document = SimpleNamespace(export_to_dict=lambda: {"texts": []}, export_to_markdown=lambda: "")
    converter = SimpleNamespace(convert=lambda _source: SimpleNamespace(document=document, status="success"))
    monkeypatch.setattr(docling_adapter, "_build_converter", lambda *_args, **_kwargs: converter)

    with pytest.raises(ValueError, match="ASR_EMPTY_TRANSCRIPT"):
        docling_adapter._convert(Path("recording.wav"), ".wav", do_ocr=False)


def test_audio_transcript_becomes_source_unit(monkeypatch):
    document = SimpleNamespace(
        export_to_dict=lambda: {"texts": [{"text": "转写内容", "label": "text", "prov": []}]},
        export_to_markdown=lambda: "转写内容",
    )
    converter = SimpleNamespace(convert=lambda _source: SimpleNamespace(document=document, status="success"))
    monkeypatch.setattr(docling_adapter, "_build_converter", lambda *_args, **_kwargs: converter)

    units, profile = docling_adapter._convert(Path("recording.wav"), ".wav", do_ocr=False)

    assert len(units) == 1
    assert units[0].content == "转写内容"
    assert profile["file_type"] == "wav"


def test_pdf_pipeline_uses_prefetched_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(docling_adapter.settings, "docling_artifacts_path", str(tmp_path))

    options = docling_adapter._pdf_pipeline_options(do_ocr=False)

    assert options.artifacts_path == tmp_path
