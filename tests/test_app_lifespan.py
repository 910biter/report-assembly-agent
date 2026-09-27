from fastapi.testclient import TestClient


def test_app_lifespan_starts_and_stops_graph_worker_without_deprecated_events(monkeypatch):
    import app.db as db
    import app.interaction as interaction
    import app.workflow.queue as workflow_queue

    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(workflow_queue, "reconcile_interrupted_tasks", lambda: None)
    monkeypatch.setattr(interaction, "reconcile_interrupted_interactions", lambda: None)

    from app import api
    from app import graph_jobs

    lifecycle_calls = []
    monkeypatch.setattr(graph_jobs, "start_worker", lambda: lifecycle_calls.append("start"))
    monkeypatch.setattr(graph_jobs, "stop_worker", lambda: lifecycle_calls.append("stop"))

    application = api.create_app()

    assert application.router.on_startup == []
    assert application.router.on_shutdown == []

    with TestClient(application):
        assert lifecycle_calls == ["start"]

    assert lifecycle_calls == ["start", "stop"]
