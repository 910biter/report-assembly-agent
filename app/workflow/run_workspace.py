"""Private plan and report rows for a report-writing run.

The working report is not referenced by the task until all writing and
post-processing succeeds. Promotion moves its sentences into the stable
report ID in one transaction, preserving the report's version history.
"""

import hashlib
import json

from sqlalchemy import delete, insert, select, update

from app.db import session_scope
from app.memory import short_term
from app.report_versions import ensure_report_version
from app.infrastructure.orm import (
    ORMPlan, ORMReport, ORMSentence, ORMSentenceFact, ORMSentenceInference,
)


def _report_fingerprint(session, report_id: int) -> str:
    report = session.execute(
        select(ORMReport).where(ORMReport.c.id == report_id)
    ).mappings().one()
    sentences = session.execute(
        select(ORMSentence).where(ORMSentence.c.report_id == report_id)
        .order_by(ORMSentence.c.id)
    ).mappings().all()
    state = {
        "report": {key: report[key] for key in ("plan_id", "title", "style_profile_id", "status")},
        "sentences": [
            {key: row[key] for key in ORMSentence.c.keys() if key != "report_id"}
            for row in sentences
        ],
    }
    return hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def fork_plan(plan_id: int, report_id: int) -> tuple[int, int]:
    """Return (private plan ID, live plan ID) for an existing report."""
    with session_scope() as session:
        report = session.execute(
            select(ORMReport.c.plan_id).where(ORMReport.c.id == report_id)
        ).mappings().one()
        live_plan_id = int(report["plan_id"])
        source = session.execute(
            select(ORMPlan).where(ORMPlan.c.id == plan_id)
        ).mappings().one()
        values = {key: source[key] for key in ORMPlan.c.keys() if key not in {"id", "created_at"}}
        result = session.execute(insert(ORMPlan).values(**values))
        return int(result.inserted_primary_key[0]), live_plan_id


def create_working_report(report_id: int, plan_id: int) -> tuple[int, str]:
    """Copy the current report without exposing the copy through the task."""
    with session_scope() as session:
        report = session.execute(
            select(ORMReport).where(ORMReport.c.id == report_id).with_for_update()
        ).mappings().one()
        fingerprint = _report_fingerprint(session, report_id)
        values = {key: report[key] for key in ORMReport.c.keys() if key not in {"id", "created_at"}}
        values["plan_id"] = plan_id
        values["title"] = session.execute(
            select(ORMPlan.c.title).where(ORMPlan.c.id == plan_id)
        ).scalar_one()
        staged = session.execute(insert(ORMReport).values(**values))
        working_id = int(staged.inserted_primary_key[0])
        sentences = session.execute(
            select(ORMSentence).where(ORMSentence.c.report_id == report_id)
            .order_by(ORMSentence.c.id)
        ).mappings().all()
        sentence_ids = [int(row["id"]) for row in sentences]
        facts = session.execute(
            select(ORMSentenceFact).where(ORMSentenceFact.c.sentence_id.in_(sentence_ids))
        ).mappings().all() if sentence_ids else []
        inferences = session.execute(
            select(ORMSentenceInference).where(ORMSentenceInference.c.sentence_id.in_(sentence_ids))
        ).mappings().all() if sentence_ids else []
        copied: dict[int, int] = {}
        for row in sentences:
            values = {key: row[key] for key in ORMSentence.c.keys() if key != "id"}
            values["report_id"] = working_id
            result = session.execute(insert(ORMSentence).values(**values))
            copied[int(row["id"])] = int(result.inserted_primary_key[0])
        for row in facts:
            session.execute(insert(ORMSentenceFact).values(
                sentence_id=copied[int(row["sentence_id"])], fact_id=row["fact_id"],
            ))
        for row in inferences:
            session.execute(insert(ORMSentenceInference).values(
                sentence_id=copied[int(row["sentence_id"])], inference_id=row["inference_id"],
            ))
        return working_id, fingerprint


def promote_working_report(report_id: int, working_id: int, expected_fingerprint: str,
                           task_id: str) -> dict:
    """Atomically replace live sentences and plan pointer after checking edits."""
    if report_id == working_id:
        raise ValueError("WORKING_REPORT_MUST_BE_PRIVATE")
    with session_scope() as session:
        session.execute(select(ORMReport.c.id).where(ORMReport.c.id == report_id).with_for_update()).one()
        if _report_fingerprint(session, report_id) != expected_fingerprint:
            raise ValueError("REPORT_CHANGED_DURING_RUN")
        working = session.execute(
            select(ORMReport).where(ORMReport.c.id == working_id).with_for_update()
        ).mappings().one()
        sentence_ids = list(session.execute(
            select(ORMSentence.c.id).where(ORMSentence.c.report_id == report_id)
        ).scalars())
        if sentence_ids:
            session.execute(delete(ORMSentenceFact).where(ORMSentenceFact.c.sentence_id.in_(sentence_ids)))
            session.execute(delete(ORMSentenceInference).where(ORMSentenceInference.c.sentence_id.in_(sentence_ids)))
            session.execute(delete(ORMSentence).where(ORMSentence.c.id.in_(sentence_ids)))
        session.execute(update(ORMSentence).where(
            ORMSentence.c.report_id == working_id
        ).values(report_id=report_id))
        session.execute(update(ORMReport).where(ORMReport.c.id == report_id).values(
            plan_id=working["plan_id"], title=working["title"],
            style_profile_id=working["style_profile_id"], status=working["status"],
        ))
        session.execute(delete(ORMReport).where(ORMReport.c.id == working_id))
        ensure_report_version(
            report_id, task_id=task_id, status="final",
            change_summary="本轮写作完成快照", _session=session,
        )
        def activate(current: dict, _session) -> dict:
            if int(current.get("report_id") or 0) != report_id:
                raise ValueError("REPORT_POINTER_CHANGED_DURING_RUN")
            current.update(
                working_report_id=None, working_report_hash="", working_report_run_id="",
                working_plan_id=None, working_plan_run_id="", working_base_plan_id=None,
                working_base_plan_title=None, working_base_plan_frozen=None,
            )
            return current

        return short_term.transition_task(task_id, activate, _session=session)


def discard_working_report(report_id: int, working_id: int) -> None:
    if not working_id or working_id == report_id:
        return
    with session_scope() as session:
        sentence_ids = list(session.execute(
            select(ORMSentence.c.id).where(ORMSentence.c.report_id == working_id)
        ).scalars())
        if sentence_ids:
            session.execute(delete(ORMSentenceFact).where(ORMSentenceFact.c.sentence_id.in_(sentence_ids)))
            session.execute(delete(ORMSentenceInference).where(ORMSentenceInference.c.sentence_id.in_(sentence_ids)))
            session.execute(delete(ORMSentence).where(ORMSentence.c.id.in_(sentence_ids)))
        session.execute(delete(ORMReport).where(ORMReport.c.id == working_id))
