from contextlib import contextmanager
from unittest.mock import patch

from app.evidence.extractor import save_claim
from app.models.material import Claim


class _FakeInsert:
    def __init__(self, recorder: list[dict]):
        self.recorder = recorder

    def values(self, **kwargs):
        self.recorder.append(kwargs)
        return self


class _FakeClaimTable:
    def __init__(self, recorder: list[dict]):
        self.recorder = recorder

    def insert(self):
        return _FakeInsert(self.recorder)


class _FakeResult:
    inserted_primary_key = [123]


class _FakeSession:
    def execute(self, _statement):
        return _FakeResult()


@contextmanager
def _fake_session_scope():
    yield _FakeSession()


def test_save_pending_claim_keeps_fact_id_null():
    recorded: list[dict] = []
    claim = Claim(material_id=7, content="候选陈述", quote="无法绑定的短摘")

    with patch("app.evidence.extractor.ORMClaim", _FakeClaimTable(recorded)), patch(
        "app.evidence.extractor.session_scope", _fake_session_scope
    ):
        claim_id = save_claim(claim, status="pending", origin_call_id="call-1", task_id="task-1")

    assert claim_id == 123
    assert recorded[0]["fact_id"] is None
    assert recorded[0]["status"] == "pending"


def test_save_promoted_claim_persists_real_fact_id():
    recorded: list[dict] = []
    claim = Claim(material_id=7, content="确认事实", quote="可绑定短摘", fact_id=42)

    with patch("app.evidence.extractor.ORMClaim", _FakeClaimTable(recorded)), patch(
        "app.evidence.extractor.session_scope", _fake_session_scope
    ):
        claim_id = save_claim(claim, status="promoted", origin_call_id="call-2", task_id="task-1")

    assert claim_id == 123
    assert recorded[0]["fact_id"] == 42
    assert recorded[0]["status"] == "promoted"
