"""License lifecycle without business DB, real cryptographic signatures."""
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.licensing.protocol import LicenseError, encode, sign, verify_request
from app.licensing.service import LicenseService, add_months


@pytest.fixture
def licensing(tmp_path):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    clock = [int(datetime(2026, 1, 31, 12, tzinfo=timezone.utc).timestamp())]
    tick = [0]
    service = LicenseService(tmp_path / "license", {"test": encode(public)},
        machine=lambda: "host-one", clock=lambda: clock[0], monotonic=lambda: tick[0])

    def issue(generation=1, license_id=None, **updates):
        request = verify_request(service.request()).model_dump()
        body = {**request, "kind": "grant", "key_id": "test",
                "license_id": license_id or f"grant-{generation}", "generation": generation,
                "issued_at": clock[0], "duration_months": 3, **updates}
        return sign("IRA-LIC1", body, private)

    return service, issue, clock, tick, private


def test_activation_restart_expiry_and_renewal(licensing):
    service, issue, clock, _, _ = licensing
    assert service.status()["status"] == "unlicensed"
    token = issue()
    result = service.activate(token)
    expiry = int(datetime(2026, 4, 30, 12, tzinfo=timezone.utc).timestamp())
    assert result["expires_at"] == expiry
    assert service.activate(token)["status"] == "unchanged"
    restarted = LicenseService(service.root, service.keys, machine=service.machine,
                               clock=service.clock, monotonic=service.monotonic)
    assert restarted.status()["active"]
    clock[0] = expiry
    assert service.status()["status"] == "expired"
    assert service.activate(token)["expires_at"] == expiry
    service.activate(issue(2))
    assert service.status()["active"]
    with pytest.raises(LicenseError, match="LICENSE_ALREADY_USED"):
        service.activate(token)


@pytest.mark.parametrize("replacement", [None, "source"])
def test_native_module_required_without_fallback(tmp_path, monkeypatch, replacement):
    import importlib
    from types import SimpleNamespace
    real_import = importlib.import_module

    def load(name, package=None):
        if name == "app.licensing._trust":
            if replacement is None:
                raise ImportError("missing native module")
            return SimpleNamespace(__loader__=None, verify=lambda token: True)
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", load)
    service = LicenseService(tmp_path / "license", machine=lambda: "host")
    assert service.status()["status"] == "license_module_unavailable"
    with pytest.raises(LicenseError, match="LICENSE_MODULE_UNAVAILABLE"):
        service.activate("anything")


@pytest.mark.parametrize("months", [3, 6, 12, 36, 60, 120])
def test_signed_duration_lifecycle(licensing, months):
    service, issue, clock, _, _ = licensing
    token = issue(duration_months=months)
    expected = add_months(clock[0], months)
    assert service.activate(token)["expires_at"] == expected
    restarted = LicenseService(service.root, service.keys, machine=service.machine,
                               clock=service.clock, monotonic=service.monotonic)
    assert restarted.status(admin=True)["duration_months"] == months
    assert restarted.status()["active"]
    assert service.activate(token)["expires_at"] == expected
    clock[0] += 86400
    # Renewal changes the duration, starting now rather than at the old expiry.
    service.activate(issue(2, duration_months=6))
    assert service.status()["expires_at"] == add_months(clock[0], 6)
    with pytest.raises(LicenseError, match="LICENSE_ALREADY_USED"):
        service.activate(token)
    clock[0] = add_months(clock[0], 6)
    assert service.status()["status"] == "expired"


@pytest.mark.parametrize("months", [0, -3, 1, 24, 121, "12"])
def test_invalid_duration_rejected(licensing, months):
    service, issue, _, _, _ = licensing
    with pytest.raises(LicenseError, match="INVALID_LICENSE"):
        service.activate(issue(duration_months=months))
    assert not service.status()["active"]


def test_issuer_duration_cli(licensing, tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    from app.licensing.protocol import verify_license

    service, _, _, _, _ = licensing
    script = Path(__file__).resolve().parents[1] / "tools" / "license_issuer.py"
    password = tmp_path / "password"
    password.write_text("test-password-not-for-production")
    key = tmp_path / "issuer.private.pem"
    trust = tmp_path / "public.json"
    request = tmp_path / "request.txt"
    request.write_text(service.request())
    common = ["--key", str(key), "--password-file", str(password)]
    subprocess.run([sys.executable, str(script), "keygen", *common,
                    "--output", str(trust)], check=True, capture_output=True)
    for generation, months in enumerate([3, 6, 12, 36, 60, 120], 1):
        output = tmp_path / f"grant-{generation}.license"
        command = [sys.executable, str(script), "issue", *common,
                   "--request", str(request), "--generation", str(generation),
                   "--output", str(output)]
        if months != 3:
            command += ["--months", str(months)]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        assert f"License term: {months} calendar months" in result.stdout
        grant = verify_license(output.read_text(), json.loads(trust.read_text()))
        assert grant.duration_months == months
    ledger = [json.loads(line) for line in (tmp_path / "issuance.jsonl").read_text().splitlines()]
    assert [row["duration_months"] for row in ledger] == [3, 6, 12, 36, 60, 120]


def test_bound_to_product_and_installation_and_host(licensing, tmp_path):
    service, issue, _, _, _ = licensing
    token = issue()
    other = LicenseService(tmp_path / "other", service.keys, machine=service.machine)
    with pytest.raises(LicenseError, match="DEVICE_MISMATCH"):
        other.activate(token)
    service.activate(token)
    shutil.copytree(service.root, tmp_path / "copy")
    clone = LicenseService(tmp_path / "copy", service.keys, machine=lambda: "host-two")
    assert clone.status()["status"] == "device_mismatch"
    with pytest.raises(LicenseError, match="INVALID_LICENSE"):
        service.activate(issue(product="another-product"))


def test_forgery_invalid_new_license_does_not_replace_old(licensing):
    service, issue, _, _, _ = licensing
    service.activate(issue())
    token = issue(2)
    parts = token.split(".")
    parts[1] = parts[1][:-2] + "AA"
    with pytest.raises(LicenseError):
        service.activate(".".join(parts))
    assert service.status(admin=True)["generation"] == 1


def test_clock_rollback_restart_and_signed_recovery(licensing):
    service, issue, clock, tick, private = licensing
    service.activate(issue())
    expiry = service.status()["expires_at"]
    clock[0] += 86400
    tick[0] += 86400
    assert service.status()["active"]
    clock[0] -= 3600
    assert service.status()["status"] == "clock_rollback"
    with pytest.raises(LicenseError, match="CLOCK_ROLLBACK"):
        service.activate(issue(2))
    restarted = LicenseService(service.root, service.keys, machine=service.machine,
                               clock=service.clock, monotonic=service.monotonic)
    assert restarted.status()["status"] == "clock_rollback"
    request = service.recovery_request()
    recovery = sign("IRA-LIC1", {**request, "product": "report-assembly-agent",
        "kind": "clock_recovery", "key_id": "test", "recovery_id": "recovery-1",
        "issued_at": clock[0], "valid_until": clock[0] + 3600}, private)
    assert service.recover_clock(recovery)["active"]
    assert service.status()["expires_at"] == expiry
    with pytest.raises(LicenseError, match="INVALID_RECOVERY"):
        service.recover_clock(recovery)


def test_monotonic_clock_prevents_repeated_small_backsteps(licensing):
    service, issue, clock, tick, _ = licensing
    service.activate(issue())
    for _ in range(3):
        tick[0] += 100
        assert service.status()["active"]
    tick[0] += 100
    assert service.status()["status"] == "clock_rollback"


def test_state_tampering_or_deletion_fails_closed(licensing):
    service, issue, _, _, _ = licensing
    service.activate(issue())
    path = service.root / "state.json"
    envelope = json.loads(path.read_text())
    envelope["state"]["expires_at"] += 86400
    path.write_text(json.dumps(envelope))
    assert service.status()["status"] == "state_damaged"
    path.unlink()
    assert service.status()["status"] == "state_damaged"


def test_concurrent_same_token_has_one_activation(licensing):
    service, issue, _, _, _ = licensing
    token = issue()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: service.activate(token), range(8)))
    assert sum(r["status"] == "activated" for r in results) == 1
    assert len({r["expires_at"] for r in results}) == 1


def test_calendar_months():
    stamp = int(datetime(2023, 11, 30, tzinfo=timezone.utc).timestamp())
    assert datetime.fromtimestamp(add_months(stamp), timezone.utc).day == 29


def test_signing_date_in_future_is_rejected(licensing):
    service, issue, clock, _, _ = licensing
    with pytest.raises(LicenseError, match="CLOCK_BEFORE_ISSUE"):
        service.activate(issue(issued_at=clock[0] + 600))


@pytest.fixture
def license_http(licensing, monkeypatch):
    import app.db as db
    import app.interaction as interaction
    import app.workflow.queue as queue
    from app.config import settings
    monkeypatch.setattr(settings, "multiuser_enabled", False)
    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(queue, "reconcile_interrupted_tasks", lambda: None)
    monkeypatch.setattr(interaction, "reconcile_interrupted_interactions", lambda: None)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import api  # Initialize routes through the actual application entrypoint.
    from app.licensing import http
    from app.api.auth_routes import current_principal, admin_principal
    from app.auth.accounts import Principal
    monkeypatch.setattr(http, "get_service", lambda: licensing[0])
    app = FastAPI()
    app.add_middleware(http.LicenseMiddleware)
    app.include_router(http.router)
    app.dependency_overrides[current_principal] = lambda: Principal(1, "admin", "admin")
    @app.get("/api/business")
    def business():
        return {"ok": True}
    @app.get("/license")
    def shell():
        return {"shell": True}
    return TestClient(app), app, current_principal, Principal


def test_api_gate_admin_activation_and_user_forbidden(license_http, licensing):
    client, app, principal, Principal = license_http
    service, issue, _, _, _ = licensing
    assert client.get("/api/business").status_code == 403
    assert client.get("/api/health").status_code == 200
    assert client.get("/license").status_code == 200
    assert client.get("/api/license/status").json()["status"] == "unlicensed"
    token = issue()
    app.dependency_overrides[principal] = lambda: Principal(2, "user", "user")
    assert client.post("/api/license/activate", json={"token": token}).status_code == 403
    assert client.get("/api/license/request").status_code == 403
    assert "installation_id" not in client.get("/api/license/status").json()
    app.dependency_overrides[principal] = lambda: Principal(1, "admin", "admin")
    assert client.post("/api/license/activate", json={"token": token}).status_code == 200
    assert client.get("/api/business").status_code == 200


def test_backend_execution_gate(licensing, monkeypatch):
    from app.licensing import service as module
    from app.llm_scheduler import invoke
    from app.licensing.service import LicenseBlocked
    monkeypatch.setattr(module, "get_service", lambda: licensing[0])
    with pytest.raises(LicenseBlocked):
        invoke("embed", lambda: pytest.fail("must not execute"))
    assert invoke("health", lambda: "ready") == "ready"
    licensing[0].activate(licensing[1]())
    assert invoke("embed", lambda: "executed") == "executed"
