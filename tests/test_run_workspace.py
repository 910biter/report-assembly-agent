import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.infrastructure.orm import (
    Base, ORMPlan, ORMReport, ORMReportVersion, ORMSentence, ORMShortMemory,
)
from app.workflow import run_workspace
from app.workflow import controller as controller_module


@pytest.fixture
def workspace_db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine) as session:
            with session.begin():
                yield session

    monkeypatch.setattr(run_workspace, "session_scope", scope)
    with scope() as session:
        plan_id = session.execute(insert(ORMPlan).values(title="旧目录")).inserted_primary_key[0]
        report_id = session.execute(insert(ORMReport).values(
            plan_id=plan_id, title="原报告", task_id="task-1",
        )).inserted_primary_key[0]
        session.execute(insert(ORMSentence).values(
            report_id=report_id, section="第一章", position=1001,
            content="原文", source_level="TRANSITION",
        ))
        session.execute(insert(ORMShortMemory).values(
            task_id="task-1", payload=json.dumps({"report_id": report_id, "plan_id": plan_id}),
        ))
    yield scope, int(plan_id), int(report_id)
    engine.dispose()


def test_working_report_is_invisible_until_atomic_promotion(workspace_db):
    scope, plan_id, report_id = workspace_db
    working_plan_id, base_plan_id = run_workspace.fork_plan(plan_id, report_id)
    assert base_plan_id == plan_id
    assert working_plan_id != plan_id
    working_id, fingerprint = run_workspace.create_working_report(report_id, working_plan_id)
    with scope() as session:
        session.execute(update(ORMPlan).where(ORMPlan.c.id == working_plan_id).values(title="新目录"))
        session.execute(update(ORMSentence).where(
            ORMSentence.c.report_id == working_id
        ).values(content="新文"))
        live = session.execute(select(ORMSentence.c.content).where(
            ORMSentence.c.report_id == report_id
        )).scalar_one()
        assert live == "原文"
    run_workspace.promote_working_report(report_id, working_id, fingerprint, "task-1")
    with scope() as session:
        report = session.execute(select(ORMReport).where(ORMReport.c.id == report_id)).mappings().one()
        assert report["plan_id"] == working_plan_id
        assert session.execute(select(ORMSentence.c.content).where(
            ORMSentence.c.report_id == report_id
        )).scalar_one() == "新文"
        assert session.execute(select(ORMReport.c.id).where(ORMReport.c.id == working_id)).first() is None
        assert session.execute(select(ORMReportVersion.c.id).where(
            ORMReportVersion.c.report_id == report_id
        )).first() is not None
        task = json.loads(session.execute(select(ORMShortMemory.c.payload)).scalar_one())
        assert task["report_id"] == report_id


def test_concurrent_live_edit_blocks_promotion_without_losing_either_copy(workspace_db):
    scope, plan_id, report_id = workspace_db
    working_id, fingerprint = run_workspace.create_working_report(report_id, plan_id)
    with scope() as session:
        session.execute(update(ORMSentence).where(
            ORMSentence.c.report_id == report_id
        ).values(content="用户刚编辑的正文"))
    with pytest.raises(ValueError, match="REPORT_CHANGED_DURING_RUN"):
        run_workspace.promote_working_report(report_id, working_id, fingerprint, "task-1")
    with scope() as session:
        assert session.execute(select(ORMSentence.c.content).where(
            ORMSentence.c.report_id == report_id
        )).scalar_one() == "用户刚编辑的正文"
        assert session.execute(select(ORMSentence.c.content).where(
            ORMSentence.c.report_id == working_id
        )).scalar_one() == "原文"
    run_workspace.discard_working_report(report_id, working_id)


def test_version_failure_rolls_back_report_promotion(workspace_db, monkeypatch):
    scope, plan_id, report_id = workspace_db
    working_id, fingerprint = run_workspace.create_working_report(report_id, plan_id)
    with scope() as session:
        session.execute(update(ORMSentence).where(
            ORMSentence.c.report_id == working_id
        ).values(content="新文"))
    monkeypatch.setattr(
        run_workspace, "ensure_report_version",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("VERSION_SAVE_FAILED")),
    )
    with pytest.raises(RuntimeError, match="VERSION_SAVE_FAILED"):
        run_workspace.promote_working_report(report_id, working_id, fingerprint, "task-1")
    with scope() as session:
        assert session.execute(select(ORMSentence.c.content).where(
            ORMSentence.c.report_id == report_id
        )).scalar_one() == "原文"
        assert session.execute(select(ORMSentence.c.content).where(
            ORMSentence.c.report_id == working_id
        )).scalar_one() == "新文"
        assert session.execute(select(ORMReportVersion.c.id).where(
            ORMReportVersion.c.report_id == report_id
        )).first() is None


def test_controller_writes_to_private_report_and_keeps_stable_report_id(workspace_db, monkeypatch):
    scope, plan_id, report_id = workspace_db
    controller = controller_module.WorkflowController.__new__(controller_module.WorkflowController)
    controller.task_id = "task-1"
    controller.task = {"plan_id": plan_id, "report_id": report_id, "run_id": "run-2"}
    controller.cm = None
    controller._update = lambda **fields: controller.task.update(fields)
    controller._ensure_working_plan()
    working_plan_id = controller.task["plan_id"]
    controller._plan = lambda: {
        "id": working_plan_id, "title": "新目录", "structure": ["第一章"],
        "chapter_plans": [{"title": "第一章"}],
    }
    controller._facts = lambda: []
    controller._inferences = lambda: []
    controller._selected_variant = lambda: None
    controller._effective_institution_rules = lambda _variant: {}
    controller._auto_revision = lambda *_args: None
    controller._normalize_report_order = lambda *_args: None
    controller._run_quality_check = lambda *_args: None
    controller._auto_quality_fix = lambda *_args: False

    def fake_write(*_args, **kwargs):
        working_id = kwargs["existing_report_id"]
        assert working_id != report_id
        with scope() as session:
            assert session.execute(select(ORMSentence.c.content).where(
                ORMSentence.c.report_id == report_id
            )).scalar_one() == "原文"
            session.execute(update(ORMSentence).where(
                ORMSentence.c.report_id == working_id
            ).values(content="完成的新文"))
        return SimpleNamespace(id=working_id)

    monkeypatch.setattr(controller_module.writer_agent, "write", fake_write)
    controller.write()
    with scope() as session:
        assert session.execute(select(ORMSentence.c.content).where(
            ORMSentence.c.report_id == report_id
        )).scalar_one() == "完成的新文"
        assert session.execute(select(ORMReport.c.plan_id).where(
            ORMReport.c.id == report_id
        )).scalar_one() == working_plan_id
    assert controller.task["report_id"] == report_id


def test_failed_run_discards_private_report_and_restores_plan_pointer(workspace_db, monkeypatch):
    scope, plan_id, report_id = workspace_db
    controller = controller_module.WorkflowController.__new__(controller_module.WorkflowController)
    controller.task_id = "task-1"
    controller.task = {"plan_id": plan_id, "report_id": report_id, "run_id": "run-2"}
    controller._update = lambda **fields: controller.task.update(fields)
    controller._ensure_working_plan()
    working_id, fingerprint = run_workspace.create_working_report(report_id, controller.task["plan_id"])
    controller.task.update(
        working_report_id=working_id, working_report_hash=fingerprint,
        working_report_run_id="run-2",
    )
    monkeypatch.setattr("app.licensing.service.require_license", lambda: None)
    controller._run_to_review = lambda: (_ for _ in ()).throw(RuntimeError("WRITER_INCOMPLETE_CHAPTERS"))
    controller._save_failed_core_chain_audit = lambda _exc: None
    with pytest.raises(RuntimeError, match="WRITER_INCOMPLETE_CHAPTERS"):
        controller.run_to_review()
    with scope() as session:
        assert session.execute(select(ORMSentence.c.content).where(
            ORMSentence.c.report_id == report_id
        )).scalar_one() == "原文"
        assert session.execute(select(ORMReport.c.id).where(ORMReport.c.id == working_id)).first() is None
    assert controller.task["plan_id"] == plan_id
    assert controller.task["report_id"] == report_id
