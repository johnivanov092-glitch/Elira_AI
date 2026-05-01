from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Dict, Optional

from app.core.data_files import data_file
from app.infrastructure.db.connection import connect_sqlite

DB_PATH = data_file("elira_state.db")


def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_store (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT,
                title TEXT,
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'chat',
                pinned INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

def build_mode_reply(
    content: str,
    mode: str = "chat",
    model: Optional[str] = None,
    agent_profile: Optional[str] = None,
) -> Dict[str, Any]:
    mode = (mode or "chat").lower()
    content = content.strip()

    if mode == "code":
        assistant = (
            "Р РµР¶РёРј Code Р°РєС‚РёРІРёСЂРѕРІР°РЅ.\n\n"
            "РЎР»РµРґСѓСЋС‰РёР№ С€Р°Рі: РѕС‚РєСЂС‹С‚СЊ С„Р°Р№Р» РїСЂРѕРµРєС‚Р°, СЃРѕР±СЂР°С‚СЊ diff preview Рё РїРѕРґРіРѕС‚РѕРІРёС‚СЊ patch plan.\n\n"
            f"Р—Р°РїСЂРѕСЃ: {content}"
        )
    elif mode == "research":
        assistant = (
            "Р РµР¶РёРј Research Р°РєС‚РёРІРёСЂРѕРІР°РЅ.\n\n"
            "РЎР»РµРґСѓСЋС‰РёР№ С€Р°Рі: СЃРѕР±СЂР°С‚СЊ РёСЃС‚РѕС‡РЅРёРєРё, РІС‹РґРµР»РёС‚СЊ РєР»СЋС‡РµРІС‹Рµ С„Р°РєС‚С‹ Рё РІРµСЂРЅСѓС‚СЊ СЃС‚СЂСѓРєС‚СѓСЂРёСЂРѕРІР°РЅРЅС‹Р№ РѕР±Р·РѕСЂ.\n\n"
            f"Р—Р°РїСЂРѕСЃ: {content}"
        )
    elif mode == "image":
        assistant = (
            "Р РµР¶РёРј Text-to-Image Р°РєС‚РёРІРёСЂРѕРІР°РЅ.\n\n"
            "РЎР»РµРґСѓСЋС‰РёР№ С€Р°Рі: СЃС„РѕСЂРјРёСЂРѕРІР°С‚СЊ image prompt Рё РїР°СЂР°РјРµС‚СЂС‹ РіРµРЅРµСЂР°С†РёРё.\n\n"
            f"Р—Р°РїСЂРѕСЃ: {content}"
        )
    elif mode == "orchestrator":
        assistant = (
            "Р РµР¶РёРј Orchestrator Р°РєС‚РёРІРёСЂРѕРІР°РЅ.\n\n"
            "РЎР»РµРґСѓСЋС‰РёР№ С€Р°Рі: СЂР°Р·Р±РёС‚СЊ Р·Р°РґР°С‡Сѓ РЅР° РїРѕРґР°РіРµРЅС‚РѕРІ, СЃРѕСЃС‚Р°РІРёС‚СЊ РїР»Р°РЅ РІС‹РїРѕР»РЅРµРЅРёСЏ Рё С‚СЂРµРє СЃС‚Р°С‚СѓСЃРѕРІ.\n\n"
            f"Р—Р°РїСЂРѕСЃ: {content}"
        )
    else:
        assistant = (
            "Р РµР¶РёРј Chat Р°РєС‚РёРІРёСЂРѕРІР°РЅ.\n\n"
            "Elira РїСЂРёРЅСЏР»Р° СЃРѕРѕР±С‰РµРЅРёРµ Рё РїРѕРґРіРѕС‚РѕРІРёР»Р° РѕР±С‹С‡РЅС‹Р№ РґРёР°Р»РѕРіРѕРІС‹Р№ РѕС‚РІРµС‚.\n\n"
            f"Р—Р°РїСЂРѕСЃ: {content}"
        )

    return {
        "mode": mode,
        "assistant_content": assistant,
        "status": "ok",
        "model": model,
        "agent_profile": agent_profile,
    }


def list_memory(q: str = ""):
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=sqlite3.Row, journal_mode=None)
    try:
        if q.strip():
            rows = conn.execute(
                """
                SELECT id, chat_id, title, content, source, pinned, created_at, updated_at
                FROM memory_store
                WHERE content LIKE ? OR COALESCE(title, '') LIKE ?
                ORDER BY pinned DESC, updated_at DESC
                """,
                (f"%{q}%", f"%{q}%"),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, chat_id, title, content, source, pinned, created_at, updated_at
                FROM memory_store
                ORDER BY pinned DESC, updated_at DESC
                """
            ).fetchall()

        items = [dict(row) for row in rows]
        for item in items:
            item["pinned"] = bool(item["pinned"])
        return {"items": items}
    finally:
        conn.close()


def save_memory(
    content: str,
    chat_id: Optional[str] = None,
    title: Optional[str] = None,
    source: str = "chat",
    pinned: bool = False,
) -> dict:
    ensure_db()
    now = datetime.utcnow().isoformat()
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
    try:
        cur = conn.execute(
            """
            INSERT INTO memory_store (
                chat_id, title, content, source, pinned, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                title,
                content,
                source,
                1 if pinned else 0,
                now,
                now,
            ),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "chat_id": chat_id,
            "title": title,
            "content": content,
            "source": source,
            "pinned": pinned,
            "created_at": now,
            "updated_at": now,
        }
    finally:
        conn.close()


def delete_memory(memory_id: int) -> dict:
    ensure_db()
    conn = connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)
    try:
        conn.execute("DELETE FROM memory_store WHERE id = ?", (memory_id,))
        conn.commit()
        return {"status": "ok", "deleted_id": memory_id}
    finally:
        conn.close()
