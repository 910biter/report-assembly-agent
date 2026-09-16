from app.writing.writer import chapter_handles_information_gaps


def test_gap_handling_uses_runtime_plan_not_title_keywords():
    assert chapter_handles_information_gaps({"title": "风险边界"}) is False
    assert chapter_handles_information_gaps(
        {"title": "任意章节", "handles_missing_information": True}
    ) is True


def test_explicit_false_overrides_legacy_missing_information_fallback():
    assert chapter_handles_information_gaps({
        "title": "任意章节",
        "missing_information": ["缺少样本"],
        "handles_missing_information": False,
    }) is False


def test_legacy_plan_can_use_structured_missing_information_as_fallback():
    assert chapter_handles_information_gaps({
        "title": "任意章节",
        "missing_information": ["缺少样本"],
    }) is True
