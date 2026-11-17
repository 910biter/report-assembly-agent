from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from app.retrieval.source_candidates import _search_terms, load_small_source_scope, search_source_candidates


def test_long_chinese_query_is_split_into_retrieval_candidates():
    terms = _search_terms("请说明该企业过去三年的主营业务与经营情况")
    assert terms
    assert all(2 <= len(term) <= 3 for term in terms)
    assert len(terms) <= 32


def test_source_search_is_material_scoped_and_database_bounded():
    row = {
        "unit_id": 17, "material_id": 4, "kind": "text", "page": 3,
        "paragraph": 8, "content": "企业主营业务介绍", "filename": "source.pdf",
        "term_matches": 1, "term_hit_0": 1,
    }
    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = [row]
    scope = MagicMock()
    scope.__enter__.return_value = session
    with patch("app.retrieval.source_candidates.session_scope", return_value=scope), \
            patch("app.db._UNIT_TRGM_INDEX_READY", True):
        rows, metadata = search_source_candidates([4, 5], "企业主营业务", limit=40)

    statement = session.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "units.material_id IN" in sql
    assert "ILIKE" in sql
    assert "LIMIT" in sql
    assert "units.content" in sql
    assert rows == [{key: value for key, value in row.items() if key != "term_hit_0"} | {
        "matched_terms": ["企业主营业务"],
    }]
    assert metadata["bounded"] is True
    assert metadata["index"] == "postgres_trigram"
    assert metadata["candidate_limit"] == 40


def test_small_scope_returns_all_rows_or_defers_to_bounded_search():
    session = MagicMock()
    scope = MagicMock()
    scope.__enter__.return_value = session
    rows = [{"unit_id": index, "content": f"text-{index}"} for index in range(3)]
    session.execute.return_value.mappings.return_value.all.return_value = rows
    with patch("app.retrieval.source_candidates.session_scope", return_value=scope):
        assert load_small_source_scope([4], 3) == rows
        assert load_small_source_scope([4], 2) is None
    statement = session.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "units.material_id IN" in sql
    assert "LIMIT" in sql
