import json

from sqlalchemy.sql.dml import Insert

from app import report_versions


class _Result:
    def __init__(self, row=None, inserted_primary_key=None):
        self.row = row
        self.inserted_primary_key = inserted_primary_key or []

    def mappings(self):
        return self

    def first(self):
        return self.row


class _Session:
    def __init__(self, latest, delta):
        self.latest = latest
        self.delta = delta
        self.inserted = False

    def execute(self, statement):
        if isinstance(statement, Insert):
            self.inserted = True
            return _Result(inserted_primary_key=[91])
        if self.inserted:
            return _Result(self.delta)
        return _Result(self.latest)


def test_delta_created_inside_caller_transaction_returns_full_delta(monkeypatch):
    latest = {
        "id": 12,
        "report_id": 7,
        "task_id": "task-1",
        "version_no": 2,
        "material_fingerprints": json.dumps([{"material_id": 1, "file_hash": "old"}]),
        "fact_snapshot": "[]",
        "inference_snapshot": "[]",
        "conflict_snapshot": "[]",
    }
    added_materials = [{"material_id": 2, "file_hash": "new", "filename": "new.pdf"}]
    delta_row = {
        "id": 91,
        "report_id": 7,
        "from_version_id": 12,
        "to_version_id": None,
        "status": "preview",
        "update_reason": "new material",
        "added_materials": json.dumps(added_materials),
        "duplicate_materials": "[]",
        "added_facts": "[]",
        "modified_facts": "[]",
        "deprecated_facts": "[]",
        "added_inferences": "[]",
        "modified_inferences": "[]",
        "deprecated_inferences": "[]",
        "new_conflicts": "[]",
        "resolved_conflicts": "[]",
        "affected_chapters": "[]",
        "structure_changes": "[]",
        "evidence_changes": "{}",
        "metadata_json": "{}",
        "created_at": "2026-09-23 00:00:00",
    }
    session = _Session(latest, delta_row)
    monkeypatch.setattr(report_versions, "build_report_snapshot", lambda *args, **kwargs: {
        "task_id": "task-1", "fact_snapshot": [], "inference_snapshot": [],
        "conflict_snapshot": [], "sentence_snapshot": [],
    })
    monkeypatch.setattr(report_versions, "_materials_in_session", lambda *args, **kwargs: added_materials)
    monkeypatch.setattr(report_versions, "_object_delta", lambda *_args: {
        "added": [], "modified": [], "deprecated": [],
    })
    monkeypatch.setattr(report_versions, "_affected_chapters", lambda *_args: [])

    result = report_versions.create_incremental_delta(
        7, [2], update_reason="new material", run_id="run-3", _session=session
    )

    assert result["id"] == 91
    assert result["added_materials"] == added_materials
    assert result["from_version_id"] == 12
