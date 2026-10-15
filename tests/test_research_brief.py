from unittest.mock import MagicMock, patch

from app.research_brief import build_research_brief


def test_brief_exposes_partial_reading_without_inventing_a_theme():
    brief = build_research_brief({
        "material_ids": [1, 2, 3],
        "parse_progress": {"done": 2},
        "material_insights": [{
            "material_id": 1, "filename": "source.pdf", "topic": "主题线索",
            "key_points": ["材料提及一项计划"],
            "missing_information": ["计划是否落地"],
            "context_meta": {"selected_units": 2, "total_units": 5},
        }],
    })
    assert brief["focus"] == ""
    assert brief["topic_candidates"] == ["主题线索"]
    assert brief["coverage"] == {
        "selected": 3, "parsed": 2, "understood": 1,
        "not_understood": 2, "limited_context": 1,
        "limited_materials": [{"material_id": 1, "filename": "source.pdf"}],
        "unknown_context": 0, "completeness": "unknown",
        "coverage_note": "0 份材料的历史理解结果未记录实际阅读范围；2 份材料尚无理解结果。覆盖完整性无法确认。",
        "analysis_status": "pending",
    }
    assert brief["open_questions"][0]["filename"] == "source.pdf"
    assert brief["preliminary"] is True


def test_legacy_insight_has_unknown_reading_scope():
    brief = build_research_brief({
        "material_ids": [7],
        "material_insights": [{"material_id": 7, "key_points": ["线索"]}],
    })
    assert brief["coverage"]["parsed"] == 1
    assert brief["coverage"]["unknown_context"] == 1
    assert brief["coverage"]["completeness"] == "unknown"
    assert "覆盖完整性无法确认" in brief["coverage"]["coverage_note"]
    assert brief["preliminary"] is True


def test_known_full_context_has_a_distinct_coverage_state():
    brief = build_research_brief({
        "material_ids": [4],
        "material_insights": [{
            "material_id": 4, "context_meta": {"selected_units": 3, "total_units": 3},
        }],
    })
    assert brief["coverage"]["completeness"] == "known"
    assert "覆盖了各材料单元" in brief["coverage"]["coverage_note"]


def test_shared_model_slots_use_one_total_pool_with_interactive_reserve():
    from app.llm_scheduler import _shared_slot

    connection = MagicMock()
    connection.invalidated = False
    connection.closed = False
    connection.execute.return_value.scalar_one.return_value = True
    engine = MagicMock()
    engine.connect.return_value.execution_options.return_value.__enter__.return_value = connection
    names = []

    def key(name):
        names.append(name)
        return len(names)

    with patch("app.llm_scheduler.settings.multiuser_enabled", True), \
            patch("app.llm_scheduler.settings.llm_concurrency", 2), \
            patch("app.llm_scheduler.settings.interactive_concurrency", 1), \
            patch("app.db._get_engine", return_value=engine), \
            patch("app.workflow.execution._key", side_effect=key):
        with _shared_slot("workflow"):
            pass
        with _shared_slot("interactive"):
            pass
    assert names == [
        "report-assembly:model:total:0",
        "report-assembly:model:total:0", "report-assembly:model:total:1",
    ]
    assert connection.execute.call_count == 4
