"""SQLite 连接与建表。

数据模型见 app/models/,表结构与本模块一一对应。
"""
import sqlite3

from app.config import settings

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

CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL REFERENCES materials(id),
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    page INTEGER,
    paragraph INTEGER,
    image_desc TEXT
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    dimension TEXT NOT NULL DEFAULT '',
    source_level TEXT NOT NULL DEFAULT 'MATERIAL_FACT',
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    conflict_ids TEXT NOT NULL DEFAULT '[]',
    task_id TEXT NOT NULL DEFAULT '',
    origin_call_id TEXT NOT NULL DEFAULT ''
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
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_key TEXT NOT NULL,
    entries TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'unresolved',
    origin_call_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS inferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    source_level TEXT NOT NULL,
    based_fact_ids TEXT NOT NULL DEFAULT '[]',
    reasoning_chain TEXT NOT NULL DEFAULT '',
    dimension TEXT NOT NULL DEFAULT '',
    origin_call_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS style_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    institution TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    origin_call_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS user_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_type TEXT NOT NULL DEFAULT 'edit',
    summary TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS artifact_cache (
    cache_key TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    model_version TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    config_version TEXT NOT NULL DEFAULT '',
    input_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_hit_at TEXT
);

CREATE TABLE IF NOT EXISTS task_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    input_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'done',
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(task_id, stage, input_hash)
);

CREATE TABLE IF NOT EXISTS llm_call_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL DEFAULT '',
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
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    key_points TEXT NOT NULL DEFAULT '[]',
    material_role TEXT NOT NULL DEFAULT '',
    claim_support TEXT NOT NULL DEFAULT 'unknown',
    allowed_usage TEXT NOT NULL DEFAULT '[]',
    forbidden_usage TEXT NOT NULL DEFAULT '[]',
    missing_information TEXT NOT NULL DEFAULT '[]'
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
    user_requirements TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES report_plans(id),
    title TEXT NOT NULL,
    style_profile_id INTEGER,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS report_sentences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id),
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
    aliases TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    time TEXT NOT NULL DEFAULT '',
    entity_ids TEXT NOT NULL DEFAULT '[]',
    fact_ids TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entity INTEGER NOT NULL,
    target_entity INTEGER NOT NULL,
    relation_type TEXT NOT NULL,
    fact_ids TEXT NOT NULL DEFAULT '[]'
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

CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(content, tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS units_fts_ai AFTER INSERT ON units BEGIN
    INSERT INTO units_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS units_fts_ad AFTER DELETE ON units BEGIN
    INSERT INTO units_fts(units_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS units_fts_au AFTER UPDATE OF content ON units BEGIN
    INSERT INTO units_fts(units_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO units_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    settings.ensure_dirs()
    with connect() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        # 旧 SQLite 向量方案清理(向量统一走 Qdrant,不再双写)
        for _table in ("material_vectors", "unit_vectors", "fact_vectors"):
            try:
                conn.execute(f"DROP TABLE IF EXISTS {_table}")
            except Exception:
                pass
        # 依赖迁移列的索引(列由 _migrate 补充后建立;旧库无列时 _SCHEMA 建索引会崩)
        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_facts_task ON facts(task_id)",
            "CREATE INDEX IF NOT EXISTS idx_materials_hash ON materials(file_hash)",
            "CREATE INDEX IF NOT EXISTS idx_artifact_cache_stage ON artifact_cache(stage)",
            "CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_stage ON task_artifacts(task_id, stage)",
            "CREATE INDEX IF NOT EXISTS idx_llm_call_logs_task ON llm_call_logs(task_id)",
            "CREATE INDEX IF NOT EXISTS idx_llm_call_logs_agent ON llm_call_logs(agent)",
        ):
            try:
                conn.execute(index_sql)
            except Exception:
                pass
        # FTS5 索引幂等重建(旧库已有 units 数据的同步;触发器只覆盖新行)
        try:
            conn.execute("INSERT OR IGNORE INTO units_fts(rowid, content) SELECT id, content FROM units")
        except Exception:
            pass


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移:为已有库补充新列(旧库 CREATE TABLE IF NOT EXISTS 不生效的列)。"""
    migrations = [
        ("report_sentences", "paragraph", "INTEGER NOT NULL DEFAULT 1"),
        ("report_sentences", "edit_history", "TEXT NOT NULL DEFAULT '[]'"),
        ("facts", "fact_type", "TEXT NOT NULL DEFAULT 'STATEMENT'"),
        ("facts", "task_id", "TEXT NOT NULL DEFAULT ''"),
        ("facts", "origin_call_id", "TEXT NOT NULL DEFAULT ''"),
        ("claims", "origin_call_id", "TEXT NOT NULL DEFAULT ''"),
        ("conflicts", "origin_call_id", "TEXT NOT NULL DEFAULT ''"),
        ("inferences", "origin_call_id", "TEXT NOT NULL DEFAULT ''"),
        ("report_sentences", "origin_call_id", "TEXT NOT NULL DEFAULT ''"),
        ("materials", "file_hash", "TEXT NOT NULL DEFAULT ''"),
        ("materials", "parser_version", "TEXT NOT NULL DEFAULT ''"),
        ("materials", "parsed_at", "TEXT"),
        ("material_insights", "key_points", "TEXT NOT NULL DEFAULT '[]'"),
        ("material_insights", "material_role", "TEXT NOT NULL DEFAULT ''"),
        ("material_insights", "claim_support", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("material_insights", "allowed_usage", "TEXT NOT NULL DEFAULT '[]'"),
        ("material_insights", "forbidden_usage", "TEXT NOT NULL DEFAULT '[]'"),
        ("material_insights", "missing_information", "TEXT NOT NULL DEFAULT '[]'"),
        ("conflicts", "claim_ids", "TEXT NOT NULL DEFAULT '[]'"),
        ("evidence", "created_at", "TEXT NOT NULL DEFAULT (datetime('now'))"),
        ("inferences", "analysis_type", "TEXT NOT NULL DEFAULT ''"),
        ("style_variants", "chapter_styles_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("style_variants", "reasoning_profile_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("style_variants", "institution_rules_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("long_memory", "scope", "TEXT NOT NULL DEFAULT 'institution'"),
        ("report_plans", "objective", "TEXT NOT NULL DEFAULT ''"),
        ("report_plans", "audience", "TEXT NOT NULL DEFAULT ''"),
        ("report_plans", "report_type", "TEXT NOT NULL DEFAULT ''"),
        ("report_plans", "core_question", "TEXT NOT NULL DEFAULT ''"),
        ("report_plans", "core_judgment", "TEXT NOT NULL DEFAULT ''"),
        ("report_plans", "narrative_logic", "TEXT NOT NULL DEFAULT ''"),
        ("report_plans", "chapter_plans", "TEXT NOT NULL DEFAULT '[]'"),
        ("report_plans", "budget", "TEXT NOT NULL DEFAULT '{}'"),
        ("report_plans", "required_facts", "TEXT NOT NULL DEFAULT '[]'"),
        ("llm_call_logs", "returned_chars", "INTEGER NOT NULL DEFAULT 0"),
        ("llm_call_logs", "valid_json_chars", "INTEGER NOT NULL DEFAULT 0"),
        ("llm_call_logs", "stored_chars", "INTEGER NOT NULL DEFAULT 0"),
        ("llm_call_logs", "final_chars", "INTEGER NOT NULL DEFAULT 0"),
        ("llm_call_logs", "returned_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("llm_call_logs", "parsed_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("llm_call_logs", "persisted_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("llm_call_logs", "final_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ("llm_call_logs", "candidate_count", "INTEGER NOT NULL DEFAULT 0"),
        ("llm_call_logs", "candidate_total", "INTEGER NOT NULL DEFAULT 0"),
        ("llm_call_logs", "funnel_json", "TEXT NOT NULL DEFAULT '{}'"),
    ]
    for table, column, ddl in migrations:
        columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
