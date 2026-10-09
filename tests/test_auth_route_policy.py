import pytest

from app.auth.authorization import required_resources


@pytest.mark.parametrize("params,expected", [
    ({"task_id": "t-1"}, [("task", "t-1")]),
    ({"report_id": 4, "sentence_id": 9}, [("report", 4)]),
    ({"base_version_id": 1, "target_version_id": 2},
     [("report_version", 1), ("report_version", 2)]),
    ({"comparison_id": 7, "item_id": 3}, [("comparison", 7)]),
    ({"job_id": "abc"}, [("style_job", "abc")]),
])
def test_route_params_map_to_owner_checks(params, expected):
    assert required_resources(params) == expected


def test_unrecognized_route_parameter_fails_closed():
    with pytest.raises(ValueError, match="UNMAPPED_ROUTE_PARAMETER"):
        required_resources({"new_private_id": 7})
