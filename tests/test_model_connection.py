import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from app import model_connection as mc
from app.gateway import OpenAICompatibleGateway
from app.infrastructure.orm import Base


@pytest.fixture(autouse=True)
def connection(monkeypatch):
    value = mc.Connection("http://localhost:8100/v1", "test-model")
    monkeypatch.setattr(mc, "load_connection", lambda: value)
    monkeypatch.setattr("app.gateway.load_connection", lambda: value)
    return value


def test_public_config_never_exposes_key(monkeypatch):
    monkeypatch.setattr(mc, "load_connection", lambda: mc.Connection("http://localhost/v1", "m", "secret"))
    result = mc.public_connection()
    assert result["has_api_key"] is True
    assert "secret" not in json.dumps(result)
    assert "api_key" not in result


@pytest.mark.parametrize("url", ["ftp://host", "http://u:p@host/v1", "http://host/v1?key=secret", "http://host/#x"])
def test_invalid_addresses_rejected(url):
    with pytest.raises(ValueError):
        mc.connection_from_payload({"base_url": url, "model_name": "m"})


def test_key_omission_preserves_and_empty_clears(monkeypatch):
    monkeypatch.setattr(mc, "load_connection", lambda: mc.Connection("http://localhost/v1", "m", "secret"))
    payload = {"base_url": "http://localhost/v1", "model_name": "m"}
    assert mc.connection_from_payload(payload).api_key == "secret"
    assert mc.connection_from_payload(payload | {"api_key": ""}).api_key == ""


def test_capacity_validation():
    with pytest.raises(ValueError):
        mc.connection_from_payload({"base_url": "http://localhost/v1", "model_name": "m", "context_tokens": 4096, "output_tokens": 4096})


def test_generic_extensions_empty_and_vllm_priority_opt_in():
    assert mc.Connection("http://host/v1", "m").extra_body(False) == {}
    value = mc.Connection("http://host/v1", "m", service_kind="vllm")
    assert "priority" not in value.extra_body(False)
    assert "chat_template_kwargs" in value.extra_body(False)


def test_identity_changes_for_endpoint_and_model_not_credentials():
    assert mc.Connection("http://a/v1", "m", "one").identity == mc.Connection("http://a/v1", "m", "two").identity
    assert mc.Connection("http://a/v1", "m").identity != mc.Connection("http://b/v1", "m").identity


def test_generation_probe_does_not_require_models_endpoint():
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            assert url.endswith("/chat/completions")
            assert "priority" not in kwargs["json"]
            return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": {"content": "OK"}}]})
    with patch.object(mc.httpx, "Client", Client):
        assert mc.test_connection(mc.Connection("http://host/v1", "m"))["connected"]


def test_gateway_uses_snapshot_and_generic_wire_parameter(monkeypatch):
    captured = {}
    value = mc.Connection("http://host/v1", "m", "secret", output_tokens=512, output_parameter="max_completion_tokens", send_temperature=False)
    monkeypatch.setattr("app.gateway.load_connection", lambda: value)
    class Client:
        def __init__(self, **kwargs): pass
        def close(self): pass
        def post(self, url, headers, json):
            captured.update(json)
            return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    with patch("app.gateway.httpx.Client", Client):
        assert OpenAICompatibleGateway().generate("hello", max_tokens=512, temperature=0.2) == "OK"
        with pytest.raises(ValueError, match="MODEL_INPUT_OVER_BUDGET"):
            OpenAICompatibleGateway().generate("hello", max_tokens=1000, temperature=0.2)
    assert captured["max_completion_tokens"] == 512
    assert not {"priority", "temperature", "chat_template_kwargs", "max_tokens"} & captured.keys()


def test_collaboration_wire_parameter_adapter():
    from app.collaboration_model import _legacy_output_parameter
    request = httpx.Request("POST", "http://host/v1/chat/completions", json={"max_completion_tokens": 20, "messages": []})
    asyncio.run(_legacy_output_parameter(request))
    content = b"".join(request.stream)
    assert json.loads(content)["max_tokens"] == 20
    assert "max_completion_tokens" not in json.loads(content)
    assert request.content == content
    assert int(request.headers["Content-Length"]) == len(content)


def test_database_contract():
    columns = Base.metadata.tables["model_connection"].c
    assert not columns.options_json.nullable
    assert not columns.encrypted_api_key.nullable


def test_save_checks_real_queue_columns_and_upserts(monkeypatch):
    from contextlib import contextmanager
    statements = []
    class Session:
        def execute(self, statement):
            statements.append(str(statement))
            return self
        def first(self): return None
    @contextmanager
    def scope():
        yield Session()
    monkeypatch.setattr(mc, "session_scope", scope)
    mc.save_connection(mc.Connection("http://host/v1", "m"))
    assert any("graph_jobs.job_id" in item for item in statements)
    assert any("ON CONFLICT" in item for item in statements)


def test_crypto_key_written_with_restricted_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(mc.settings, "runtime_root", tmp_path)
    cipher = mc._cipher()
    assert cipher.decrypt(cipher.encrypt(b"secret")) == b"secret"
    assert (tmp_path / "secrets" / "model_connection.key").stat().st_mode & 0o077 == 0
