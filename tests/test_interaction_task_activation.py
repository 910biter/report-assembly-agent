from contextlib import contextmanager

from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Update

from app import interaction


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Mappings:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return _Rows(self.rows)


class _ProposalSession:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        if isinstance(statement, Update):
            return _Mappings([])
        return _Mappings([{
            "id": 5,
            "task_id": "task-1",
            "status": "accepted",
            "execution_status": "waiting",
        }])


def test_pending_proposals_are_not_claimed_in_a_separate_preparation_transaction(monkeypatch):
    session = _ProposalSession()

    @contextmanager
    def fake_session_scope():
        yield session

    monkeypatch.setattr(interaction, "session_scope", fake_session_scope)

    proposals = interaction._claim_revision_proposals("task-1")

    assert [item["id"] for item in proposals] == [5]
    assert proposals[0]["execution_status"] == "waiting"
    assert not any(isinstance(statement, Update) for statement in session.statements)


def test_stale_preparation_failure_does_not_fail_proposals_claimed_by_another_run(monkeypatch):
    session = _ProposalSession()

    @contextmanager
    def fake_session_scope():
        yield session

    monkeypatch.setattr(interaction, "session_scope", fake_session_scope)

    interaction._mark_revision_preparation_failed(
        "task-1", [5], error="stale preparation", prepared={},
    )

    proposal_update = next(item for item in session.statements if isinstance(item, Update))
    compiled = proposal_update.compile(dialect=postgresql.dialect())
    assert "execution_status =" in str(compiled)
    assert "waiting" in compiled.params.values()
