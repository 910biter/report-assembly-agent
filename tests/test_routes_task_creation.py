from contextlib import contextmanager


def test_create_task_initializes_run_id_before_building_payload(monkeypatch):
    import app.db as db
    import app.interaction as interaction
    import app.workflow.queue as workflow_queue

    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(interaction, "reconcile_interrupted_interactions", lambda: None)
    monkeypatch.setattr(workflow_queue, "reconcile_interrupted_tasks", lambda: None)
    from app.api import routes

    captured = {}

    monkeypatch.setattr(routes, "_collect_material_ids", lambda *args: [31, 32, 33])
    monkeypatch.setattr(routes, "attach_draft_thread", lambda *args: None)
    monkeypatch.setattr(routes, "enqueue_task", lambda task_id: {"status": "queued"})

    @contextmanager
    def fake_session_scope():
        yield object()

    monkeypatch.setattr(routes, "session_scope", fake_session_scope)

    def fake_create_task_run(task_id, revision, run_mode, _session):
        captured["task_id"] = task_id
        return "run-regression"

    monkeypatch.setattr(routes, "create_task_run", fake_create_task_run)

    class FakeShortTerm:
        @staticmethod
        def create_task(task_id, payload, _session):
            captured["payload"] = payload

    monkeypatch.setattr(routes, "short_term", FakeShortTerm())

    result = routes.create_task(
        theme="测试任务",
        requirements="测试要求",
        variant_id=13,
        existing_material_ids="31,32,33",
        workflow_mode="automatic",
        requirement_review="auto",
        directory_review="auto",
        files=None,
    )

    assert result["material_count"] == 3
    assert captured["payload"]["run_id"] == "run-regression"
