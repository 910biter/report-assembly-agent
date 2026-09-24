"""PostgreSQL session locks shared by report and graph execution."""
from contextlib import contextmanager
from hashlib import blake2b

from sqlalchemy import func, select

from app.db import _get_engine


def _key(name: str) -> int:
    return int.from_bytes(blake2b(name.encode(), digest_size=8).digest(), "big", signed=True)


_EXECUTION_KEY = _key("report-assembly:execution")


def task_key(task_id: str) -> int:
    return _key(f"report-assembly:task:{task_id}")


class ExecutionSlot:
    def __init__(self, connection):
        self.connection = connection

    def check(self):
        # Never reconnect a lost lock session and silently continue as owner.
        if self.connection.invalidated or self.connection.closed:
            raise RuntimeError("EXECUTION_LOCK_LOST")
        self.connection.execute(select(1))

    @contextmanager
    def task(self, task_id: str):
        self.check()
        key = task_key(task_id)
        self.connection.execute(select(func.pg_advisory_lock(key)))
        try:
            yield
        finally:
            if not self.connection.invalidated and not self.connection.closed:
                self.connection.execute(select(func.pg_advisory_unlock(key)))


@contextmanager
def execution_slot(*, wait: bool = False):
    """Hold one process-independent execution slot without a long transaction.

    A crash releases the connection's locks. No elapsed-time threshold can
    revoke a still-running worker and start a duplicate build.
    """
    with _get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        if wait:
            connection.execute(select(func.pg_advisory_lock(_EXECUTION_KEY)))
            acquired = True
        else:
            acquired = bool(connection.execute(
                select(func.pg_try_advisory_lock(_EXECUTION_KEY))
            ).scalar_one())
        try:
            yield ExecutionSlot(connection) if acquired else None
        finally:
            if acquired and not connection.invalidated and not connection.closed:
                connection.execute(select(func.pg_advisory_unlock(_EXECUTION_KEY)))


def require_idle_task(session, task_id: str) -> None:
    """Protect activation/deletion against an executing report or graph."""
    if not session.execute(select(func.pg_try_advisory_xact_lock(task_key(task_id)))).scalar_one():
        raise ValueError("TASK_BUSY")
