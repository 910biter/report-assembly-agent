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


def _migrate_integrity() -> None:
    """Backfill stable identities and enforce critical PG uniqueness indexes."""
    from sqlalchemy import text

    engine = _get_engine()
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE report_sentences SET lineage_id = 'legacy-' || id::text "
            "WHERE lineage_id IS NULL OR lineage_id = ''"
        ))
        conn.execute(text(
            "UPDATE report_versions SET version_major = version_no "
            "WHERE version_minor = 0 AND version_major = 1 AND version_no > 1"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_report_versions_report_sequence "
            "ON report_versions(report_id, version_no)"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runs_task_revision "
            "ON task_runs(task_id, revision)"
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


def init_db() -> None:
    settings.ensure_dirs()
    create_all_tables()  # PG:ORM 统一建表(含迁移列)
    _migrate_columns()
    _migrate_integrity()


def check_connection() -> bool:
    """连通性检查(PG 可用性)。"""
    try:
        with _get_engine().connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        return True
    except Exception:
        return False
