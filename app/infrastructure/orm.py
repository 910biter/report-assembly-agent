"""ORM 数据模型(SQLAlchemy 2.0)——38 张表集中定义。

- 表结构唯一真源:_SCHEMA(CREATE TABLE)解析生成 Table 对象
- PostgreSQL 方言由 SQLAlchemy 自动映射(SQLite 已完全退出)
- Alembic 迁移基础(后续自动化)
"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

# 表结构唯一真源(CREATE TABLE 语句;业务 schema 的历史基线,
# SQLAlchemy create_all 在 PG 下按类型映射生成兼容 DDL)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    path TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE DEFAULT '',
    file_hash TEXT NOT NULL DEFAULT '',
    parser_version TEXT NOT NULL DEFAULT '',
    parsed_at TEXT,
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    duplicate_of INTEGER
);

CREATE TABLE IF NOT EXISTS file_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES file_nodes(id),
    node_type TEXT NOT NULL DEFAULT 'file',
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    relative_path TEXT NOT NULL DEFAULT '',
    file_type TEXT NOT NULL DEFAULT '',
    file_size INTEGER NOT NULL DEFAULT 0,
    file_hash TEXT NOT NULL DEFAULT '',
    material_id INTEGER REFERENCES materials(id),
    status TEXT NOT NULL DEFAULT 'indexed',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE TABLE IF NOT EXISTS file_parse_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES file_nodes(id),
    material_id INTEGER REFERENCES materials(id),
    parser TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    page_count INTEGER NOT NULL DEFAULT 0,
    text_count INTEGER NOT NULL DEFAULT 0,
    table_count INTEGER NOT NULL DEFAULT 0,
    image_count INTEGER NOT NULL DEFAULT 0,
    markdown_chars INTEGER NOT NULL DEFAULT 0,
    structure_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    parsed_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    UNIQUE(node_id, parser)
);

CREATE TABLE IF NOT EXISTS node_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES file_nodes(id),
    summary_type TEXT NOT NULL DEFAULT 'folder',
    status TEXT NOT NULL DEFAULT 'ready',
    summary TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    generated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    UNIQUE(node_id, summary_type)
);

CREATE TABLE IF NOT EXISTS export_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES file_nodes(id),
    package_path TEXT NOT NULL,
    file_count INTEGER NOT NULL DEFAULT 0,
    total_size INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL REFERENCES materials(id),
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    page INTEGER,
    paragraph INTEGER,
    image_desc TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS fact_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    cluster_key TEXT NOT NULL,
    fact_ids TEXT NOT NULL DEFAULT '[]',
    sources TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'confirmed',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);
CREATE TABLE IF NOT EXISTS fact_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL,
    evidence_quote TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    dimension TEXT NOT NULL DEFAULT '',
    need_id INTEGER NOT NULL DEFAULT 0,
    source_level TEXT NOT NULL DEFAULT 'MATERIAL_FACT',
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    conflict_ids TEXT NOT NULL DEFAULT '[]',
    task_id TEXT NOT NULL DEFAULT '',
    origin_call_id TEXT NOT NULL DEFAULT '',
    disposition TEXT NOT NULL DEFAULT 'UNASSIGNED',
    stable_key TEXT NOT NULL DEFAULT '',
    lifecycle_status TEXT NOT NULL DEFAULT 'active',
    introduced_run_id TEXT NOT NULL DEFAULT '',
    superseded_by_fact_id INTEGER
);

CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id INTEGER NOT NULL REFERENCES facts(id),
    material_id INTEGER NOT NULL,
    unit_id INTEGER NOT NULL,
    source_file TEXT NOT NULL,
    page INTEGER,
    paragraph INTEGER,
    quote TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE TABLE IF NOT EXISTS conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_key TEXT NOT NULL,
    entries TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'unresolved',
    task_id TEXT NOT NULL DEFAULT '',
    origin_call_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS inferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    source_level TEXT NOT NULL,
    based_fact_ids TEXT NOT NULL DEFAULT '[]',
    reasoning_chain TEXT NOT NULL DEFAULT '',
    dimension TEXT NOT NULL DEFAULT '',
    confidence_level TEXT NOT NULL DEFAULT 'medium',
    confidence_reason TEXT NOT NULL DEFAULT '',
    uncertainty TEXT NOT NULL DEFAULT '',
    origin_call_id TEXT NOT NULL DEFAULT '',
    stable_key TEXT NOT NULL DEFAULT '',
    lifecycle_status TEXT NOT NULL DEFAULT 'active',
    introduced_run_id TEXT NOT NULL DEFAULT '',
    superseded_by_inference_id INTEGER
);

CREATE TABLE IF NOT EXISTS style_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    institution TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE TABLE IF NOT EXISTS style_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id INTEGER NOT NULL REFERENCES style_library(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    structure_json TEXT NOT NULL DEFAULT '{}',
    writing_style_json TEXT NOT NULL DEFAULT '{}',
    terminology_json TEXT NOT NULL DEFAULT '{}',
    format_spec_json TEXT NOT NULL DEFAULT '{}',
    writing_patterns_json TEXT NOT NULL DEFAULT '{}',
    style_samples_json TEXT NOT NULL DEFAULT '[]',
    source_reports TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    source_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id INTEGER REFERENCES facts(id),
    material_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    quote TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    fact_type TEXT NOT NULL DEFAULT 'STATEMENT',
    dimension TEXT NOT NULL DEFAULT '',
    need_id INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    task_id TEXT NOT NULL DEFAULT '',
    origin_call_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS user_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_type TEXT NOT NULL DEFAULT 'edit',
    summary TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE TABLE IF NOT EXISTS artifact_cache (
    cache_key TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    model_version TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    config_version TEXT NOT NULL DEFAULT '',
    input_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    last_hit_at TEXT
);

CREATE TABLE IF NOT EXISTS task_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL,
    input_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'done',
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    UNIQUE(task_id, run_id, stage, input_hash)
);

CREATE TABLE IF NOT EXISTS task_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    run_mode TEXT NOT NULL DEFAULT 'initial',
    status TEXT NOT NULL DEFAULT 'created',
    base_version_id INTEGER REFERENCES report_versions(id),
    candidate_version_id INTEGER REFERENCES report_versions(id),
    update_reason TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    finished_at TEXT,
    UNIQUE(task_id, revision)
);

CREATE TABLE IF NOT EXISTS llm_call_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT '',
    report_mode TEXT NOT NULL DEFAULT '',
    input_chars INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    context_tokens INTEGER NOT NULL DEFAULT 0,
    returned_chars INTEGER NOT NULL DEFAULT 0,
    valid_json_chars INTEGER NOT NULL DEFAULT 0,
    stored_chars INTEGER NOT NULL DEFAULT 0,
    final_chars INTEGER NOT NULL DEFAULT 0,
    returned_tokens INTEGER NOT NULL DEFAULT 0,
    parsed_tokens INTEGER NOT NULL DEFAULT 0,
    persisted_tokens INTEGER NOT NULL DEFAULT 0,
    final_tokens INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    candidate_total INTEGER NOT NULL DEFAULT 0,
    funnel_json TEXT NOT NULL DEFAULT '{}',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 1,
    error TEXT NOT NULL DEFAULT '',
    material_count INTEGER NOT NULL DEFAULT 0,
    produced_fact_ids TEXT NOT NULL DEFAULT '[]',
    produced_inference_ids TEXT NOT NULL DEFAULT '[]',
    produced_chapter_ids TEXT NOT NULL DEFAULT '[]',
    final_used_fact_ids TEXT NOT NULL DEFAULT '[]',
    final_used_inference_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE TABLE IF NOT EXISTS material_scan (
    task_id TEXT NOT NULL,
    material_id INTEGER NOT NULL,
    dimension TEXT NOT NULL,
    scanned INTEGER NOT NULL DEFAULT 1,
    units_selected INTEGER NOT NULL DEFAULT 0,
    fact_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (task_id, material_id, dimension)
);

CREATE TABLE IF NOT EXISTS material_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL,
    doc_type TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    key_sections TEXT NOT NULL DEFAULT '[]',
    entities TEXT NOT NULL DEFAULT '[]',
    times TEXT NOT NULL DEFAULT '[]',
    value_rank INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    key_points TEXT NOT NULL DEFAULT '[]',
    material_role TEXT NOT NULL DEFAULT '',
    claim_support TEXT NOT NULL DEFAULT 'unknown',
    task_id TEXT NOT NULL DEFAULT '',
    allowed_usage TEXT NOT NULL DEFAULT '[]',
    forbidden_usage TEXT NOT NULL DEFAULT '[]',
    missing_information TEXT NOT NULL DEFAULT '[]',
    analysis_version TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS report_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    structure TEXT NOT NULL DEFAULT '[]',
    dimensions TEXT NOT NULL DEFAULT '[]',
    objective TEXT NOT NULL DEFAULT '',
    audience TEXT NOT NULL DEFAULT '',
    report_type TEXT NOT NULL DEFAULT '',
    core_question TEXT NOT NULL DEFAULT '',
    core_judgment TEXT NOT NULL DEFAULT '',
    narrative_logic TEXT NOT NULL DEFAULT '',
    chapter_plans TEXT NOT NULL DEFAULT '[]',
    budget TEXT NOT NULL DEFAULT '{}',
    required_facts TEXT NOT NULL DEFAULT '[]',
    evidence_needs TEXT NOT NULL DEFAULT '[]',
    user_requirements TEXT NOT NULL DEFAULT '',
    plan_stage TEXT NOT NULL DEFAULT 'analysis',
    plan_version INTEGER NOT NULL DEFAULT 1,
    analysis_plan_json TEXT NOT NULL DEFAULT '{}',
    final_plan_json TEXT NOT NULL DEFAULT '{}',
    finalized_at TEXT,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES report_plans(id),
    title TEXT NOT NULL,
    style_profile_id INTEGER,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE TABLE IF NOT EXISTS report_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    version_no INTEGER NOT NULL,
    version_major INTEGER NOT NULL DEFAULT 1,
    version_minor INTEGER NOT NULL DEFAULT 0,
    based_on_version_id INTEGER,
    task_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'snapshot',
    title TEXT NOT NULL DEFAULT '',
    user_requirements TEXT NOT NULL DEFAULT '',
    template_id INTEGER,
    material_fingerprints TEXT NOT NULL DEFAULT '[]',
    report_plan_snapshot TEXT NOT NULL DEFAULT '{}',
    narrative_plan_snapshot TEXT NOT NULL DEFAULT '{}',
    scale_plan_snapshot TEXT NOT NULL DEFAULT '{}',
    fact_snapshot TEXT NOT NULL DEFAULT '[]',
    inference_snapshot TEXT NOT NULL DEFAULT '[]',
    conflict_snapshot TEXT NOT NULL DEFAULT '[]',
    sentence_snapshot TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    change_summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    UNIQUE(report_id, version_no)
);

CREATE TABLE IF NOT EXISTS report_version_deltas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    run_id TEXT NOT NULL DEFAULT '',
    from_version_id INTEGER REFERENCES report_versions(id),
    to_version_id INTEGER REFERENCES report_versions(id),
    status TEXT NOT NULL DEFAULT 'planned',
    update_reason TEXT NOT NULL DEFAULT '',
    added_materials TEXT NOT NULL DEFAULT '[]',
    duplicate_materials TEXT NOT NULL DEFAULT '[]',
    added_facts TEXT NOT NULL DEFAULT '[]',
    modified_facts TEXT NOT NULL DEFAULT '[]',
    deprecated_facts TEXT NOT NULL DEFAULT '[]',
    added_inferences TEXT NOT NULL DEFAULT '[]',
    modified_inferences TEXT NOT NULL DEFAULT '[]',
    deprecated_inferences TEXT NOT NULL DEFAULT '[]',
    new_conflicts TEXT NOT NULL DEFAULT '[]',
    resolved_conflicts TEXT NOT NULL DEFAULT '[]',
    affected_chapters TEXT NOT NULL DEFAULT '[]',
    structure_changes TEXT NOT NULL DEFAULT '[]',
    evidence_changes TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE TABLE IF NOT EXISTS report_change_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    base_version_id INTEGER NOT NULL REFERENCES report_versions(id),
    candidate_hash TEXT NOT NULL,
    change_key TEXT NOT NULL,
    scope_json TEXT NOT NULL DEFAULT '{}',
    decision TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    applied_at TEXT,
    UNIQUE(base_version_id, candidate_hash, change_key)
);

CREATE TABLE IF NOT EXISTS report_sentences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    lineage_id TEXT NOT NULL DEFAULT '',
    parent_sentence_id INTEGER,
    section TEXT NOT NULL,
    paragraph INTEGER NOT NULL DEFAULT 1,
    position INTEGER NOT NULL,
    content TEXT NOT NULL,
    source_level TEXT NOT NULL,
    source_refs TEXT NOT NULL DEFAULT '{}',
    selected INTEGER NOT NULL DEFAULT 1,
    user_edit TEXT,
    edit_history TEXT NOT NULL DEFAULT '[]',
    origin_call_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS short_memory (
    task_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS long_memory (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT '',
    aliases TEXT NOT NULL DEFAULT '[]',
    task_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    time TEXT NOT NULL DEFAULT '',
    entity_ids TEXT NOT NULL DEFAULT '[]',
    fact_ids TEXT NOT NULL DEFAULT '[]',
    task_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entity INTEGER NOT NULL,
    target_entity INTEGER NOT NULL,
    relation_type TEXT NOT NULL,
    fact_ids TEXT NOT NULL DEFAULT '[]',
    task_id TEXT NOT NULL DEFAULT ''
);

-- Canonical graph records. Neo4j is a rebuildable projection of these rows,
-- never a second source of truth for facts or provenance.
CREATE TABLE IF NOT EXISTS kg_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_key TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    canonical_name TEXT NOT NULL,
    entity_type TEXT NOT NULL DEFAULT 'other',
    aliases_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE TABLE IF NOT EXISTS kg_entity_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES kg_entities(id),
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    source_task_id TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'confirmed',
    UNIQUE(entity_id, normalized_alias)
);

CREATE TABLE IF NOT EXISTS kg_assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assertion_key TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    task_id TEXT NOT NULL DEFAULT '',
    subject_entity_id INTEGER NOT NULL REFERENCES kg_entities(id),
    predicate TEXT NOT NULL,
    object_entity_id INTEGER REFERENCES kg_entities(id),
    object_value TEXT NOT NULL DEFAULT '',
    object_kind TEXT NOT NULL DEFAULT 'entity',
    event_name TEXT NOT NULL DEFAULT '',
    valid_from TEXT NOT NULL DEFAULT '',
    valid_to TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'candidate',
    extraction_method TEXT NOT NULL DEFAULT 'llm',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE TABLE IF NOT EXISTS kg_assertion_facts (
    assertion_id INTEGER NOT NULL REFERENCES kg_assertions(id),
    fact_id INTEGER NOT NULL REFERENCES facts(id),
    PRIMARY KEY (assertion_id, fact_id)
);

CREATE TABLE IF NOT EXISTS kg_task_membership (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    entity_id INTEGER REFERENCES kg_entities(id),
    assertion_id INTEGER REFERENCES kg_assertions(id),
    role TEXT NOT NULL DEFAULT 'observed',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    UNIQUE(task_id, entity_id, assertion_id, role)
);

CREATE TABLE IF NOT EXISTS kg_changesets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    changeset_key TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    task_id TEXT NOT NULL,
    report_version_id INTEGER,
    change_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending_review',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
);

CREATE TABLE IF NOT EXISTS graph_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    projected_at TEXT
);

CREATE TABLE IF NOT EXISTS inference_fact (
    inference_id INTEGER NOT NULL REFERENCES inferences(id),
    fact_id INTEGER NOT NULL REFERENCES facts(id),
    PRIMARY KEY (inference_id, fact_id)
);

CREATE TABLE IF NOT EXISTS report_sentence_fact (
    sentence_id INTEGER NOT NULL REFERENCES report_sentences(id),
    fact_id INTEGER NOT NULL REFERENCES facts(id),
    PRIMARY KEY (sentence_id, fact_id)
);

CREATE TABLE IF NOT EXISTS report_sentence_inference (
    sentence_id INTEGER NOT NULL REFERENCES report_sentences(id),
    inference_id INTEGER NOT NULL REFERENCES inferences(id),
    PRIMARY KEY (sentence_id, inference_id)
);

CREATE INDEX IF NOT EXISTS idx_units_material ON units(material_id);
CREATE INDEX IF NOT EXISTS idx_evidence_fact ON evidence(fact_id);
CREATE INDEX IF NOT EXISTS idx_evidence_material ON evidence(material_id);
CREATE INDEX IF NOT EXISTS idx_evidence_unit ON evidence(unit_id);
CREATE INDEX IF NOT EXISTS idx_claims_material ON claims(material_id);
CREATE INDEX IF NOT EXISTS idx_insights_material ON material_insights(material_id);
CREATE INDEX IF NOT EXISTS idx_sentences_report ON report_sentences(report_id);
CREATE INDEX IF NOT EXISTS idx_report_versions_report ON report_versions(report_id);
CREATE INDEX IF NOT EXISTS idx_report_deltas_report ON report_version_deltas(report_id);
"""
# 迁移/演进补充列(历史 ALTER 汇总;新库 create_all 直接含,旧库靠 _migrate 补齐)
_MIGRATED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ('claims', 'need_id', 'INTEGER NOT NULL DEFAULT 0'),
    ('facts', 'need_id', 'INTEGER NOT NULL DEFAULT 0'),
    ('style_variants', 'source_hash', "TEXT NOT NULL DEFAULT ''"),
    ('report_sentences', 'paragraph', 'INTEGER NOT NULL DEFAULT 1'),
    ('report_sentences', 'edit_history', "TEXT NOT NULL DEFAULT '[]'"),
    ('facts', 'fact_type', "TEXT NOT NULL DEFAULT 'STATEMENT'"),
    ('facts', 'task_id', "TEXT NOT NULL DEFAULT ''"),
    ('facts', 'origin_call_id', "TEXT NOT NULL DEFAULT ''"),
    ('facts', 'disposition', "TEXT NOT NULL DEFAULT 'UNASSIGNED'"),
    ('facts', 'stable_key', "TEXT NOT NULL DEFAULT ''"),
    ('facts', 'lifecycle_status', "TEXT NOT NULL DEFAULT 'active'"),
    ('facts', 'introduced_run_id', "TEXT NOT NULL DEFAULT ''"),
    ('facts', 'superseded_by_fact_id', 'INTEGER'),
    ('inferences', 'stable_key', "TEXT NOT NULL DEFAULT ''"),
    ('inferences', 'lifecycle_status', "TEXT NOT NULL DEFAULT 'active'"),
    ('inferences', 'introduced_run_id', "TEXT NOT NULL DEFAULT ''"),
    ('inferences', 'superseded_by_inference_id', 'INTEGER'),
    ('claims', 'origin_call_id', "TEXT NOT NULL DEFAULT ''"),
    ('claims', 'task_id', "TEXT NOT NULL DEFAULT ''"),
    ('conflicts', 'origin_call_id', "TEXT NOT NULL DEFAULT ''"),
    ('conflicts', 'task_id', "TEXT NOT NULL DEFAULT ''"),
    ('inferences', 'origin_call_id', "TEXT NOT NULL DEFAULT ''"),
    ('report_sentences', 'origin_call_id', "TEXT NOT NULL DEFAULT ''"),
    ('report_sentences', 'lineage_id', "TEXT NOT NULL DEFAULT ''"),
    ('report_sentences', 'parent_sentence_id', 'INTEGER'),
    ('materials', 'file_hash', "TEXT NOT NULL DEFAULT ''"),
    ('materials', 'parser_version', "TEXT NOT NULL DEFAULT ''"),
    ('materials', 'parsed_at', 'TEXT'),
    ('file_nodes', 'parent_id', 'INTEGER REFERENCES file_nodes(id)'),
    ('file_nodes', 'material_id', 'INTEGER REFERENCES materials(id)'),
    ('file_nodes', 'status', "TEXT NOT NULL DEFAULT 'indexed'"),
    ('file_parse_profiles', 'markdown_chars', 'INTEGER NOT NULL DEFAULT 0'),
    ('units', 'metadata_json', "TEXT NOT NULL DEFAULT '{}'"),
    ('material_insights', 'key_points', "TEXT NOT NULL DEFAULT '[]'"),
    ('material_insights', 'material_role', "TEXT NOT NULL DEFAULT ''"),
    ('material_insights', 'claim_support', "TEXT NOT NULL DEFAULT 'unknown'"),
    ('material_insights', 'task_id', "TEXT NOT NULL DEFAULT ''"),
    ('material_insights', 'allowed_usage', "TEXT NOT NULL DEFAULT '[]'"),
    ('material_insights', 'forbidden_usage', "TEXT NOT NULL DEFAULT '[]'"),
    ('material_insights', 'missing_information', "TEXT NOT NULL DEFAULT '[]'"),
    ('material_insights', 'analysis_version', "TEXT NOT NULL DEFAULT ''"),
    ('conflicts', 'claim_ids', "TEXT NOT NULL DEFAULT '[]'"),
    ('evidence', 'created_at', "TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)"),
    ('inferences', 'analysis_type', "TEXT NOT NULL DEFAULT ''"),
    ('inferences', 'confidence_level', "TEXT NOT NULL DEFAULT 'medium'"),
    ('inferences', 'confidence_reason', "TEXT NOT NULL DEFAULT ''"),
    ('inferences', 'uncertainty', "TEXT NOT NULL DEFAULT ''"),
    ('style_variants', 'chapter_styles_json', "TEXT NOT NULL DEFAULT '[]'"),
    ('style_variants', 'reasoning_profile_json', "TEXT NOT NULL DEFAULT '{}'"),
    ('style_variants', 'institution_rules_json', "TEXT NOT NULL DEFAULT '{}'"),
    ('long_memory', 'scope', "TEXT NOT NULL DEFAULT 'institution'"),
    ('report_plans', 'objective', "TEXT NOT NULL DEFAULT ''"),
    ('report_plans', 'audience', "TEXT NOT NULL DEFAULT ''"),
    ('report_plans', 'report_type', "TEXT NOT NULL DEFAULT ''"),
    ('report_plans', 'core_question', "TEXT NOT NULL DEFAULT ''"),
    ('report_plans', 'core_judgment', "TEXT NOT NULL DEFAULT ''"),
    ('report_plans', 'narrative_logic', "TEXT NOT NULL DEFAULT ''"),
    ('report_plans', 'chapter_plans', "TEXT NOT NULL DEFAULT '[]'"),
    ('report_plans', 'budget', "TEXT NOT NULL DEFAULT '{}'"),
    ('report_plans', 'required_facts', "TEXT NOT NULL DEFAULT '[]'"),
    ('report_plans', 'evidence_needs', "TEXT NOT NULL DEFAULT '[]'"),
    ('report_plans', 'plan_stage', "TEXT NOT NULL DEFAULT 'analysis'"),
    ('report_plans', 'plan_version', 'INTEGER NOT NULL DEFAULT 1'),
    ('report_plans', 'analysis_plan_json', "TEXT NOT NULL DEFAULT '{}'"),
    ('report_plans', 'final_plan_json', "TEXT NOT NULL DEFAULT '{}'"),
    ('report_plans', 'finalized_at', 'TEXT'),
    ('llm_call_logs', 'returned_chars', 'INTEGER NOT NULL DEFAULT 0'),
    ('llm_call_logs', 'valid_json_chars', 'INTEGER NOT NULL DEFAULT 0'),
    ('llm_call_logs', 'stored_chars', 'INTEGER NOT NULL DEFAULT 0'),
    ('llm_call_logs', 'final_chars', 'INTEGER NOT NULL DEFAULT 0'),
    ('llm_call_logs', 'returned_tokens', 'INTEGER NOT NULL DEFAULT 0'),
    ('llm_call_logs', 'parsed_tokens', 'INTEGER NOT NULL DEFAULT 0'),
    ('llm_call_logs', 'persisted_tokens', 'INTEGER NOT NULL DEFAULT 0'),
    ('llm_call_logs', 'final_tokens', 'INTEGER NOT NULL DEFAULT 0'),
    ('llm_call_logs', 'candidate_count', 'INTEGER NOT NULL DEFAULT 0'),
    ('llm_call_logs', 'candidate_total', 'INTEGER NOT NULL DEFAULT 0'),
    ('llm_call_logs', 'funnel_json', "TEXT NOT NULL DEFAULT '{}'"),
    ('llm_call_logs', 'run_id', "TEXT NOT NULL DEFAULT ''"),
    ('task_artifacts', 'run_id', "TEXT NOT NULL DEFAULT ''"),
    ('report_versions', 'version_major', 'INTEGER NOT NULL DEFAULT 1'),
    ('report_versions', 'version_minor', 'INTEGER NOT NULL DEFAULT 0'),
    ('report_versions', 'run_id', "TEXT NOT NULL DEFAULT ''"),
    ('report_version_deltas', 'run_id', "TEXT NOT NULL DEFAULT ''"),
    ('entities', 'task_id', "TEXT NOT NULL DEFAULT ''"),
    ('events', 'task_id', "TEXT NOT NULL DEFAULT ''"),
    ('relations', 'task_id', "TEXT NOT NULL DEFAULT ''"),
)

class Base(DeclarativeBase):
    pass

def _build_tables_from_schema() -> None:
    """从 app.db._SCHEMA 解析 CREATE TABLE 生成 Table 对象(单一真源,100% 对齐)。

    手写声明式类与 _SCHEMA 易漂移;自动生成保证业务 SQL(裸 connect)与
    ORM session 访问的是同一结构。
    """
    import re
    from sqlalchemy import Column, Float, ForeignKey, Integer, String, Table, UniqueConstraint


    _type_map = {"INTEGER": Integer, "TEXT": String, "REAL": Float}
    created: set[str] = set()
    for stmt in re.finditer(
        r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\);",
        _SCHEMA, re.S,
    ):
        name = stmt.group(1)
        body = stmt.group(2)
        if name in Base.metadata.tables:
            continue
        from sqlalchemy import text as _text
        DEFAULT_RE = re.compile(r"DEFAULT\s+(.+?)(?=\s+NOT NULL|\s+UNIQUE|\s+PRIMARY KEY|,|$)", re.I)

        def _server_default(suffix: str):
            """提取 DEFAULT 子句为 SQLAlchemy server_default(跨方言)。"""
            dm = DEFAULT_RE.search(suffix or "")
            if not dm:
                return None
            raw = dm.group(1).strip()
            if raw.startswith("(") and raw.endswith(")"):
                raw = raw[1:-1].strip()
            upper = raw.upper()
            if upper in ("CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME"):
                return _text(upper)
            if raw.startswith("'") and raw.endswith("'"):
                return raw[1:-1]  # 去引号:SQLAlchemy server_default 自动正确引号
            return raw  # 数字/NULL

        cols = []
        for col_m in re.finditer(r"\n\s*([\w_]+)\s+(INTEGER|TEXT|REAL|BLOB)([^,\n]*)", body):
            col_name = col_m.group(1)
            col_type = _type_map[col_m.group(2)]
            col_suffix = col_m.group(3) or ""
            is_pk = "PRIMARY KEY" in col_suffix.upper()
            is_uniq = "UNIQUE" in col_suffix.upper()
            nullable = "NOT NULL" not in col_suffix.upper()
            default = _server_default(col_suffix)
            fk_m = re.search(r"REFERENCES\s+(\w+)\s*\((\w+)\)", col_suffix, re.I)
            args = [ForeignKey(f"{fk_m.group(1)}.{fk_m.group(2)}")] if fk_m else []
            if col_name == "id" or is_pk:
                cols.append(Column(col_name, col_type, *args, primary_key=True, nullable=False))
            elif is_uniq:
                cols.append(Column(col_name, col_type, *args, unique=True, nullable=nullable, server_default=default))
            else:
                cols.append(Column(col_name, col_type, *args, nullable=nullable, server_default=default))
        if cols:
            # 表级复合主键(关联表 PRIMARY KEY (a, b))
            from sqlalchemy import PrimaryKeyConstraint
            pk_m = re.search(r"PRIMARY KEY\s*\(([^)]+)\)", body, re.S)
            table_constraints = []
            for unique_m in re.finditer(r"UNIQUE\s*\(([^)]+)\)", body, re.I | re.S):
                unique_cols = [c.strip() for c in unique_m.group(1).split(",")]
                if all(c in {col.name for col in cols} for c in unique_cols):
                    table_constraints.append(UniqueConstraint(*unique_cols))
            if pk_m:
                pk_cols = [c.strip() for c in pk_m.group(1).split(",")]
                if all(c in {col.name for col in cols} for c in pk_cols):
                    for col in cols:
                        if col.name in pk_cols:
                            col.primary_key = True
                    Table(name, Base.metadata, *cols, PrimaryKeyConstraint(*pk_cols), *table_constraints)
            else:
                Table(name, Base.metadata, *cols, *table_constraints)
            created.add(name)
        # 合并迁移补充列(内置清单;新库 create_all 直接含,结构 100% 对齐)
        for _table, _col, _ddl in _MIGRATED_COLUMNS:
            if _table in Base.metadata.tables and _col not in Base.metadata.tables[_table].c:
                _ctype = _type_map.get(_ddl.split()[0], String)
                Base.metadata.tables[_table].append_column(Column(_col, _ctype))


_build_tables_from_schema()



# 关联表由 _SCHEMA 自动生成(见 _build_tables_from_schema)

# ORM 表对象别名(业务访问用;与 _SCHEMA 100% 对齐)

ORMClaim = Base.metadata.tables["claims"]
ORMFact = Base.metadata.tables["facts"]
ORMEvidence = Base.metadata.tables["evidence"]
ORMSentence = Base.metadata.tables["report_sentences"]
ORMMaterial = Base.metadata.tables["materials"]
ORMUnit = Base.metadata.tables["units"]
ORMInference = Base.metadata.tables["inferences"]
ORMConflict = Base.metadata.tables["conflicts"]
ORMPlan = Base.metadata.tables["report_plans"]
ORMReport = Base.metadata.tables["reports"]
ORMReportVersion = Base.metadata.tables["report_versions"]
ORMReportVersionDelta = Base.metadata.tables["report_version_deltas"]
ORMReportChangeDecision = Base.metadata.tables["report_change_decisions"]
ORMVariant = Base.metadata.tables["style_variants"]
ORMCluster = Base.metadata.tables["fact_clusters"]
ORMRelation = Base.metadata.tables["fact_relations"]
ORMTaskArtifact = Base.metadata.tables["task_artifacts"]
ORMTaskRun = Base.metadata.tables["task_runs"]
ORMShortMemory = Base.metadata.tables["short_memory"]
ORMLLMCall = Base.metadata.tables["llm_call_logs"]
ORMInsight = Base.metadata.tables["material_insights"]
ORMMaterialScan = Base.metadata.tables["material_scan"]
ORMKGEntity = Base.metadata.tables["kg_entities"]
ORMKGEntityAlias = Base.metadata.tables["kg_entity_aliases"]
ORMKGAssertion = Base.metadata.tables["kg_assertions"]
ORMKGAssertionFact = Base.metadata.tables["kg_assertion_facts"]
ORMKGTaskMembership = Base.metadata.tables["kg_task_membership"]
ORMKGChangeSet = Base.metadata.tables["kg_changesets"]
ORMGraphOutbox = Base.metadata.tables["graph_outbox"]
# 关联表(复合主键;writer 血缘等访问)
ORMSentenceFact = Base.metadata.tables["report_sentence_fact"]
ORMSentenceInference = Base.metadata.tables["report_sentence_inference"]
ORMInferenceFact = Base.metadata.tables["inference_fact"]
