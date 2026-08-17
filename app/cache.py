"""Dependency-aware artifact cache for expensive deterministic stage outputs."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.db import session_scope
from app.infrastructure.orm import Base
from sqlalchemy import select

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
    cache_table = Base.metadata.tables["artifact_cache"]
    with session_scope() as s:
        row = s.execute(select(cache_table.c.payload).where(cache_table.c.cache_key == key)).first()
        if row is None:
            return None
        s.execute(cache_table.update().where(cache_table.c.cache_key == key).values(last_hit_at=__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    try:
        return json.loads(row["payload"])
    except (TypeError, ValueError):
        return None


def set_cached(stage: str, effective_inputs: Any, payload: Any, model_version: str = "",
               prompt_version: str = "", config_version: str = "") -> None:
    key, input_hash = cache_key(stage, effective_inputs, model_version, prompt_version, config_version)
    cache_table = Base.metadata.tables["artifact_cache"]
    with session_scope() as s:
        exists = s.execute(select(cache_table.c.cache_key).where(cache_table.c.cache_key == key)).first()
        values = dict(cache_key=key, stage=stage, model_version=model_version,
                      prompt_version=prompt_version, config_version=config_version,
                      input_hash=input_hash, payload=json.dumps(payload, ensure_ascii=False))
        if exists:
            s.execute(cache_table.update().where(cache_table.c.cache_key == key).values(payload=values["payload"]))
        else:
            s.execute(cache_table.insert().values(**values))
