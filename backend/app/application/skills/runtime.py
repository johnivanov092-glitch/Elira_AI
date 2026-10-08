"""Shared SQL, HTTP and screenshot runtime; document generation lives in skills."""
from __future__ import annotations
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import requests as http_lib

from app.core.config import DATA_DIR, GENERATED_DIR
from app.infrastructure.db.connection import connect_sqlite

logger = logging.getLogger(__name__)

OUTPUT_DIR = GENERATED_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def screenshot_capability_status() -> dict:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return {
            "feature": "screenshot",
            "available": False,
            "reason": "optional_dependency_missing",
            "missing_packages": ["playwright"],
            "hint": "pip install playwright && playwright install chromium",
        }
    return {
        "feature": "screenshot",
        "available": True,
        "reason": None,
        "missing_packages": [],
        "hint": None,
    }


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

def http_request(url: str, method: str = "GET", headers: dict = None, body: Any = None,
                 timeout: int = 15, allow_loopback_ports: set = None) -> dict:
    del allow_loopback_ports

    try:
        kw = {"url": url, "headers": headers or {}, "timeout": timeout}
        method = method.upper()
        if method == "GET":
            resp = http_lib.get(**kw)
        elif method == "POST":
            kw["json"] = body if isinstance(body, (dict, list)) else None
            kw["data"] = body if not isinstance(body, (dict, list)) else None
            resp = http_lib.post(**kw)
        elif method == "PUT":
            kw["json"] = body if isinstance(body, (dict, list)) else None
            resp = http_lib.put(**kw)
        elif method == "DELETE":
            resp = http_lib.delete(**kw)
        else:
            return {"ok": False, "error": f"Неизвестный метод: {method}"}

        ct = resp.headers.get("content-type", "")
        try:
            rbody = resp.json() if "json" in ct else resp.text[:30000]
        except Exception:
            rbody = resp.text[:30000]

        return {"ok": True, "status": resp.status_code, "body": rbody,
                "url": str(resp.url), "elapsed_ms": int(resp.elapsed.total_seconds() * 1000)}
    except http_lib.Timeout:
        return {"ok": False, "error": f"Таймаут ({timeout}с)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# 4. СКРИНШОТ САЙТА
# ═══════════════════════════════════════════════════════════════

def screenshot_url(url: str, width: int = 1280, height: int = 800, full_page: bool = False) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        status = screenshot_capability_status()
        return {
            "ok": False,
            "error": "Screenshot feature is unavailable",
            "feature": status["feature"],
            "reason": status["reason"],
            "missing_packages": status["missing_packages"],
            "hint": status["hint"],
        }

    fname = f"screenshot_{int(time.time())}.png"
    path = OUTPUT_DIR / fname
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(url, wait_until="networkidle", timeout=20000)
            page.screenshot(path=str(path), full_page=full_page)
            title = page.title()
            browser.close()
        return {"ok": True, "path": str(path), "filename": fname, "title": title,
                "download_url": f"/api/skills/download/{fname}", "view_url": f"/api/skills/view/{fname}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
