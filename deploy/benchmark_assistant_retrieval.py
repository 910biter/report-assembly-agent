#!/usr/bin/env python3
"""Compare bounded assistant source retrieval with its exhaustive baseline.

Input JSON is a list of cases, e.g.:
[{"query": "具体问题", "material_id": 12, "expected_unit_ids": [34, 36]}]
Expected IDs are optional human-labeled source units; without them the script
reports overlap against the exhaustive implementation, not factual accuracy.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.control_agent import _search_material_units
from app.db import session_scope
from app.infrastructure.orm import ORMMaterial, ORMUnit
from app.memory import short_term
from app.models import Unit
from app.retrieval.embedder import embedding_cache_stats
from app.retrieval.rag import hybrid_retrieve_units


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return round(ordered[index], 3)


def _exhaustive_search(task_id: str, query: str, material_id: int | None, limit: int) -> dict:
    task = short_term.load_task(task_id) or {}
    material_ids = [int(value) for value in task.get("material_ids") or [] if str(value).isdigit()]
    if material_id is not None:
        if int(material_id) not in material_ids:
            return {"items": [], "candidate_units": 0}
        material_ids = [int(material_id)]
    with session_scope() as session:
        rows = session.execute(
            select(
                ORMUnit.c.id, ORMUnit.c.material_id, ORMUnit.c.kind, ORMUnit.c.page,
                ORMUnit.c.paragraph, ORMUnit.c.content, ORMMaterial.c.filename,
            ).join(ORMMaterial, ORMMaterial.c.id == ORMUnit.c.material_id)
            .where(ORMUnit.c.material_id.in_(material_ids))
        ).mappings().all() if material_ids else []
    grouped: dict[int, list[Unit]] = {}
    by_id = {}
    for row in rows:
        if not str(row["content"] or "").strip():
            continue
        unit = Unit(
            id=int(row["id"]), material_id=int(row["material_id"]),
            kind=str(row["kind"] or "text"), content=str(row["content"]),
            page=row["page"], paragraph=row["paragraph"], metadata_json="{}",
        )
        grouped.setdefault(unit.material_id, []).append(unit)
        by_id[unit.id] = dict(row)
    hits, _metadata = hybrid_retrieve_units(
        query, grouped, top_k=min(max(1, limit), 8),
        filters={"material_id": material_ids},
    )
    return {
        "items": [
            {"unit_id": hit.unit_id, "material_id": int(by_id[hit.unit_id]["material_id"]),
             "filename": str(by_id[hit.unit_id]["filename"] or ""),
             "page": by_id[hit.unit_id]["page"], "paragraph": by_id[hit.unit_id]["paragraph"]}
            for hit in hits if hit.unit_id in by_id
        ],
        "candidate_units": len(by_id),
    }


def _run_once(mode: str, task_id: str, case: dict, limit: int) -> tuple[dict, float]:
    started = time.perf_counter()
    if mode == "bounded":
        result = _search_material_units(
            task_id, query=str(case["query"]),
            material_id=int(case["material_id"]) if case.get("material_id") else None,
            limit=limit,
        )
    else:
        result = _exhaustive_search(
            task_id, str(case["query"]),
            int(case["material_id"]) if case.get("material_id") else None, limit,
        )
    return result, (time.perf_counter() - started) * 1000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--cases", required=True, type=Path, help="JSON list of queries and optional labeled unit IDs")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        parser.error("cases must be a non-empty JSON array")

    embedding_cache_stats(reset=True)
    results = []
    for case_index, case in enumerate(cases):
        if not isinstance(case, dict) or not str(case.get("query") or "").strip():
            parser.error(f"case {case_index} needs a non-empty query")
        # Warm both paths once; warmup is excluded from all latency samples.
        _run_once("bounded", args.task_id, case, args.limit)
        _run_once("exhaustive", args.task_id, case, args.limit)
        samples = {"bounded": [], "exhaustive": []}
        latest = {}
        for repeat in range(max(1, args.repeats)):
            order = ("bounded", "exhaustive") if repeat % 2 == 0 else ("exhaustive", "bounded")
            for mode in order:
                latest[mode], elapsed = _run_once(mode, args.task_id, case, args.limit)
                samples[mode].append(elapsed)
        bounded_ids = [int(item["unit_id"]) for item in latest["bounded"].get("items") or []]
        exhaustive_ids = [int(item["unit_id"]) for item in latest["exhaustive"].get("items") or []]
        expected = {int(value) for value in case.get("expected_unit_ids") or []}
        result = {
            "query": case["query"],
            "material_id": case.get("material_id"),
            "bounded": {
                "latency_ms_p50": _percentile(samples["bounded"], 0.50),
                "latency_ms_p95": _percentile(samples["bounded"], 0.95),
                "candidate_units": int(latest["bounded"].get("retrieval_candidate_units") or 0),
                "returned_unit_ids": bounded_ids,
            },
            "exhaustive_baseline": {
                "latency_ms_p50": _percentile(samples["exhaustive"], 0.50),
                "latency_ms_p95": _percentile(samples["exhaustive"], 0.95),
                "candidate_units": int(latest["exhaustive"].get("candidate_units") or 0),
                "returned_unit_ids": exhaustive_ids,
            },
            "overlap_recall_at_k_vs_exhaustive": round(
                len(set(bounded_ids) & set(exhaustive_ids)) / max(1, len(set(exhaustive_ids))), 4,
            ),
            "labeled_recall_at_k": (
                round(len(set(bounded_ids) & expected) / len(expected), 4) if expected else None
            ),
            "quality_note": (
                "labeled_recall_at_k uses supplied human-labeled unit IDs"
                if expected else "overlap is only agreement with exhaustive retrieval; it is not factual accuracy"
            ),
        }
        results.append(result)

    report = {
        "task_id": args.task_id,
        "repeats": max(1, args.repeats),
        "top_k": min(max(1, args.limit), 8),
        "cases": results,
        "summary": {
            "bounded_p50_ms": _percentile([case["bounded"]["latency_ms_p50"] for case in results], 0.50),
            "exhaustive_p50_ms": _percentile([case["exhaustive_baseline"]["latency_ms_p50"] for case in results], 0.50),
            "bounded_candidate_units": sum(case["bounded"]["candidate_units"] for case in results),
            "exhaustive_candidate_units": sum(case["exhaustive_baseline"]["candidate_units"] for case in results),
            "embedding_cache": embedding_cache_stats(),
        },
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
