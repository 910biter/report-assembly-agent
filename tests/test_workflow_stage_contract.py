from app.models import Stage
from app.workflow.stages import STAGE_LABELS, STAGE_ORDER, stage_rank, workflow_map


def test_stage_protocol_contains_all_persisted_execution_stages():
    values = {stage.value for stage in STAGE_ORDER}
    assert "dedup" not in values
    assert "dedup" not in STAGE_LABELS
    assert all(item["stage"] != "dedup" for item in workflow_map())
    assert {"final_plan", "narrative", "qa"}.issubset(values)
    assert stage_rank(Stage.FINAL_PLAN) < stage_rank(Stage.WRITING)
    assert stage_rank("qa") < stage_rank("review")


def test_workflow_map_is_a_defensive_copy():
    first = workflow_map()
    first[0]["purpose"] = "changed"
    assert workflow_map()[0]["purpose"] != "changed"
    assert STAGE_LABELS["final_plan"] == "目录生成"
