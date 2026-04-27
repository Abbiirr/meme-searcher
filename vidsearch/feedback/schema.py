from __future__ import annotations

import threading
from pathlib import Path

from vidsearch.storage import pg as pg_store


_SCHEMA_LOCK = threading.Lock()
_SCHEMA_APPLIED_FOR: set[str] = set()


def ensure_feedback_schema() -> None:
    """Apply the idempotent feedback migration for existing local volumes."""
    url = pg_store.DATABASE_URL
    if url in _SCHEMA_APPLIED_FOR:
        return

    with _SCHEMA_LOCK:
        if url in _SCHEMA_APPLIED_FOR:
            return

        sql_path = Path(__file__).resolve().parents[2] / "infra" / "postgres" / "003_feedback_loop.sql"
        sql = sql_path.read_text(encoding="utf-8")
        with pg_store.get_cursor() as cur:
            cur.execute(sql)
        _SCHEMA_APPLIED_FOR.add(url)
