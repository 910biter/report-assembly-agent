from app.planning.scale import normalize_execution_plan
from app.planning.structure import normalize_contract


def test_legacy_numeric_requirement_lists_are_prompt_safe():
    plan = normalize_contract({
        "chapter_plans": [{
            "title": "第一章",
            "questions": ["需要回答的问题"],
            "required_facts": [3322, 3418],
            "required_inferences": [660],
        }],
    })
    chapter = plan["chapter_plans"][0]
    assert chapter["required_facts"] == ["3322", "3418"]
    assert chapter["required_inferences"] == ["660"]


def test_execution_plan_normalizes_top_level_and_chapter_contracts():
    plan = normalize_execution_plan({
        "dimensions": [1, "技术"],
        "required_facts": [2],
        "budget": {"target_words": 1000},
        "chapter_plans": [{
            "title": "第一章",
            "questions": 3,
            "required_facts": 4,
        }],
    })
    assert plan["dimensions"] == ["1", "技术"]
    assert plan["required_facts"] == ["2"]
    assert plan["chapter_plans"][0]["questions"] == ["3"]
    assert plan["chapter_plans"][0]["required_facts"] == ["4"]
