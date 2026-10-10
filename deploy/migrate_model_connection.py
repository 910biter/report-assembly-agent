"""Explicit one-time migration; runtime never falls back to .env model fields."""
import os
from dotenv import dotenv_values

from app.config import settings, _PROJECT_ROOT
from app.db import create_all_tables
from app.model_connection import connection_from_payload, public_connection, save_connection


def main():
    create_all_tables()
    if public_connection()["configured"]:
        print("Model connection already configured; left unchanged.")
        return
    values = {**dotenv_values(_PROJECT_ROOT / ".env"), **os.environ}
    url = str(values.get("IRA_GENERATION_URL") or "").strip()
    name = str(values.get("IRA_GENERATION_MODEL") or "").strip()
    if not url or not name:
        raise SystemExit("No legacy model connection; configure System Settings.")
    connection = connection_from_payload({
        "base_url": url, "model_name": name,
        "api_key": values.get("IRA_GENERATION_API_KEY") or "",
        "service_kind": "vllm",
        "context_tokens": settings.model_context_window_tokens,
        "output_tokens": settings.generation_reserve_tokens,
        "tokenizer_path": values.get("IRA_GENERATION_TOKENIZER_PATH") or "",
        "vllm_priority": True,
    })
    save_connection(connection)
    print("Legacy connection migrated to PostgreSQL; no credentials printed.")


if __name__ == "__main__":
    main()
