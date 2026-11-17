"""Explicit remote license smoke test. Never changes the machine clock."""
import argparse
import json
import os
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["preflight", "request", "activate", "verify"])
    parser.add_argument("--url", required=True)
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, headers={"Origin": args.url}, timeout=30) as client:
        response = client.post("/api/auth/login", json={
            "username": os.environ.get("IRA_TEST_ADMIN_USER", "admin"),
            "password": os.environ["IRA_TEST_ADMIN_PASSWORD"],
        })
        response.raise_for_status()
        assert response.json()["role"] == "admin"
        if args.action == "preflight":
            response = client.get("/api/system/queues")
            response.raise_for_status()
            print(json.dumps(response.json(), ensure_ascii=False))
        elif args.action == "request":
            status = client.get("/api/license/status")
            status.raise_for_status()
            if not status.json()["active"]:
                blocked = client.get("/api/tasks")
                assert blocked.status_code == 403 and blocked.json()["detail"] == "LICENSE_REQUIRED"
            response = client.get("/api/license/request")
            response.raise_for_status()
            args.file.write_text(response.json()["request"], encoding="utf-8")
            if os.environ.get("IRA_TEST_USER_PASSWORD"):
                client.cookies.clear()
                response = client.post("/api/auth/login", json={
                    "username": os.environ.get("IRA_TEST_USER", "user"),
                    "password": os.environ["IRA_TEST_USER_PASSWORD"],
                })
                response.raise_for_status()
                assert response.json()["role"] == "user"
                assert client.get("/api/license/request").status_code == 403
                assert client.post("/api/license/activate", json={"token": "invalid"}).status_code == 403
                assert "installation_id" not in client.get("/api/license/status").json()
                if not status.json()["active"]:
                    assert client.get("/api/tasks").status_code == 403
            print(json.dumps({"status": status.json()["status"], "request_saved": True}))
        elif args.action == "activate":
            response = client.post("/api/license/activate", json={"token": args.file.read_text().strip()})
            response.raise_for_status()
            print(json.dumps(response.json()))
        else:
            status = client.get("/api/license/status").json()
            assert status["active"]
            assert client.get("/api/tasks").status_code == 200
            assert client.get("/api/materials").status_code == 200
            assert client.get("/license").status_code == 200
            invalid = client.post("/api/license/activate", json={"token": "invalid"})
            assert invalid.status_code == 422
            assert client.get("/api/license/status").json()["expires_at"] == status["expires_at"]
            # Use an existing non-admin test account only when explicitly provided.
            if os.environ.get("IRA_TEST_USER_PASSWORD"):
                client.cookies.clear()
                response = client.post("/api/auth/login", json={
                    "username": os.environ.get("IRA_TEST_USER", "user"),
                    "password": os.environ["IRA_TEST_USER_PASSWORD"],
                })
                response.raise_for_status()
                assert response.json()["role"] == "user"
                assert client.get("/api/license/request").status_code == 403
                assert client.post("/api/license/activate", json={"token": "invalid"}).status_code == 403
                assert client.post("/api/license/recover-clock", json={"token": "invalid"}).status_code == 403
                assert "installation_id" not in client.get("/api/license/status").json()
            print(json.dumps({"active": True, "expires_at": status["expires_at"],
                              "business_api": "ok", "invalid_license": "rejected",
                              "ordinary_user": bool(os.environ.get("IRA_TEST_USER_PASSWORD"))}))


if __name__ == "__main__":
    main()
