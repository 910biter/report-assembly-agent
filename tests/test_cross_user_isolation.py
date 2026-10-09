import os
from uuid import uuid4

import pytest


@pytest.mark.skipif(not os.environ.get("IRA_TEST_DB_URL"), reason="isolated PostgreSQL required")
def test_two_users_and_admin_cannot_cross_resource_boundaries(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import db
    from app.api.routes import router as data_router
    from app.api.auth_routes import router as auth_router
    from app.auth.accounts import create_user
    from app.auth.accounts import Principal
    from app.auth.passwords import hash_password
    from app.auth.sessions import create_session
    from app.infrastructure.orm import Base, ORMPlan, ORMReport, ORMReportVersion, ORMMaterial, ORMVariant, ORMUnit
    from app.memory import short_term, style

    monkeypatch.setattr(db.settings, "db_url", os.environ["IRA_TEST_DB_URL"])
    monkeypatch.setattr(db.settings, "multiuser_enabled", True)
    db._engine = None
    db._SessionLocal = None
    db.init_db()
    users = [create_user("tenant" + uuid4().hex[:9], "a-long-test-password") for _ in range(2)]
    admin_name = "admin" + uuid4().hex[:9]
    with db.session_scope() as session:
        admin_id = session.execute(Base.metadata.tables["users"].insert().values(
            username=admin_name, password_hash=hash_password("a-long-test-password"), role="admin",
        )).inserted_primary_key[0]
    admin = Principal(int(admin_id), admin_name, "admin")
    task_ids = [uuid4().hex[:12] for _ in users]
    digest = uuid4().hex
    library_id = style._ensure_library()
    resources = []
    for user, task_id in zip(users, task_ids):
        short_term.create_task(task_id, {
            "theme": "private-" + task_id, "material_ids": [], "stage": "created",
        }, owner_user_id=user.user_id)
        with db.session_scope() as session:
            material_id = session.execute(ORMMaterial.insert().values(
                owner_user_id=user.user_id, filename=user.username + ".pdf",
                file_type="pdf", path="/tmp/private.pdf", fingerprint=digest, file_hash=digest,
            )).inserted_primary_key[0]
            session.execute(ORMUnit.insert().values(
                material_id=material_id, kind="text", content="private content " + user.username,
            ))
            variant_id = session.execute(ORMVariant.insert().values(
                owner_user_id=user.user_id, library_id=library_id, name=user.username,
            )).inserted_primary_key[0]
            plan_id = session.execute(ORMPlan.insert().values(title="private plan")).inserted_primary_key[0]
            report_id = session.execute(ORMReport.insert().values(
                owner_user_id=user.user_id, plan_id=plan_id, task_id=task_id,
                title="private report " + user.username,
            )).inserted_primary_key[0]
            version_id = session.execute(ORMReportVersion.insert().values(
                report_id=report_id, version_no=1,
            )).inserted_primary_key[0]
        short_term.update_task(task_id, material_ids=[material_id], report_id=report_id,
                               variant_id=variant_id)
        resources.append((material_id, variant_id, report_id, version_id))

    from app.control_agent import _search_material_units, _load_task_template
    denied = _search_material_units(task_ids[0], query="private", material_id=resources[1][0])
    assert denied["error"] == "MATERIAL_NOT_IN_TASK"
    assert denied["items"] == []
    assert _load_task_template(task_ids[0])["id"] == resources[0][1]

    app = FastAPI()
    app.include_router(data_router)
    app.include_router(auth_router)
    tokens = [create_session(user) for user in users]
    admin_token = create_session(admin)
    with TestClient(app) as client:
        own = {"Cookie": "ira_session=" + tokens[0]}
        write = own | {"Origin": "http://testserver"}
        foreign_material, foreign_variant, foreign_report, foreign_version = resources[1]
        assert [row["task_id"] for row in client.get("/api/tasks", headers=own).json()] == [task_ids[0]]
        assert [row["id"] for row in client.get("/api/materials", headers=own).json()] == [resources[0][0]]
        assert [row["id"] for row in client.get("/api/reports", headers=own).json()] == [resources[0][2]]
        assert [row["id"] for row in client.get("/api/style/variants", headers=own).json()] == [resources[0][1]]
        for url in (
            f"/api/tasks/{task_ids[1]}", f"/api/materials/{foreign_material}",
            f"/api/reports/{foreign_report}", f"/api/reports/{foreign_report}/export",
            f"/api/style/variants/{foreign_variant}/profile",
            f"/api/report-versions/{foreign_version}",
        ):
            assert client.get(url, headers=own).status_code == 404, url
        assert client.post("/api/interactions", headers=write, json={
            "task_id": task_ids[1], "artifact_type": "task_brief",
        }).status_code == 404
        assert client.get("/api/interactions", headers=own, params={"task_id": task_ids[1]}).status_code == 404
        assert client.post("/api/tasks", headers=write, data={
            "theme": "new", "variant_id": foreign_variant,
            "existing_material_ids": resources[0][0],
        }).status_code == 404
        assert client.post(f"/api/reports/{resources[0][2]}/incremental/preview", headers=write,
                           json={"material_ids": [foreign_material]}).status_code == 404
        assert client.get("/api/system/queues", headers=own).status_code == 403
        assert client.post(f"/api/tasks/{task_ids[0]}/run", headers=own).status_code == 403
        admin_headers = {"Cookie": "ira_session=" + admin_token}
        assert client.get("/api/admin/users", headers=admin_headers).status_code == 200
        assert client.get("/api/tasks", headers=admin_headers).json() == []
        assert client.get(f"/api/reports/{resources[0][2]}", headers=admin_headers).status_code == 404
        admin_write = admin_headers | {"Origin": "http://testserver"}
        created = client.post("/api/admin/users", headers=admin_write, json={
            "username": "managed" + uuid4().hex[:8], "password": "a-long-test-password",
        })
        assert created.status_code == 200
        managed_id = created.json()["id"]
        assert client.post(f"/api/admin/users/{managed_id}/reset-password", headers=admin_write,
                           json={"password": "another-long-password"}).status_code == 200
        assert client.post(f"/api/admin/users/{managed_id}/disable", headers=admin_write).status_code == 200
        with db.session_scope() as session:
            audit = Base.metadata.tables["account_audit_events"]
            from sqlalchemy import select
            rows = session.execute(select(audit.c.action).where(
                audit.c.actor_user_id == admin.user_id,
                audit.c.target_user_id == managed_id,
            ).order_by(audit.c.id)).scalars().all()
        assert rows == ["create_user", "reset_password", "disable_user"]


@pytest.mark.skipif(not os.environ.get("IRA_TEST_DB_URL"), reason="isolated PostgreSQL required")
def test_real_login_sessions_and_private_queue_positions(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import db
    from app.api.auth_routes import router as auth_router
    from app.api.routes import router as data_router
    from app.auth.accounts import create_user, disable_user
    from app.memory import short_term
    from app.workflow import queue_store

    monkeypatch.setattr(db.settings, "db_url", os.environ["IRA_TEST_DB_URL"])
    monkeypatch.setattr(db.settings, "multiuser_enabled", True)
    monkeypatch.setattr(db.settings, "auth_http_test_mode", True)
    db._engine = None
    db._SessionLocal = None
    db.init_db()
    names = ["login" + uuid4().hex[:9] for _ in range(2)]
    users = [create_user(name, "a-long-test-password") for name in names]
    tasks = [uuid4().hex[:12] for _ in users]
    for task_id, user in zip(tasks, users):
        short_term.create_task(task_id, {
            "stage": "created", "theme": task_id, "queue_status": {"status": "queued"},
        }, owner_user_id=user.user_id)
        queue_store.enqueue(task_id, task_id, user.user_id)
    app = FastAPI()
    app.include_router(data_router)
    app.include_router(auth_router)
    with TestClient(app) as first, TestClient(app) as second:
        for client, name in ((first, names[0]), (second, names[1])):
            response = client.post("/api/auth/login", headers={"Origin": "http://testserver"},
                                   json={"username": name, "password": "a-long-test-password"})
            assert response.status_code == 200
            assert "HttpOnly" in response.headers["set-cookie"]
            assert "Secure" not in response.headers["set-cookie"]
        assert [row["task_id"] for row in first.get("/api/tasks").json()] == [tasks[0]]
        assert [row["task_id"] for row in second.get("/api/tasks").json()] == [tasks[1]]
        assert first.get(f"/api/tasks/{tasks[1]}").status_code == 404
        assert second.get(f"/api/tasks/{tasks[0]}").status_code == 404
        assert second.get(f"/api/tasks/{tasks[1]}").json()["queue_status"]["ahead"] >= 1
        preference = "/api/auth/preferences/page_size:tasks"
        assert first.put(preference, headers={"Origin": "http://testserver"},
                         json={"value": 5}).status_code == 200
        assert first.get(preference).json()["value"] == 5
        assert second.get(preference).json()["value"] == 20
        assert second.put(preference, json={"value": 10}).status_code == 403
        disable_user(users[0].user_id)
        assert first.get("/api/tasks").status_code == 401
        assert second.get("/api/tasks").status_code == 200
    monkeypatch.setattr(db.settings, "auth_http_test_mode", False)
    with TestClient(app) as secure_client:
        response = secure_client.post("/api/auth/login", headers={"Origin": "http://testserver"},
                                      json={"username": names[1], "password": "a-long-test-password"})
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
