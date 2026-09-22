import inspect
from unittest.mock import patch

from app.graph.service import GraphBuildResult
from app.workflow.controller import WorkflowController


def test_report_completion_does_not_schedule_background_graph_build():
    """Graph construction is an explicit user action, not a post-review job."""
    source = inspect.getsource(WorkflowController.run_to_review)

    assert "_start_background_graph_build" not in source
    assert "graph_build_before_analysis" not in source


def _controller(task_id="child", base_task_id="base"):
    task = {
        "task_id": task_id,
        "id": task_id,
        "workspace_id": "workspace-1",
        "incremental_update": task_id != "initial",
        "incremental_base_task_id": base_task_id if task_id != "initial" else "",
        "incremental_new_fact_ids": [],
    }
    with patch("app.workflow.controller.short_term.load_task", return_value=task):
        controller = WorkflowController(task_id)
    controller._update = lambda **fields: controller.task.update(fields)
    controller._graph_scope_ids = lambda: [task_id] + ([base_task_id] if base_task_id else [])
    controller._legacy_graph_fact_ids = lambda: set()
    return controller


def test_incremental_graph_reuses_inherited_coverage_without_llm_extraction():
    controller = _controller()
    facts = [{"id": 1, "content": "old fact"}, {"id": 2, "content": "another old fact"}]

    class ReusingGraph:
        def covered_fact_ids(self, fact_ids, workspace_id=""):
            return set(fact_ids)

        def link_existing_fact_graph(self, task_id, fact_ids, workspace_id=""):
            return 0

        def build_task_graph(self, *_args, **_kwargs):
            raise AssertionError("inherited graph coverage must not trigger extraction")

        def task_graph(self, *_args, **_kwargs):
            return {"stats": {"entity_count": 2, "assertion_count": 1}}

    with patch("app.graph.graph_service", ReusingGraph()):
        status = controller._build_task_graph(facts)

    assert status["status"] == "reused"
    assert status["reused_fact_count"] == 2
    assert status["delta_fact_count"] == 0
    assert controller.task["graph_fact_ids"] == [1, 2]


def test_incremental_graph_extracts_only_uncovered_facts():
    controller = _controller()
    facts = [{"id": value, "content": f"fact {value}"} for value in range(1, 6)]
    extracted = []
    coverage_calls = 0

    class DeltaGraph:
        def covered_fact_ids(self, fact_ids, workspace_id=""):
            nonlocal coverage_calls
            coverage_calls += 1
            return {1, 2, 3} if coverage_calls == 1 else {1, 2, 3, 4, 5}

        def link_existing_fact_graph(self, task_id, fact_ids, workspace_id=""):
            return 0

        def build_task_graph(self, task_id, graph_facts, **_kwargs):
            extracted.append([fact["id"] for fact in graph_facts])
            return GraphBuildResult(status="ready", assertions=1)

    with patch("app.graph.graph_service", DeltaGraph()):
        status = controller._build_task_graph(facts)

    assert extracted == [[4, 5]]
    assert status["status"] == "ready"
    assert status["reused_fact_count"] == 3
    assert status["delta_fact_count"] == 2
    assert controller.task["graph_fact_ids"] == [1, 2, 3, 4, 5]


def test_incremental_graph_trusts_explicit_facts_from_ready_base_task():
    current = {
        "task_id": "child",
        "id": "child",
        "workspace_id": "workspace-1",
        "incremental_update": True,
        "incremental_base_task_id": "base",
        "incremental_inherited_fact_ids": [1, 2],
        "incremental_new_fact_ids": [3, 4],
    }
    base = {
        "task_id": "base",
        "id": "base",
        "graph_status": {"status": "ready"},
    }
    with patch(
        "app.workflow.controller.short_term.load_task",
        side_effect=lambda task_id: current if task_id == "child" else base,
    ):
        controller = WorkflowController("child")
        controller._update = lambda **fields: controller.task.update(fields)
        controller._graph_scope_ids = lambda: ["child", "base"]
        extracted = []

        class ReadyBaseGraph:
            def covered_fact_ids(self, fact_ids, workspace_id=""):
                return set()

            def link_existing_fact_graph(self, task_id, fact_ids, workspace_id=""):
                return 1

            def build_task_graph(self, task_id, graph_facts, **_kwargs):
                extracted.append([fact["id"] for fact in graph_facts])
                return GraphBuildResult(status="ready")

        facts = [{"id": value, "content": f"fact {value}"} for value in range(1, 5)]
        with patch("app.graph.graph_service", ReadyBaseGraph()):
            status = controller._build_task_graph(facts)

    assert extracted == [[3, 4]], status
    assert status["reused_fact_count"] == 2
    assert status["delta_fact_count"] == 2


def test_initial_graph_build_still_processes_all_facts():
    controller = _controller(task_id="initial", base_task_id="")
    facts = [{"id": value, "content": f"fact {value}"} for value in range(1, 4)]
    extracted = []

    class InitialGraph:
        def covered_fact_ids(self, fact_ids, workspace_id=""):
            return set()

        def link_existing_fact_graph(self, task_id, fact_ids, workspace_id=""):
            return 0

        def build_task_graph(self, task_id, graph_facts, **_kwargs):
            extracted.append([fact["id"] for fact in graph_facts])
            return GraphBuildResult(status="ready")

    with patch("app.graph.graph_service", InitialGraph()):
        controller._build_task_graph(facts)

    assert extracted == [[1, 2, 3]]
