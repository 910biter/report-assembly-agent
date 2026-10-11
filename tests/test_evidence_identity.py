from unittest.mock import patch

from app.evidence.identity import match_propositions, validate_matches
from app.evidence.extractor import _SYSTEM
from app.analysis.analyzer import _SYSTEM as ANALYSIS_SYSTEM


def record(key, content, unit=7, quote="source quote", **extra):
    return {"key": key, "content": content, "unit_ids": [unit], "quotes": [quote], **extra}


def test_exact_source_proposition_ignores_classification():
    incoming = record("c0", "a plan", assertion_mode="prospective")
    existing = record("f1", "a plan", assertion_mode="reported")
    with patch("app.evidence.identity.BaseAgent.generate_json") as generate:
        matches, _ = match_propositions([incoming], [existing])
    assert matches == {"c0": "f1"}
    generate.assert_not_called()


def test_model_can_resolve_paraphrase_without_lexical_rules():
    with patch("app.evidence.identity.stage_input_budget_tokens", return_value=8000), \
         patch("app.evidence.identity.BaseAgent.generate_json", return_value={
             "matches": [{"incoming": "c0", "target": "f1", "certain": True}]
         }), patch("app.evidence.identity.update_call_funnel"):
        matches, audit = match_propositions([record("c0", "planned deployment")], [record("f1", "deployment proposed")])
    assert matches == {"c0": "f1"}
    assert audit["classified"] == 1


def test_matching_text_in_different_sources_is_not_merged():
    with patch("app.evidence.identity.BaseAgent.generate_json") as generate:
        matches, _ = match_propositions([record("c0", "same", unit=8)], [record("f1", "same")])
    assert not matches
    generate.assert_not_called()


def test_distinct_claims_from_same_unit_survive():
    with patch("app.evidence.identity.stage_input_budget_tokens", return_value=8000), \
         patch("app.evidence.identity.BaseAgent.generate_json", return_value={"matches": []}), \
         patch("app.evidence.identity.update_call_funnel"):
        matches, _ = match_propositions([record("c0", "cash balance")], [record("f1", "borrowing estimate")])
    assert not matches


def test_merge_cannot_hide_additional_quoted_condition_even_if_model_says_equal():
    incoming = record("c0", "borrowing with a cash assumption")
    incoming["quotes"] = ["borrowing estimate", "cash assumption"]
    existing = record("f1", "borrowing", quote="borrowing estimate")
    bundles = [{"incoming": incoming, "candidates": [existing]}]
    assert validate_matches({"matches": [{"incoming": "c0", "target": "f1", "certain": True}]}, bundles) == {}
    with patch("app.evidence.identity.BaseAgent.generate_json") as generate:
        matches, _ = match_propositions([incoming], [existing])
    assert not matches
    generate.assert_not_called()


def test_same_source_numeric_paraphrase_with_distinct_quotes_requires_model_confirmation():
    incoming = record("c0", "预计购买380亿美元证券", quote="购买最多380亿美元")
    existing = record("f1", "购买证券预计不超过380亿美元", quote="该季度上限为380亿美元")
    with patch("app.evidence.identity.stage_input_budget_tokens", return_value=8000), \
         patch("app.evidence.identity.BaseAgent.generate_json", return_value={
             "matches": [{"incoming": "c0", "target": "f1", "certain": True}]
         }), patch("app.evidence.identity.update_call_funnel"):
        matches, audit = match_propositions([incoming], [existing])
    assert matches == {"c0": "f1"}
    assert audit["classified"] == 1


def test_numeric_paraphrase_cannot_merge_an_added_quantity():
    incoming = record("c0", "借款7390亿美元，余额9500亿美元", quote="余额9500亿美元")
    existing = record("f1", "借款7390亿美元", quote="借款7390亿美元")
    with patch("app.evidence.identity.BaseAgent.generate_json") as generate:
        matches, _ = match_propositions([incoming], [existing])
    assert not matches
    generate.assert_not_called()


def test_invalid_uncertain_cross_source_and_conflicting_targets_are_rejected():
    bundles = [{"incoming": record("c0", "x"), "candidates": [record("f1", "y"), record("f2", "z"), record("f3", "x", unit=8)]}]
    for item in [
        {"incoming": "c0", "target": "f99", "certain": True},
        {"incoming": "c0", "target": "f1", "certain": False},
        {"incoming": "c0", "target": "f3", "certain": True},
    ]:
        assert validate_matches({"matches": [item]}, bundles) == {}
    assert validate_matches({"matches": [
        {"incoming": "c0", "target": "f1", "certain": True},
        {"incoming": "c0", "target": "f2", "certain": True},
    ]}, bundles) == {}


def test_failed_identity_call_keeps_new_claim():
    with patch("app.evidence.identity.stage_input_budget_tokens", return_value=8000), \
         patch("app.evidence.identity.BaseAgent.generate_json", side_effect=RuntimeError("MODEL_OUTPUT_TRUNCATED")):
        matches, audit = match_propositions([record("c0", "new")], [record("f1", "old")])
    assert not matches
    assert audit["unclassified"] == 1
    assert audit["failures"] == ["MODEL_OUTPUT_TRUNCATED"]


def test_prompt_does_not_require_mechanical_full_profiles():
    for prompt in [_SYSTEM, ANALYSIS_SYSTEM]:
        assert '"semantic_profile": {"facets": []' in prompt
        assert "实现状态" in prompt
        assert "条件" in prompt
        assert "归属" in prompt
        assert "时间" in prompt
