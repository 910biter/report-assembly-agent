"""Validation helpers for workflow progress payloads shown to users."""
from __future__ import annotations


def progress_for_display(value: object) -> dict:
    """Drop impossible counters instead of presenting corrupt history as progress."""
    if not isinstance(value, dict):
        return {}
    progress = dict(value)
    if "done" not in progress or "total" not in progress:
        return progress
    try:
        done = int(progress["done"])
        total = int(progress["total"])
    except (TypeError, ValueError):
        done, total = -1, -1
    if done < 0 or total < 0 or done > total:
        progress.pop("done", None)
        progress.pop("total", None)
        progress["status"] = "stale"
    return progress
