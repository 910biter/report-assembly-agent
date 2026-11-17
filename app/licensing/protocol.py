"""Versioned, product-scoped offline signatures. No issuer secret ships here."""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field

PRODUCT = "report-assembly-agent"


class LicenseError(ValueError):
    pass


class StrictDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InstallationRequest(StrictDocument):
    product: Literal["report-assembly-agent"]
    installation_id: str
    device_key: str
    machine_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class Grant(StrictDocument):
    product: Literal["report-assembly-agent"]
    kind: Literal["grant"]
    key_id: str
    license_id: str
    installation_id: str
    device_key: str
    machine_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    generation: int = Field(gt=0)
    duration_months: Literal[3, 6, 12, 36, 60, 120]
    issued_at: int = Field(gt=0)


class Recovery(StrictDocument):
    product: Literal["report-assembly-agent"]
    kind: Literal["clock_recovery"]
    key_id: str
    recovery_id: str
    installation_id: str
    state_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    issued_at: int = Field(gt=0)
    valid_until: int = Field(gt=0)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode(value: str) -> bytes:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except Exception as exc:
        raise LicenseError("INVALID_LICENSE") from exc


def sign(prefix: str, payload: dict, private_key) -> str:
    body = encode(canonical(payload))
    message = f"{prefix}.{body}"
    return f"{message}.{encode(private_key.sign(message.encode('ascii')))}"


def unpack(token: str, prefix: str) -> tuple[dict, bytes, bytes]:
    if len(token) > 16384:
        raise LicenseError("INVALID_LICENSE")
    try:
        actual, body, signature = token.strip().split(".")
        if actual != prefix:
            raise ValueError("protocol")
        raw = decode(body)
        payload = json.loads(raw)
        if not isinstance(payload, dict) or canonical(payload) != raw:
            raise ValueError("noncanonical")
        return payload, f"{actual}.{body}".encode("ascii"), decode(signature)
    except Exception as exc:
        raise LicenseError("INVALID_LICENSE") from exc


def verify_request(token: str) -> InstallationRequest:
    try:
        payload, message, signature = unpack(token, "IRA-REQ1")
        request = InstallationRequest.model_validate(payload)
        UUID(request.installation_id)
        Ed25519PublicKey.from_public_bytes(decode(request.device_key)).verify(signature, message)
        return request
    except Exception as exc:
        raise LicenseError("INVALID_INSTALLATION_REQUEST") from exc


def verify_license(token: str, trusted_keys: dict[str, str]) -> Grant | Recovery:
    try:
        payload, message, signature = unpack(token, "IRA-LIC1")
        public = trusted_keys.get(payload.get("key_id"))
        if not public:
            raise LicenseError("UNKNOWN_SIGNING_KEY")
        Ed25519PublicKey.from_public_bytes(decode(public)).verify(signature, message)
        model = Recovery if payload.get("kind") == "clock_recovery" else Grant
        result = model.model_validate(payload)
        datetime.fromtimestamp(result.issued_at)
        return result
    except LicenseError:
        raise
    except Exception as exc:
        raise LicenseError("INVALID_LICENSE") from exc


def key_id(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()[:16]
