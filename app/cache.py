"""Dependency-aware artifact cache for expensive deterministic stage outputs."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.db import connect

CACHE_SCHEMA_VERSION = "artifact-cache-1"


def stable_hash(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def cache_key(stage: str, effective_inputs: Any, model_version: str = "",
              prompt_version: str = "", config_version: str = "") -> tuple[str, str]:
    input_hash = stable_hash({
        "schema": CACHE_SCHEMA_VERSION,
        "stage": stage,
        "inputs": effective_inputs,
        "model": model_version,
        "prompt": prompt_version,
        "config": config_version,
    })
    key = ":".join([
        "artifact",
        stage,
        model_version or "none",
        prompt_version or "none",
        config_version or "none",
        input_hash,
    ])
    return key, input_hash


def get_cached(stage: str, effective_inputs: Any, model_version: str = "",
               prompt_version: str = "", config_version: str = "") -> Any | None:
    key, _input_hash = cache_key(stage, effective_inputs, model_version, prompt_version, config_version)
    with connect() as conn:
        row = conn.execute("SELECT payload FROM artifact_cache WHERE cache_key=?", (key,)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE artifact_cache SET last_hit_at=datetime('now') WHERE cache_key=?", (key,))
    try:
        return json.loads(row["payload"])
    except (TypeError, ValueError):
        return None


def set_cached(stage: str, effective_inputs: Any, payload: Any, model_version: str = "",
               prompt_version: str = "", config_version: str = "") -> None:
    key, input_hash = cache_key(stage, effective_inputs, model_version, prompt_version, config_version)
    with connect() as conn:
        conn.execute(
            "INSERT INTO artifact_cache(cache_key, stage, model_version, prompt_version, "
            "config_version, input_hash, payload) VALUES(?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload",
            (
                key, stage, model_version, prompt_version, config_version, input_hash,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
