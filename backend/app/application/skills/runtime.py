"""Shared SQL runtime; document generation lives in skills."""
from __future__ import annotations
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any


from app.core.config import DATA_DIR, GENERATED_DIR
from app.infrastructure.db.connection import connect_sqlite

logger = logging.getLogger(__name__)

OUTPUT_DIR = GENERATED_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)




def _safe_db(db_path: str) -> Path:
    """Resolve a database path without imposing a product filesystem scope."""
    raw_text = str(db_path or "").strip()
    if not raw_text:
        raise ValueError("Путь к базе данных не указан")
    raw = Path(raw_text).expanduser()
    return (raw if raw.is_absolute() else DATA_DIR / raw).resolve()


def run_sql(db_path: str, query: str, params: list = None, max_rows: int = 100) -> dict:
    try:
        safe = _safe_db(db_path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not safe.exists():
        return {"ok": False, "error": f"Не найдена: {db_path}"}

    q_up = query.strip().upper()

    try:
        conn = connect_sqlite(safe, row_factory=sqlite3.Row, journal_mode=None)
        cur = conn.cursor()
        if q_up.startswith(("SELECT", "PRAGMA", "WITH")):
            cur.execute(query, params or [])
            rows = cur.fetchmany(max_rows)
            columns = [d[0] for d in cur.description] if cur.description else []
            data = [dict(r) for r in rows]
            conn.close()
            return {"ok": True, "columns": columns, "rows": data, "count": len(data)}
        else:
            cur.execute(query, params or [])
            conn.commit()
            affected = cur.rowcount
            conn.close()
            return {"ok": True, "affected": affected}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_databases() -> dict:
    dbs = []
    for d in [DATA_DIR.resolve()]:
        if d.exists():
            for f in d.rglob("*.db"):
                dbs.append({"path": str(f), "name": f.name, "size": f.stat().st_size})
    return {"ok": True, "databases": dbs}


def describe_db(db_path: str) -> dict:
    try:
        safe = _safe_db(db_path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        conn = connect_sqlite(safe, row_factory=sqlite3.Row, journal_mode=None)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        schema = {}
        for (tbl,) in tables:
            safe_tbl = '"' + tbl.replace('"', '""') + '"'
            cols = conn.execute(f"PRAGMA table_info({safe_tbl})").fetchall()
            cnt = conn.execute(f"SELECT COUNT(*) FROM {safe_tbl}").fetchone()[0]
            schema[tbl] = {"columns": [{"name": c[1], "type": c[2], "pk": bool(c[5])} for c in cols], "rows": cnt}
        conn.close()
        return {"ok": True, "tables": schema}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# 3. HTTP / API
# ═══════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════
# 4. СКРИНШОТ САЙТА
# ═══════════════════════════════════════════════════════════════
