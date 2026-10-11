import json
from dataclasses import replace

import httpx
import httpx2
import pytest

from app import vllm_capacity as vc
from app.model_connection import Connection


@pytest.fixture(autouse=True)
def clean():
    vc.reset()
    yield
    vc.reset()


def connection():
    return Connection("http://service/v1", "model", service_kind="vllm", context_tokens=0, output_tokens=4096)


def response(url, count=100, window=8192):
    return httpx.Response(200, request=httpx.Request("POST", url), json={"count": count, "max_model_len": window})


def test_count_uses_root_endpoint_and_actual_request_template(monkeypatch):
    seen = []
    def post(url, **kwargs):
        seen.append((url, kwargs["json"]))
        return response(url)
    monkeypatch.setattr(vc.httpx, "post", post)
    c = connection()
    payload = {"model": "model", "messages": [{"role": "user", "content": "data"}],
               "tools": [{"type": "function", "function": {"name": "read"}}],
               "max_tokens": 2048, **c.extra_body(False)}
    result = vc.verify_request(c, payload, 128)
    assert result["effective_context_tokens"] == 8192
    assert result["request_count_method"] == "vllm_tokenize"
    assert seen[-1][0] == "http://service/tokenize"
    assert seen[-1][1]["tools"] == payload["tools"]
    assert seen[-1][1]["chat_template_kwargs"] == payload["chat_template_kwargs"]
    assert "max_tokens" not in seen[-1][1]
    vc.verify_request(c, payload, 128)
    assert len(seen) == 2  # one window probe, one unique final request
    assert all("data" not in key for key in vc._counts)


def test_configured_limit_cannot_exceed_deployment(monkeypatch):
    monkeypatch.setattr(vc.httpx, "post", lambda url, **kw: response(url))
    assert vc.window(replace(connection(), context_tokens=32768)) == 8192
    assert vc.window(replace(connection(), context_tokens=4096)) == 4096


def test_overflow_never_changes_output_or_messages(monkeypatch):
    monkeypatch.setattr(vc.httpx, "post", lambda url, **kw: response(url, count=7000))
    payload = {"model": "model", "messages": [{"role": "user", "content": "x"}], "max_tokens": 2048}
    before = json.dumps(payload)
    with pytest.raises(ValueError, match="MODEL_INPUT_OVER_BUDGET"):
        vc.verify_request(connection(), payload)
    assert json.dumps(payload) == before


def test_failure_has_cooldown_then_recovery(monkeypatch):
    now = [1000.0]
    calls = []
    monkeypatch.setattr(vc.time, "monotonic", lambda: now[0])
    def failed(*args, **kwargs):
        calls.append(1)
        raise httpx.ConnectError("unavailable")
    monkeypatch.setattr(vc.httpx, "post", failed)
    c = replace(connection(), context_tokens=8192)
    payload = {"model": "model", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1024}
    assert vc.verify_request(c, payload)["count_degraded"]
    assert vc.verify_request(c, payload)["count_degraded"]
    assert len(calls) == 1
    with pytest.raises(ValueError, match="MODEL_CAPACITY_UNAVAILABLE"):
        vc.window(connection())
    now[0] += 31
    monkeypatch.setattr(vc.httpx, "post", lambda url, **kw: response(url))
    assert vc.verify_request(c, payload)["request_count_method"] == "vllm_tokenize"


def test_usage_calibration_is_local_and_bounded(monkeypatch):
    c = connection()
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    for _ in range(40):
        vc.observe_usage(c, payload, {"prompt_tokens": 100})
    monkeypatch.setattr(vc.httpx, "post", lambda *a, **kw: pytest.fail("Packing must not call network"))
    assert vc.estimate("hello", c) >= vc.raw_estimate("hello")
    assert len(vc._profile(c).ratios) == 32


def test_final_count_rechecks_changed_deployment_window(monkeypatch):
    count = [0]
    def post(url, **kw):
        count[0] += 1
        return response(url, count=100, window=8192 if count[0] == 1 else 2048)
    monkeypatch.setattr(vc.httpx, "post", post)
    with pytest.raises(ValueError, match="MODEL_INPUT_OVER_BUDGET"):
        vc.verify_request(connection(), {"model": "model", "messages": [], "max_tokens": 2048})


def test_collaboration_hook_verifies_serialized_wire_payload(monkeypatch):
    import asyncio
    from app.collaboration_model import build_collaboration_model
    import app.collaboration_model as cm
    monkeypatch.setattr(cm, "load_connection", connection)
    seen = []
    monkeypatch.setattr(vc, "verify_request", lambda c, payload, safety: seen.append(payload) or {"request_count_method": "vllm_tokenize"})
    model = build_collaboration_model()
    client = model.client._client
    assert isinstance(client, httpx2.AsyncClient)
    request = httpx2.Request("POST", "http://service/v1/chat/completions",
                            json={"model": "model", "messages": [], "max_completion_tokens": 1024})
    asyncio.run(client.event_hooks["request"][0](request))
    assert seen[0]["max_tokens"] == 1024
    assert "max_completion_tokens" not in seen[0]
    assert request.extensions["model_capacity"]["request_count_method"] == "vllm_tokenize"
    asyncio.run(client.aclose())
