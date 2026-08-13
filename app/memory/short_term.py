"""短期记忆:当前任务上下文(task_id → payload JSON)。"""
import json

from app.db import connect


def save_task(task_id: str, payload: dict) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO short_memory(task_id, payload) VALUES(?, ?) "
            "ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload",
            (task_id, json.dumps(payload, ensure_ascii=False)),
        )


def load_task(task_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT payload FROM short_memory WHERE task_id=?", (task_id,)).fetchone()
    return json.loads(row["payload"]) if row else None


def delete_task(task_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM short_memory WHERE task_id=?", (task_id,))


def update_task(task_id: str, **fields) -> dict:
    """加载任务上下文,更新指定字段后保存,返回新 payload。"""
    payload = load_task(task_id) or {}
    payload.update(fields)
    save_task(task_id, payload)
    return payload
