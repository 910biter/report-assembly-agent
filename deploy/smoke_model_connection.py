"""Opt-in local deployment smoke: credentials come from process environment."""
import asyncio
import json
import os

import httpx
from pydantic_ai import Agent

from app.collaboration_model import build_collaboration_model
from app.gateway import model_gateway


def main():
    base = os.environ.get("IRA_SMOKE_URL", "http://127.0.0.1:8000")
    with httpx.Client(base_url=base, headers={"Origin": base}, timeout=40) as client:
        response = client.post("/api/auth/login", json={
            "username": os.environ["IRA_SMOKE_USER"], "password": os.environ["IRA_SMOKE_PASSWORD"],
        })
        response.raise_for_status()
        result = client.get("/api/system/model-connection")
        result.raise_for_status()
        config = result.json()
        assert "api_key" not in config and "encrypted_api_key" not in config
        probe = client.post("/api/system/model-connection/test", json=config)
        probe.raise_for_status()
        assert probe.json()["connected"]
        saved = client.put("/api/system/model-connection", json=config)
        saved.raise_for_status()
        assert saved.json()["model_name"] == config["model_name"]
        client.post("/api/auth/logout")
    if os.environ.get("IRA_SMOKE_NORMAL_USER"):
        with httpx.Client(base_url=base, headers={"Origin": base}, timeout=10) as client:
            response = client.post("/api/auth/login", json={
                "username": os.environ["IRA_SMOKE_NORMAL_USER"], "password": os.environ["IRA_SMOKE_NORMAL_PASSWORD"],
            })
            response.raise_for_status()
            for method in ("GET", "PUT", "POST"):
                path = "/api/system/model-connection" + ("/test" if method == "POST" else "")
                assert client.request(method, path, json=config if method != "GET" else None).status_code == 403
            client.post("/api/auth/logout")
    assert model_gateway.generate_json('Return a JSON object with "ok": true.', think=False, max_tokens=128)["ok"] is True

    async def assistant():
        model = build_collaboration_model()
        agent = Agent(model, instructions="Call read_probe before answering. Return its value exactly.")

        @agent.tool_plain
        def read_probe() -> str:
            return "connection-probe-ok"

        result = await agent.run("Read the probe value using your tool.")
        assert "connection-probe-ok" in result.output
        await model.client.close()

    asyncio.run(assistant())
    print(json.dumps({"admin_config": "passed", "key_redaction": "passed", "generation": "passed", "assistant_tool": "passed", "normal_user": "passed" if os.environ.get("IRA_SMOKE_NORMAL_USER") else "not_tested"}))


if __name__ == "__main__":
    main()
