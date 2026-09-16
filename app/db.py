"""数据库访问层(PostgreSQL,SQLAlchemy 2.0)。

- 连接串:IRA_DB_URL(postgresql://user:pass@host:5432/db),未配置时默认本地 PG
- 建表:ORM create_all(表结构唯一真源 = app/infrastructure/orm.py)
- 业务数据访问统一走 session_scope(零裸 SQL)
"""
from app.config import settings

_engine = None
_SessionLocal = None

_DEFAULT_PG_URL = "postgresql+psycopg://ira:ira@127.0.0.1:5432/ira"


def _get_engine():
    """按配置创建 PG 引擎(IRA_DB_URL 覆盖默认本地连接)。"""
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    url = (getattr(settings, "db_url", "") or "").strip() or _DEFAULT_PG_URL
    _engine = create_engine(url, pool_pre_ping=True, pool_size=8, max_overflow=16)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def session_scope():
    """ORM 会话上下文(业务数据访问统一入口)。"""
    from contextlib import contextmanager

    @contextmanager
    def _scope():
        _get_engine()
        session = _SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return _scope()


def create_all_tables() -> None:
    """ORM 建表(集中定义,替代散落 CREATE TABLE)。"""
    from app.infrastructure.orm import Base
    Base.metadata.create_all(_get_engine())


def _migrate_columns() -> None:
    """Apply additive column migrations for existing PostgreSQL tables."""
    from sqlalchemy import inspect, text
    from app.infrastructure.orm import _MIGRATED_COLUMNS

    engine = _get_engine()
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    by_table: dict[str, set[str]] = {}
    for table in existing_tables:
        by_table[table] = {column["name"] for column in inspector.get_columns(table)}
    with engine.begin() as conn:
        for table, column, ddl in _MIGRATED_COLUMNS:
            if table not in existing_tables or column in by_table.get(table, set()):
                continue
            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}'))


def _default_sql(column) -> str | None:
    """Return a canonical SQL expression for a SQLAlchemy server default."""
    default = column.server_default
    if default is None:
        return None
    value = default.arg
    if hasattr(value, "text"):
        return str(value.text).strip()
    value = str(value).strip()
    from sqlalchemy import Float, Integer
    if isinstance(column.type, (Integer, Float)) and value.replace(".", "", 1).isdigit():
        return value
    return "'" + value.replace("'", "''") + "'"


def _normalize_default(value: str | None) -> str:
    """Compare PostgreSQL defaults without treating casts as drift."""
    import re
    if not value:
        return ""
    normalized = re.sub(r"::[a-zA-Z0-9_\s\[\]]+", "", str(value))
    return normalized.strip().strip("()").replace(" ", "").lower()


def _schema_constraint_plan(existing_columns: dict[str, dict[str, dict]]) -> list[str]:
    """Build idempotent SQL that reconciles existing columns to ORM metadata.

    Existing NULL values are filled only when the canonical column has a
    default. Columns without a safe default deliberately fail at SET NOT NULL
    when bad data exists, keeping migration lossless and auditable.
    """
    from app.infrastructure.orm import Base

    statements: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        table_columns = existing_columns.get(table_name, {})
        for column in table.columns:
            state = table_columns.get(column.name)
            if state is None:
                continue
            expected_default = _default_sql(column)
            actual_default = state.get("default")
            is_serial_default = (
                column.primary_key
                and str(actual_default or "").lstrip().lower().startswith("nextval(")
            )
            if expected_default is None and actual_default and not is_serial_default:
                statements.append(
                    f'ALTER TABLE "{table_name}" ALTER COLUMN "{column.name}" '
                    "DROP DEFAULT"
                )
                actual_default = None
            default_drift = (
                expected_default is not None
                and _normalize_default(actual_default) != _normalize_default(expected_default)
            )
            if default_drift:
                statements.append(
                    f'UPDATE "{table_name}" SET "{column.name}" = {expected_default} '
                    f'WHERE "{column.name}" IS NULL'
                )
                statements.append(
                    f'ALTER TABLE "{table_name}" ALTER COLUMN "{column.name}" '
                    f"SET DEFAULT {expected_default}"
                )
            if not column.nullable and bool(state.get("nullable", True)):
                if not default_drift and expected_default is not None:
                    statements.append(
                        f'UPDATE "{table_name}" SET "{column.name}" = {expected_default} '
                        f'WHERE "{column.name}" IS NULL'
                    )
                statements.append(
                    f'ALTER TABLE "{table_name}" ALTER COLUMN "{column.name}" SET NOT NULL'
                )
    return statements


def _repair_schema_constraints() -> None:
    """Repair nullable/default drift after additive columns and ownership backfill."""
    from sqlalchemy import inspect, text

    inspector = inspect(_get_engine())
    existing_columns = {
        table: {column["name"]: column for column in inspector.get_columns(table)}
        for table in inspector.get_table_names()
    }
    with _get_engine().begin() as conn:
        for statement in _schema_constraint_plan(existing_columns):
            conn.execute(text(statement))


def _report_ownership_statements() -> tuple[str, str]:
    """Return idempotent ownership backfill and audit statements."""
    backfill = """
        WITH version_owners AS (
            SELECT report_id, MIN(BTRIM(task_id)) AS task_id
            FROM report_versions
            WHERE task_id IS NOT NULL AND BTRIM(task_id) <> ''
            GROUP BY report_id
            HAVING COUNT(DISTINCT BTRIM(task_id)) = 1
        )
        UPDATE reports AS report
        SET task_id = owners.task_id
        FROM version_owners AS owners
        WHERE report.id = owners.report_id
          AND BTRIM(COALESCE(report.task_id, '')) = ''
    """
    audit = """
        WITH version_owners AS (
            SELECT
                report_id,
                ARRAY_AGG(DISTINCT BTRIM(task_id) ORDER BY BTRIM(task_id)) AS task_ids,
                COUNT(DISTINCT BTRIM(task_id)) AS owner_count
            FROM report_versions
            WHERE task_id IS NOT NULL AND BTRIM(task_id) <> ''
            GROUP BY report_id
        )
        INSERT INTO report_ownership_audits
            (report_id, status, reason, candidate_task_ids, checked_at)
        SELECT
            report.id,
            CASE
                WHEN BTRIM(COALESCE(report.task_id, '')) <> '' THEN 'resolved'
                WHEN COALESCE(owners.owner_count, 0) = 1 THEN 'resolved'
                WHEN COALESCE(owners.owner_count, 0) > 1 THEN 'ambiguous'
                ELSE 'orphaned'
            END,
            CASE
                WHEN BTRIM(COALESCE(report.task_id, '')) <> '' THEN 'DIRECT_TASK_OWNER'
                WHEN COALESCE(owners.owner_count, 0) = 1 THEN 'VERSION_TASK_OWNER'
                WHEN COALESCE(owners.owner_count, 0) > 1 THEN 'CONFLICTING_VERSION_OWNERS'
                ELSE 'NO_EXPLICIT_TASK_OWNER'
            END,
            COALESCE(TO_JSON(owners.task_ids)::TEXT, '[]'),
            CURRENT_TIMESTAMP
        FROM reports AS report
        LEFT JOIN version_owners AS owners ON owners.report_id = report.id
        ON CONFLICT (report_id) DO UPDATE SET
            status = EXCLUDED.status,
            reason = EXCLUDED.reason,
            candidate_task_ids = EXCLUDED.candidate_task_ids,
            checked_at = EXCLUDED.checked_at
    """
    return backfill, audit


def _backfill_report_ownership() -> None:
    """Populate and audit direct report ownership without guessing.

    A single explicit version owner is safe to promote. Conflicting or missing
    owners remain blank and are recorded as ``ambiguous`` or ``orphaned``.
    """
    from sqlalchemy import text

    with _get_engine().begin() as conn:
        backfill, audit = _report_ownership_statements()
        conn.execute(text(backfill))
        conn.execute(text(audit))


def _migrate_integrity() -> None:
    """Enforce critical PostgreSQL indexes for the current schema."""
    from sqlalchemy import text

    engine = _get_engine()
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_report_versions_report_sequence "
            "ON report_versions(report_id, version_no)"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runs_task_revision "
            "ON task_runs(task_id, revision)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_reports_task "
            "ON reports(task_id)"
        ))
        # Graph read/write paths are task and assertion centric. Keep these
        # indexes here because the schema parser intentionally only creates
        # tables; PostgreSQL owns operational index creation.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_kg_assertions_workspace_status "
            "ON kg_assertions(workspace_id, status)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_kg_membership_task_assertion "
            "ON kg_task_membership(task_id, assertion_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_kg_assertion_facts_fact "
            "ON kg_assertion_facts(fact_id, assertion_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_graph_outbox_status "
            "ON graph_outbox(status, id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_comparison_runs_report "
            "ON material_comparison_runs(report_id, created_at)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_comparison_items_run "
            "ON material_comparison_items(comparison_id, change_type)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_interaction_threads_report "
            "ON interaction_threads(report_id, artifact_type)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_change_proposals_thread "
            "ON change_proposals(thread_id, status)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_change_proposals_execution "
            "ON change_proposals(task_id, execution_status)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_interaction_notifications_task "
            "ON interaction_notifications(task_id, status, created_at)"
        ))


def _remove_retired_schema() -> None:
    """Remove storage objects that no current workflow reads or writes."""
    from sqlalchemy import text

    with _get_engine().begin() as conn:
        conn.execute(text("DELETE FROM file_parse_profiles WHERE material_id IS NULL"))
        conn.execute(text("ALTER TABLE file_parse_profiles DROP COLUMN IF EXISTS node_id"))
        conn.execute(text("ALTER TABLE file_parse_profiles ALTER COLUMN material_id SET NOT NULL"))
        conn.execute(text("ALTER TABLE style_variants DROP COLUMN IF EXISTS evidence_usage_profile_json"))
        conn.execute(text("DROP TABLE IF EXISTS node_summaries CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS export_packages CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS file_nodes CASCADE"))


def init_db() -> None:
    settings.ensure_dirs()
    create_all_tables()  # PG:ORM 统一建表(含迁移列)
    _migrate_columns()
    _backfill_report_ownership()
    _repair_schema_constraints()
    _remove_retired_schema()
    _migrate_integrity()


def check_connection() -> bool:
    """连通性检查(PG 可用性)。"""
    try:
        with _get_engine().connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        return True
    except Exception:
        return False
