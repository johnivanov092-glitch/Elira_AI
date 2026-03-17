
import sqlite3
from pathlib import Path

DB = Path("data/jarvis_chat.db")
DB.parent.mkdir(exist_ok=True)

def init():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS chats(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT
        )
        '''
    )
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            role TEXT,
            content TEXT
        )
        '''
    )
    conn.commit()
    conn.close()
