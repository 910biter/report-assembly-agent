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


def init_db() -> None:
    settings.ensure_dirs()
    create_all_tables()  # PG:ORM 统一建表(含迁移列)


def check_connection() -> bool:
    """连通性检查(PG 可用性)。"""
    try:
        with _get_engine().connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        return True
    except Exception:
        return False