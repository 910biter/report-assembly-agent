"""Cooperative and immediate task cancellation signals."""
from __future__ import annotations

import threading

_LOCK = threading.Lock()
_EVENTS: dict[str, threading.Event] = {}
_CLIENTS: dict[str, set[object]] = {}
_ACTIVE_TASK_ID = ""


def begin_task(task_id: str) -> None:
    global _ACTIVE_TASK_ID
    if not task_id:
        return
    with _LOCK:
        _EVENTS[task_id] = threading.Event()
        _CLIENTS[task_id] = set()
        _ACTIVE_TASK_ID = task_id


def end_task(task_id: str) -> None:
    global _ACTIVE_TASK_ID
    if not task_id:
        return
    with _LOCK:
        _EVENTS.pop(task_id, None)
        _CLIENTS.pop(task_id, None)
        if _ACTIVE_TASK_ID == task_id:
            _ACTIVE_TASK_ID = ""


def active_task_id() -> str:
    with _LOCK:
        return _ACTIVE_TASK_ID


def register_client(task_id: str, client: object) -> None:
    with _LOCK:
        _CLIENTS.setdefault(task_id, set()).add(client)


def unregister_client(task_id: str, client: object) -> None:
    with _LOCK:
        _CLIENTS.get(task_id, set()).discard(client)


def request_pause(task_id: str) -> None:
    if not task_id:
        return
    with _LOCK:
        _EVENTS.setdefault(task_id, threading.Event()).set()
        clients = list(_CLIENTS.get(task_id, set()))
    for client in clients:
        close = getattr(client, "close", None)
        if close:
            try:
                close()
            except Exception:
                pass


def clear_pause(task_id: str) -> None:
    if not task_id:
        return
    with _LOCK:
        event = _EVENTS.get(task_id)
        if event is not None:
            event.clear()


def is_paused(task_id: str) -> bool:
    with _LOCK:
        event = _EVENTS.get(task_id)
        return bool(event and event.is_set())


def paused_event(task_id: str) -> threading.Event | None:
    with _LOCK:
        return _EVENTS.get(task_id)


def raise_if_paused(task_id: str) -> None:
    if is_paused(task_id):
        raise RuntimeError("TASK_PAUSED")
