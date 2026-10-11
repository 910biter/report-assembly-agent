"""Bounded repair of explicit references; no similarity search or invented IDs."""
from copy import deepcopy
import json

from sqlalchemy import select

from app.db import session_scope
from app.infrastructure.orm import ORMFact, ORMInference
from app.planning.narrative import unit_evidence_ids


class EvidenceReferenceBlocked(ValueError):
    pass


def _load(table, task_id, identifier):
    with session_scope() as session:
        row = session.execute(select(table).where(
            table.c.id == identifier, table.c.task_id == task_id,
        )).mappings().first()
    if row is None:
        return None
    result = dict(row)
    for key in ("semantic_profile", "source_semantics"):
        if key + "_json" in result:
            result[key] = json.loads(result[key + "_json"] or ("[]" if key == "source_semantics" else "{}"))
    return result


def repair_references(plan, facts, inferences, task_id, *, load=_load, max_hops=4):
    """Restore omitted task-owned records or follow explicit replacement links.

    Missing references never expand to all chapter evidence. Changes are made
    to a copy and returned with an audit; original planning records stay intact.
    """
    result = deepcopy(plan or {})
    fact_ids, inference_ids = unit_evidence_ids(result)
    def collect(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in {"fact_ids", "facts", "inference_ids", "inferences"} and isinstance(child, list):
                    target = fact_ids if key in {"fact_ids", "facts"} else inference_ids
                    target.extend(int(item) for item in child if str(item).isdigit())
                elif isinstance(child, (dict, list)):
                    collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)
    collect(result)
    fact_ids, inference_ids = list(dict.fromkeys(fact_ids)), list(dict.fromkeys(inference_ids))
    pools = [{int(row["id"]): row for row in facts}, {int(row["id"]): row for row in inferences}]
    replacements = [{}, {}]
    changes = []
    for kind, identifiers in enumerate((fact_ids, inference_ids)):
        table = ORMFact if kind == 0 else ORMInference
        link = "superseded_by_fact_id" if kind == 0 else "superseded_by_inference_id"
        for identifier in identifiers:
            current, visited = identifier, set()
            for _ in range(max_hops + 1):
                if current in visited:
                    raise EvidenceReferenceBlocked(f"WRITER_EVIDENCE_REFERENCE_CYCLE:{identifier}")
                visited.add(current)
                row = pools[kind].get(current)
                if row is None:
                    row = load(table, task_id, current) if task_id else None
                if row is None or row.get("lifecycle_status", "active") != "active" and not row.get(link):
                    raise EvidenceReferenceBlocked(f"WRITER_EVIDENCE_REFERENCE_MISSING:{identifier}")
                successor = row.get(link)
                if successor:
                    current = int(successor)
                    continue
                row = dict(row)
                if kind == 1 and isinstance(row.get("based_fact_ids"), str):
                    row["based_fact_ids"] = json.loads(row["based_fact_ids"] or "[]")
                pools[kind][current] = row
                replacements[kind][identifier] = current
                if current != identifier or identifier not in {int(value["id"]) for value in (facts if kind == 0 else inferences)}:
                    changes.append({"kind": "fact" if kind == 0 else "inference", "from_id": identifier, "to_id": current})
                break
            else:
                raise EvidenceReferenceBlocked(f"WRITER_EVIDENCE_REPAIR_LIMIT:{identifier}")
    # Every selected inference still needs its material fact chain.
    for identifier in replacements[1].values():
        for fid in pools[1][identifier].get("based_fact_ids") or []:
            if int(fid) not in pools[0]:
                closure, recovered, _, audit = repair_references(
                    {"fact_ids": [int(fid)]}, list(pools[0].values()), [], task_id,
                    load=load, max_hops=max_hops,
                )
                pools[0].update({int(row["id"]): row for row in recovered})
                replacements[0][int(fid)] = closure["fact_ids"][0]
                changes.extend(audit)
        pools[1][identifier] = {**pools[1][identifier], "based_fact_ids": [
            replacements[0].get(int(fid), int(fid)) for fid in pools[1][identifier].get("based_fact_ids") or []]}

    def rewrite(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"fact_ids", "facts", "inference_ids", "inferences"} and isinstance(child, list):
                    mapping = replacements[0 if key in {"fact_ids", "facts"} else 1]
                    value[key] = [mapping.get(int(item), int(item)) for item in child]
                else:
                    rewrite(child)
        elif isinstance(value, list):
            for child in value:
                rewrite(child)
    rewrite(result)
    return result, list(pools[0].values()), list(pools[1].values()), changes
