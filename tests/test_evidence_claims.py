from contextlib import contextmanager
from unittest.mock import patch

from app.evidence.extractor import _positive_unit_id, _unit_ids_in_text, save_claim, save_fact_with_evidence
from app.models import Fact
from app.models.material import Claim, Evidence


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


class _FakeInsertTable:
    def __init__(self, recorder: list[dict]):
        self.recorder = recorder
        self.c = type("Columns", (), {"id": object()})()

    def insert(self):
        return _FakeInsert(self.recorder)


class _FakeResult:
    inserted_primary_key = [123]


class _FakeUpdate:
    def where(self, *_args, **_kwargs):
        return self

    def values(self, **_kwargs):
        return self


class _FakeSession:
    def execute(self, _statement):
        return _FakeResult()


@contextmanager
def _fake_session_scope():
    yield _FakeSession()


def test_save_pending_claim_keeps_fact_id_null():
    recorded: list[dict] = []
    claim = Claim(material_id=7, content="候选陈述", quote="无法绑定的短摘",
                  assertion_mode="prescriptive", assertion_mode_raw="设计要求")

    with patch("app.evidence.extractor.ORMClaim", _FakeClaimTable(recorded)), patch(
        "app.evidence.extractor.session_scope", _fake_session_scope
    ):
        claim_id = save_claim(claim, status="pending", origin_call_id="call-1", task_id="task-1")

    assert claim_id == 123
    assert recorded[0]["fact_id"] is None
    assert recorded[0]["status"] == "pending"
    assert recorded[0]["assertion_mode"] == "prescriptive"
    assert recorded[0]["assertion_mode_raw"] == "设计要求"


def test_save_fact_persists_protocol_and_open_source_semantics():
    fact_rows: list[dict] = []
    evidence_rows: list[dict] = []
    fact = Fact(
        content="创建持久化队列。",
        assertion_mode="prescriptive",
        assertion_mode_raw="design requirement",
    )
    evidence = Evidence(
        fact_id=None, material_id=7, unit_id=42,
        source_file="queue-design.md", quote="Create the durable queue.",
    )

    with patch("app.evidence.extractor.ORMFact", _FakeInsertTable(fact_rows)), patch(
        "app.evidence.extractor.ORMEvidence", _FakeInsertTable(evidence_rows)
    ), patch("app.evidence.extractor.session_scope", _fake_session_scope), patch(
        "app.evidence.extractor.stable_hash", lambda _value: "stable"
    ), patch("app.evidence.extractor.current_context", lambda: {"run_id": "run-1"}), patch(
        "app.evidence.extractor.update", lambda *_args, **_kwargs: _FakeUpdate()
    ):
        fact_id = save_fact_with_evidence(fact, [evidence], task_id="task-1")

    assert fact_id == 123
    assert fact_rows[0]["assertion_mode"] == "prescriptive"
    assert fact_rows[0]["assertion_mode_raw"] == "design requirement"
    assert evidence_rows[0]["fact_id"] == fact_id


def test_missing_or_invalid_unit_id_is_normalized_before_comparison():
    assert _positive_unit_id(None) is None
    assert _positive_unit_id("") is None
    assert _positive_unit_id(0) is None
    assert _positive_unit_id("42") == 42


def test_unit_audit_parses_only_material_unit_markers():
    text = "[U12 | sample.pdf]\n本文\n[U17 | sample.pdf | 第2页]\n更多本文"
    assert _unit_ids_in_text(text) == [12, 17]


def test_save_pending_claim_persists_null_fact_reference():
    recorded: list[dict] = []
    claim = Claim(material_id=7, content="候选陈述", quote="无法绑定的短摘")

    with patch("app.evidence.extractor.ORMClaim", _FakeClaimTable(recorded)), patch(
        "app.evidence.extractor.session_scope", _fake_session_scope
    ):
        save_claim(claim, status="pending", origin_call_id="call-1", task_id="task-1")

    assert recorded[0]["fact_id"] is None


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


def test_save_promoted_claim_requires_real_fact_id():
    claim = Claim(material_id=7, content="错误晋升", quote="没有事实引用")

    try:
        save_claim(claim, status="promoted", origin_call_id="call-3", task_id="task-1")
    except ValueError as exc:
        assert "persisted fact" in str(exc)
    else:
        raise AssertionError("promoted claim without fact_id must be rejected")
