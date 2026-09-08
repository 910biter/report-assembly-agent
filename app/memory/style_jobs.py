"""Background template/style learning jobs with lightweight progress state."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import shutil
import threading
import uuid
from pathlib import Path

from app.parsing import parse_file


_LOCK = threading.RLock()
_JOBS: dict[str, dict] = {}
_TERMINAL = {"completed", "failed"}


def create_job(files: list[dict], job_id: str | None = None) -> dict:
    job_id = str(job_id or uuid.uuid4().hex[:16])
    now = _now()
    job = {
        "id": job_id,
        "status": "queued",
        "phase": "queued",
        "message": "文件已上传，等待开始学习",
        "progress_percent": 0,
        "total_files": len(files),
        "processed_files": 0,
        "current_file": "",
        "successful_files": 0,
        "failed_files": 0,
        "failures": [],
        "variant_ids": [],
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
        "files": deepcopy(files),
    }
    with _LOCK:
        _JOBS[job_id] = job
    return _public(job)


def get_job(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(str(job_id))
        return _public(job) if job else None


def run_job(job_id: str) -> None:
    """Parse uploaded files and build StyleVariants after the request returns."""
    from app.memory import style

    with _LOCK:
        job = _JOBS.get(job_id)
        files = deepcopy(job.get("files") or []) if job else []
    if not files:
        _fail(job_id, "NO_UPLOADED_FILES")
        return
    reports: list[dict] = []
    failures: list[dict] = []
    total = len(files)
    try:
        _update(job_id, status="running", phase="parsing", message="正在解析模板与成品报告")
        for index, item in enumerate(files, start=1):
            filename = str(item.get("filename") or "")
            path = Path(str(item.get("path") or ""))
            _update(
                job_id,
                phase="parsing",
                current_file=filename,
                processed_files=index - 1,
                progress_percent=round((index - 1) / max(1, total) * 45),
                message=f"正在解析 {filename}",
            )
            try:
                units = parse_file(path)
                text = "\n".join(unit.content for unit in units if str(unit.content or "").strip())
                if not text:
                    raise ValueError("NO_TEXT_CONTENT")
                reports.append({
                    "filename": filename, "text": text, "path": str(path),
                    "asset_role": str(item.get("asset_role") or "auto"),
                })
            except Exception as exc:
                failures.append({"filename": filename, "error": str(exc)[:300]})
            _update(
                job_id,
                processed_files=index,
                successful_files=len(reports),
                failed_files=len(failures),
                failures=failures,
                progress_percent=round(index / max(1, total) * 45),
            )
        if not reports:
            _fail(job_id, "NO_PARSEABLE_FILES", failures=failures)
            return

        def progress(event: dict) -> None:
            phase = str(event.get("phase") or "learning")
            current = int(event.get("current") or 0)
            count = max(1, int(event.get("total") or 1))
            if phase == "classifying":
                percent = 45 + round(current / count * 20)
                message = "正在识别报告类型与样例角色"
            else:
                percent = 65 + round(current / count * 30)
                message = "正在提炼版式、语言与材料使用画像"
            _update(
                job_id,
                phase=phase,
                current_file=str(event.get("label") or ""),
                progress_percent=min(95, percent),
                message=message,
            )

        _update(job_id, phase="classifying", progress_percent=45, message="正在分析语言与结构")
        variants = style.analyze_library(reports, progress_callback=progress)
        _update(
            job_id,
            status="completed",
            phase="completed",
            message=f"学习完成，生成 {len(variants)} 个模板画像",
            progress_percent=100,
            current_file="",
            variant_ids=[int(item.id) for item in variants if item.id is not None],
            finished_at=_now(),
        )
    except Exception as exc:
        _fail(job_id, str(exc), failures=failures)
    finally:
        for item in files:
            path = Path(str(item.get("path") or ""))
            try:
                path.unlink(missing_ok=True)
                parent = path.parent
                if parent.name == job_id:
                    shutil.rmtree(parent, ignore_errors=True)
            except Exception:
                pass


def _update(job_id: str, **values) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job or job.get("status") in _TERMINAL:
            return
        job.update(values)
        job["updated_at"] = _now()


def _fail(job_id: str, error: str, failures: list[dict] | None = None) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job.update({
            "status": "failed",
            "phase": "failed",
            "message": "模板学习失败",
            "error": str(error)[:500],
            "failures": failures if failures is not None else job.get("failures", []),
            "finished_at": _now(),
            "updated_at": _now(),
        })


def _public(job: dict) -> dict:
    return {key: deepcopy(value) for key, value in job.items() if key != "files"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
