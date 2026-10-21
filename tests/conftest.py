"""Business regressions run with a real, ephemeral signed test entitlement."""
import time
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture(autouse=True)
def licensed_business_runtime(request, tmp_path, monkeypatch):
    # Licensing tests explicitly exercise unlicensed/expired/corrupt states.
    if request.node.path.name in {"test_licensing.py", "test_license_native.py"}:
        yield
        return
    from app.licensing import service
    from app.licensing.protocol import encode, sign, verify_request
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    runtime = service.LicenseService(tmp_path / "test-entitlement", {"test": encode(public)},
                                     machine=lambda: "test-only-host")
    payload = verify_request(runtime.request()).model_dump()
    runtime.activate(sign("IRA-LIC1", {
        **payload, "kind": "grant", "key_id": "test", "license_id": str(uuid4()),
        "generation": 1, "issued_at": int(time.time()), "duration_months": 3,
    }, private))
    monkeypatch.setattr(service, "get_service", lambda: runtime)
    yield
