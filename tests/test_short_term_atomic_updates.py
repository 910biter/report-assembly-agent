import json
import threading
from contextlib import contextmanager

import pytest

from app.memory import short_term
from sqlalchemy.sql.dml import Insert


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row

    def scalar_one(self):
        return self._row


class _TransactionalMemory:
    def __init__(self):
        self.payload = json.dumps({"stage": "analysis", "theme": "baseline"})
        self.row_lock = threading.Lock()
        self.payload_read_barrier = threading.Barrier(2)


class _Session:
    def __init__(self, memory):
        self.memory = memory
        self._owns_lock = False

    def execute(self, statement):
        selected = (
            [column.name for column in statement.selected_columns]
            if hasattr(statement, "selected_columns")
            else []
        )
        if getattr(statement, "is_select", False) and selected not in (["payload"], ["task_id"]):
            return _Result(True)
        if selected == ["payload"]:
            if statement._for_update_arg is not None:
                acquired = self.memory.row_lock.acquire(timeout=3)
                assert acquired
                if self.memory.payload is None:
                    self.memory.row_lock.release()
                else:
                    self._owns_lock = True
            row = (self.memory.payload,) if self.memory.payload is not None else None
            if statement._for_update_arg is None:
                # Force both old implementations to read the same pre-update value.
                self.memory.payload_read_barrier.wait(timeout=3)
            return _Result(row)

        if selected == ["task_id"]:
            row = ("task-1",) if self.memory.payload is not None else None
            return _Result(row)

        values = getattr(statement, "_values", {})
        if isinstance(statement, Insert):
            value_by_name = {
                getattr(key, "name", key): value.value if hasattr(value, "value") else value
                for key, value in values.items()
            }
            acquired = False
            if not self._owns_lock:
                acquired = self.memory.row_lock.acquire(timeout=3)
                assert acquired
            try:
                if self.memory.payload is None:
                    self.memory.payload = value_by_name["payload"]
            finally:
                if acquired:
                    self.memory.row_lock.release()
            return _Result(None)

        payload_column = next(
            (key for key in values if getattr(key, "name", key) == "payload"),
            None,
        )
        if payload_column is not None:
            value = values[payload_column]
            payload = value.value if hasattr(value, "value") else value
            if not self._owns_lock:
                acquired = self.memory.row_lock.acquire(timeout=3)
                assert acquired
            self.memory.payload = payload
            if not self._owns_lock:
                self.memory.row_lock.release()
        return _Result(None)

    def close(self):
        if self._owns_lock:
            self._owns_lock = False
            self.memory.row_lock.release()


def test_concurrent_disjoint_task_updates_do_not_overwrite_each_other(monkeypatch):
    memory = _TransactionalMemory()

    @contextmanager
    def fake_session_scope():
        session = _Session(memory)
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(short_term, "session_scope", fake_session_scope)
    errors = []

    def update(**fields):
        try:
            short_term.update_task("task-1", **fields)
        except Exception as exc:  # surfaced below with the worker's actual error
            errors.append(exc)

    workers = [
        threading.Thread(target=update, kwargs={"queue_status": {"status": "running"}}),
        threading.Thread(target=update, kwargs={"graph_status": {"status": "ready"}}),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert not any(worker.is_alive() for worker in workers)
    assert errors == []
    payload = json.loads(memory.payload)
    assert payload["stage"] == "analysis"
    assert payload["theme"] == "baseline"
    assert payload["queue_status"] == {"status": "running"}
    assert payload["graph_status"] == {"status": "ready"}


def test_update_task_preserves_creation_behavior_for_missing_payload(monkeypatch):
    memory = _TransactionalMemory()
    memory.payload = None

    @contextmanager
    def fake_session_scope():
        session = _Session(memory)
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(short_term, "session_scope", fake_session_scope)

    result = short_term.update_task("task-1", stage="created")

    assert result == {"stage": "created"}
    assert json.loads(memory.payload) == {"stage": "created"}


def test_locked_mutations_compose_from_the_latest_payload(monkeypatch):
    memory = _TransactionalMemory()

    @contextmanager
    def fake_session_scope():
        session = _Session(memory)
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(short_term, "session_scope", fake_session_scope)
    errors = []

    def mutate(field, value):
        try:
            short_term.mutate_task(
                "task-1", lambda payload: payload.update({field: value})
            )
        except Exception as exc:
            errors.append(exc)

    workers = [
        threading.Thread(target=mutate, args=("queue_status", {"status": "running"})),
        threading.Thread(target=mutate, args=("graph_status", {"status": "ready"})),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert not any(worker.is_alive() for worker in workers)
    assert errors == []
    payload = json.loads(memory.payload)
    assert payload["queue_status"] == {"status": "running"}
    assert payload["graph_status"] == {"status": "ready"}


def test_transition_callback_runs_while_the_task_row_is_locked(monkeypatch):
    memory = _TransactionalMemory()

    @contextmanager
    def fake_session_scope():
        session = _Session(memory)
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(short_term, "session_scope", fake_session_scope)
    seen = {}

    def transition(payload, session):
        seen["session"] = session
        payload["run_revision"] = 2
        return payload

    result = short_term.transition_task("task-1", transition)

    assert seen["session"] is not None
    assert result["run_revision"] == 2
    assert json.loads(memory.payload)["run_revision"] == 2


def test_new_task_creation_is_insert_only(monkeypatch):
    memory = _TransactionalMemory()
    memory.payload = None

    @contextmanager
    def fake_session_scope():
        session = _Session(memory)
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(short_term, "session_scope", fake_session_scope)
    short_term.create_task("task-1", {"stage": "created"})

    assert json.loads(memory.payload) == {"stage": "created"}
    try:
        short_term.create_task("task-1", {"stage": "overwritten"})
    except Exception:
        pass
    else:
        raise AssertionError("duplicate create must not replace an existing task")
    assert json.loads(memory.payload) == {"stage": "created"}


def test_controller_version_counters_increment_from_current_payload(monkeypatch):
    from app.workflow.controller import WorkflowController

    memory = _TransactionalMemory()
    memory.payload = json.dumps({"stage": "review", "versions": {"task": 0}})

    @contextmanager
    def fake_session_scope():
        session = _Session(memory)
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(short_term, "session_scope", fake_session_scope)
    controllers = []
    for _ in range(2):
        controller = WorkflowController.__new__(WorkflowController)
        controller.task_id = "task-1"
        controller.task = {"stage": "review", "versions": {"task": 0}}
        controllers.append(controller)

    workers = [threading.Thread(target=controller._update, kwargs={"stage": "analysis"})
               for controller in controllers]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert not any(worker.is_alive() for worker in workers)
    assert json.loads(memory.payload)["versions"]["task"] == 2


def test_controller_map_patches_preserve_siblings_and_increment_versions(monkeypatch):
    from app.workflow.controller import WorkflowController

    memory = _TransactionalMemory()
    memory.payload = json.dumps({"stage": "review", "versions": {"task": 0}})

    @contextmanager
    def fake_session_scope():
        session = _Session(memory)
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(short_term, "session_scope", fake_session_scope)
    controllers = []
    for _ in range(2):
        controller = WorkflowController.__new__(WorkflowController)
        controller.task_id = "task-1"
        controller.task = {"stage": "review", "versions": {"task": 0}}
        controllers.append(controller)

    workers = [
        threading.Thread(target=controller._update, kwargs={
            "_map_patches": {"background_jobs": {f"job-{index}": {"status": "done"}}}
        })
        for index, controller in enumerate(controllers)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert not any(worker.is_alive() for worker in workers)
    payload = json.loads(memory.payload)
    assert set(payload["background_jobs"]) == {"job-0", "job-1"}
    assert payload["versions"]["task"] == 2


def test_incremental_activation_serializes_run_delta_and_payload_transition(monkeypatch):
    from types import SimpleNamespace

    from app import task_activation

    memory = _TransactionalMemory()
    memory.payload = json.dumps({
        "stage": "review",
        "run_mode": "initial",
        "run_revision": 2,
        "run_history": [],
        "report_id": 9,
        "theme": "existing title",
        "variant_id": 7,
        "queue_status": {"status": "completed", "finished_at": 10},
        "graph_status": {"status": "ready"},
        "material_ids": [1],
    })
    seen_sessions = []

    @contextmanager
    def fake_session_scope():
        session = _Session(memory)
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(task_activation, "session_scope", fake_session_scope)

    def queue_status_before_row_lock():
        assert not memory.row_lock.locked()
        return {}

    monkeypatch.setattr(task_activation, "task_queue_status", queue_status_before_row_lock)
    monkeypatch.setattr(
        task_activation, "ensure_report_version",
        lambda *args, **kwargs: SimpleNamespace(version_id=44),
    )
    monkeypatch.setattr(task_activation, "get_report_version", lambda *args, **kwargs: {
        "id": 44,
        "title": "existing title",
        "template_id": 7,
        "material_fingerprints": [{"material_id": 1}],
        "fact_snapshot": [{"id": 101}],
        "inference_snapshot": [{"id": 201, "source_level": "MATERIAL"}],
        "conflict_snapshot": [],
        "report_plan_snapshot": {"structure": ["Chapter"]},
    })

    def create_run(*args, **kwargs):
        seen_sessions.append(kwargs["_session"])
        return "run-3"

    def create_delta(*args, **kwargs):
        seen_sessions.append(kwargs["_session"])
        return {"id": 77}

    monkeypatch.setattr(task_activation, "create_task_run", create_run)
    monkeypatch.setattr(task_activation, "create_incremental_delta", create_delta)

    result = task_activation.activate_incremental_task(
        "task-1", {"id": 9, "title": "existing title", "plan_id": 3, "style_profile_id": 7},
        [2], update_reason="new material",
    )

    payload = json.loads(memory.payload)
    assert result["revision"] == 3
    assert payload["run_id"] == "run-3"
    assert payload["incremental_delta_id"] == 77
    assert payload["graph_status"] == {"status": "ready"}
    assert payload["queue_status"] == {"status": "created"}
    assert payload["incremental_inherited_fact_ids"] == [101]
    assert len(seen_sessions) == 2
    assert seen_sessions[0] is seen_sessions[1]
    with pytest.raises(ValueError, match="PREVIOUS_INCREMENT_PENDING"):
        task_activation.activate_incremental_task(
            "task-1", {"id": 9, "title": "existing title", "plan_id": 3, "style_profile_id": 7},
            [2], update_reason="duplicate request",
        )
    assert len(seen_sessions) == 2
