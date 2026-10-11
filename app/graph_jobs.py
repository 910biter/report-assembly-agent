"""Durable PostgreSQL queue for explicit task graph builds."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid

from sqlalchemy import select, update

from app.db import session_scope
from app.infrastructure.orm import ORMGraphJob, ORMShortMemory
from app.workflow.execution import execution_slot

_LOG = logging.getLogger(__name__)
_POLL_SECONDS = 1.0
_WORKER_LOCK = threading.Lock()
_WORKER: threading.Thread | None = None
_STOP = threading.Event()
_WAKE = threading.Event()


def submit(task_id: str) -> dict:
    """Persist an explicit graph request; coalesce duplicate active requests."""
    now = time.time()
    with session_scope() as session:
        task_row = session.execute(
            select(ORMShortMemory.c.payload)
            .where(ORMShortMemory.c.task_id == task_id)
            .with_for_update()
        ).first()
        if task_row is None:
            raise KeyError("TASK_NOT_FOUND")
        row = session.execute(
            select(ORMGraphJob).where(ORMGraphJob.c.task_id == task_id).with_for_update()
        ).mappings().first()
        if row and str(row["status"]) in {"queued", "running"}:
            result = _detail(row)
        else:
            job_id = uuid.uuid4().hex
            progress = {"phase": "queued", "completed_batches": 0, "total_batches": 0}
            values = {
                "job_id": job_id,
                "source_run_id": "",
                "status": "queued",
                "requested_at": now,
                "started_at": 0,
                "finished_at": 0,
                "heartbeat_at": now,
                "attempts": 0,
                "progress_json": json.dumps(progress),
                "result_json": "{}",
                "error": "",
            }
            if row:
                session.execute(
                    update(ORMGraphJob).where(ORMGraphJob.c.task_id == task_id).values(**values)
                )
            else:
                session.execute(ORMGraphJob.insert().values(task_id=task_id, **values))
            payload = json.loads(task_row[0] or "{}")
            task_jobs = dict(payload.get("background_jobs") or {})
            task_jobs["graph_rebuild"] = {"job_id": job_id, "status": "queued", "queued_at": now}
            payload["background_jobs"] = task_jobs
            payload["graph_status"] = {"status": "queued"}
            session.execute(
                update(ORMShortMemory).where(ORMShortMemory.c.task_id == task_id)
                .values(payload=json.dumps(payload, ensure_ascii=False))
            )
            result = {
                "task_id": task_id, "job_id": job_id, "status": "queued",
                "progress": progress, "attempts": 0, "waiting_for_main": False,
            }
    _WAKE.set()
    result["waiting_for_main"] = result["status"] == "queued" and _main_work_pending()
    return result


def get(task_id: str) -> dict | None:
    with session_scope() as session:
        row = session.execute(
            select(ORMGraphJob).where(ORMGraphJob.c.task_id == task_id)
        ).mappings().first()
    if not row:
        return None
    detail = _detail(row)
    detail["waiting_for_main"] = detail["status"] == "queued" and _main_work_pending()
    return detail


def start_worker() -> None:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER and _WORKER.is_alive():
            return
        _STOP.clear()
        _WORKER = threading.Thread(target=_worker_loop, daemon=True, name="graph-job-worker")
        _WORKER.start()


def stop_worker() -> None:
    _STOP.set()
    _WAKE.set()
    with _WORKER_LOCK:
        worker = _WORKER
    if worker and worker.is_alive():
        worker.join(timeout=2)


def run_once() -> bool:
    """Run one persisted job only when the shared model slot is available."""
    with execution_slot(wait=False) as slot:
        if slot is None:
            return False
        _recover_interrupted_jobs()
        if _main_work_pending():
            return False
        with session_scope() as session:
            row = session.execute(
                select(ORMGraphJob).where(ORMGraphJob.c.status == "queued")
                .order_by(ORMGraphJob.c.requested_at, ORMGraphJob.c.task_id)
            ).mappings().first()
            if row is None:
                return False
            task_id = str(row["task_id"])
            job_id = str(row["job_id"])
        # Keep the task lock for the whole build, not merely while claiming it.
        with slot.task(task_id):
            try:
                if _main_work_pending():
                    return False
                with session_scope() as session:
                    current = session.execute(
                        select(ORMGraphJob).where(
                            ORMGraphJob.c.task_id == task_id,
                            ORMGraphJob.c.job_id == job_id,
                            ORMGraphJob.c.status == "queued",
                        ).with_for_update(skip_locked=True)
                    ).mappings().first()
                    if current is None:
                        return False
                    task_row = session.execute(
                        select(ORMShortMemory.c.payload).where(
                            ORMShortMemory.c.task_id == task_id
                        )
                    ).first()
                    if task_row is None:
                        session.execute(update(ORMGraphJob).where(
                            ORMGraphJob.c.task_id == task_id,
                        ).values(status="cancelled", finished_at=time.time(), error="TASK_NOT_FOUND"))
                        return True
                    task = json.loads(task_row[0] or "{}")
                    now = time.time()
                    attempts = int(current["attempts"] or 0) + 1
                    progress = {"phase": "loading_facts", "completed_batches": 0, "total_batches": 0}
                    session.execute(update(ORMGraphJob).where(
                        ORMGraphJob.c.task_id == task_id,
                        ORMGraphJob.c.job_id == job_id,
                        ORMGraphJob.c.status == "queued",
                    ).values(
                        source_run_id=str(task.get("run_id") or ""),
                        status="running", started_at=now, heartbeat_at=now,
                        attempts=attempts, progress_json=json.dumps(progress), error="",
                    ))
                    payload = json.loads(task_row[0] or "{}")
                    jobs = dict(payload.get("background_jobs") or {})
                    job_state = dict(jobs.get("graph_rebuild") or {})
                    job_state.update({"job_id": job_id, "status": "running", "started_at": now})
                    jobs["graph_rebuild"] = job_state
                    payload["background_jobs"] = jobs
                    payload["graph_status"] = {"status": "running"}
                    session.execute(update(ORMShortMemory).where(
                        ORMShortMemory.c.task_id == task_id
                    ).values(payload=json.dumps(payload, ensure_ascii=False)))
                _execute_job(task_id, job_id, slot, slot.check)
            except Exception as exc:
                _finish(task_id, job_id, "queued" if str(exc) == "EXECUTION_LOCK_LOST" else "failed", error=str(exc))
                if str(exc) != "EXECUTION_LOCK_LOST":
                    _LOG.exception("Graph build job %s failed", job_id)
        return True


def _execute_job(task_id: str, job_id: str, slot, check) -> None:
    from app.workflow.controller import WorkflowController

    controller = WorkflowController(task_id)
    facts = controller._facts()
    if not facts:
        _finish(task_id, job_id, "failed", error="TASK_HAS_NO_FACTS")
        return
    active_fact_ids = [int(item["id"]) for item in facts if item.get("id") is not None]

    def progress(event: dict) -> None:
        check()
        _update_progress(task_id, job_id, event)

    controller._update(graph_status={"status": "running", "fact_count": len(facts)})
    status = controller._build_task_graph(facts, progress_callback=progress)
    check()
    artifact_status = (
        "done" if status.get("status") in {"ready", "reused"}
        else "partial" if status.get("status") == "partial_ready"
        else "skipped" if status.get("status") == "skipped"
        else "failed"
    )
    controller._record_artifact("graph", {"graph_status": status}, status=artifact_status)
    terminal = "done" if status.get("status") in {"ready", "reused", "skipped"} else (
        "partial" if status.get("status") == "partial_ready" else "failed"
    )
    _finish(task_id, job_id, terminal, result={**status, "fact_count": len(active_fact_ids)})


def _update_progress(task_id: str, job_id: str, event: dict) -> None:
    now = time.time()
    with session_scope() as session:
        session.execute(update(ORMGraphJob).where(
            ORMGraphJob.c.task_id == task_id,
            ORMGraphJob.c.job_id == job_id,
            ORMGraphJob.c.status == "running",
        ).values(progress_json=json.dumps(event, ensure_ascii=False), heartbeat_at=now))


def _finish(task_id: str, job_id: str, status: str, *, result: dict | None = None,
            error: str = "") -> None:
    now = time.time()
    with session_scope() as session:
        session.execute(update(ORMGraphJob).where(
            ORMGraphJob.c.task_id == task_id,
            ORMGraphJob.c.job_id == job_id,
        ).values(
            status=status, finished_at=now, heartbeat_at=now,
            result_json=json.dumps(result or {}, ensure_ascii=False), error=str(error)[:1000],
        ))
        row = session.execute(select(ORMShortMemory.c.payload).where(
            ORMShortMemory.c.task_id == task_id,
        ).with_for_update()).first()
        if row is None:
            return
        payload = json.loads(row[0] or "{}")
        jobs = dict(payload.get("background_jobs") or {})
        current = dict(jobs.get("graph_rebuild") or {})
        if current.get("job_id") == job_id:
            current.update({"status": status, "finished_at": now, "error": str(error)[:1000]})
            jobs["graph_rebuild"] = current
        payload["background_jobs"] = jobs
        if status == "queued":
            payload["graph_status"] = {"status": "queued", "error": str(error)[:300]}
        elif result:
            payload["graph_status"] = dict(result)
        else:
            payload["graph_status"] = {"status": status, "error": str(error)[:300]}
        session.execute(update(ORMShortMemory).where(
            ORMShortMemory.c.task_id == task_id,
        ).values(payload=json.dumps(payload, ensure_ascii=False)))


def _recover_interrupted_jobs() -> None:
    """Requeue any running row after acquiring the shared execution lock."""
    with session_scope() as session:
        result = session.execute(update(ORMGraphJob).where(
            ORMGraphJob.c.status == "running",
        ).values(status="queued", started_at=0, heartbeat_at=time.time(),
                 progress_json=json.dumps({"phase": "resuming", "completed_batches": 0, "total_batches": 0})))
        if result.rowcount:
            for task_id in session.execute(
                select(ORMGraphJob.c.task_id).where(ORMGraphJob.c.status == "queued")
            ).scalars():
                row = session.execute(select(ORMShortMemory.c.payload).where(
                    ORMShortMemory.c.task_id == task_id
                ).with_for_update()).first()
                if row is None:
                    continue
                payload = json.loads(row[0] or "{}")
                jobs = dict(payload.get("background_jobs") or {})
                job = dict(jobs.get("graph_rebuild") or {})
                if job.get("status") == "running":
                    job["status"] = "queued"
                    jobs["graph_rebuild"] = job
                    payload["background_jobs"] = jobs
                    payload["graph_status"] = {"status": "queued"}
                    session.execute(update(ORMShortMemory).where(
                        ORMShortMemory.c.task_id == task_id
                    ).values(payload=json.dumps(payload, ensure_ascii=False)))


def _main_work_pending() -> bool:
    from app.workflow.queue import task_queue_status
    from app.memory import short_term
    queue = task_queue_status()
    if queue.get("running_task_id") or queue.get("queued_task_ids"):
        return True
    for _task_id, task in short_term.list_tasks():
        status = str((task.get("queue_status") or {}).get("status") or "")
        stage = str(task.get("stage") or "")
        if status in {"queued", "running", "pause_requested"} and stage not in {"review", "done", "failed", "paused"}:
            return True
    return False


def _worker_loop() -> None:
    while not _STOP.is_set():
        try:
            worked = run_once()
        except Exception:
            _LOG.exception("Graph queue poll failed")
            worked = False
        if not worked:
            _WAKE.wait(_POLL_SECONDS)
        _WAKE.clear()


def _detail(row) -> dict:
    if row is None:
        return {}
    try:
        progress = json.loads(row["progress_json"] or "{}")
    except (TypeError, ValueError):
        progress = {}
    try:
        result = json.loads(row["result_json"] or "{}")
    except (TypeError, ValueError):
        result = {}
    return {
        "task_id": str(row["task_id"]), "job_id": str(row["job_id"]),
        "source_run_id": str(row["source_run_id"] or ""), "status": str(row["status"]),
        "requested_at": float(row["requested_at"] or 0),
        "started_at": float(row["started_at"] or 0), "finished_at": float(row["finished_at"] or 0),
        "heartbeat_at": float(row["heartbeat_at"] or 0), "attempts": int(row["attempts"] or 0),
        "progress": progress, "result": result, "error": str(row["error"] or ""),
    }
