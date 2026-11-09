"""바깥 세계를 흉내 내는 저장소.

- threads: 처리를 시작한 문의(thread_id) 목록. 승인 대기 목록을 만들 때 쓴다.
- outbox: 고객에게 '발송된' 답변. 실제 서비스라면 메일/알림톡 API 호출 자리다.
- manual_queue: 반려되어 담당자가 직접 처리해야 하는 건.
- notifications: 담당자에게 보낸 승인 요청 알림.

체크포인트(멈춘 상태)는 agent.py 의 SqliteSaver 가 별도 파일에 저장한다.
"""
import os
import sqlite3
from datetime import datetime
from pathlib import Path

DB_DIR = Path(os.getenv("HITL_DB_DIR", Path(__file__).parent / "runtime"))
DB_DIR.mkdir(parents=True, exist_ok=True)

_conn = sqlite3.connect(DB_DIR / "app.db", check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS threads(
        thread_id TEXT PRIMARY KEY, inquiry_id TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS outbox(
        thread_id TEXT PRIMARY KEY, inquiry_id TEXT, to_addr TEXT, body TEXT,
        mode TEXT, sent_at TEXT);
    CREATE TABLE IF NOT EXISTS manual_queue(
        thread_id TEXT PRIMARY KEY, inquiry_id TEXT, reason TEXT, at TEXT);
    CREATE TABLE IF NOT EXISTS notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT, message TEXT, at TEXT);
    """
)
_conn.commit()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def add_thread(thread_id: str, inquiry_id: str) -> None:
    _conn.execute("INSERT OR IGNORE INTO threads VALUES (?,?,?)", (thread_id, inquiry_id, now()))
    _conn.commit()


def list_threads() -> list[sqlite3.Row]:
    return _conn.execute("SELECT * FROM threads ORDER BY created_at, thread_id").fetchall()


def send_email(thread_id: str, inquiry_id: str, to_addr: str, body: str, mode: str) -> bool:
    """발송. 같은 thread_id 로 두 번 불려도 한 번만 나간다(멱등). 새로 보냈으면 True."""
    cur = _conn.execute(
        "INSERT OR IGNORE INTO outbox VALUES (?,?,?,?,?,?)",
        (thread_id, inquiry_id, to_addr, body, mode, now()),
    )
    _conn.commit()
    return cur.rowcount == 1


def to_manual(thread_id: str, inquiry_id: str, reason: str) -> None:
    _conn.execute("INSERT OR REPLACE INTO manual_queue VALUES (?,?,?,?)", (thread_id, inquiry_id, reason, now()))
    _conn.commit()


def notify(thread_id: str, message: str) -> None:
    _conn.execute("INSERT INTO notifications(thread_id, message, at) VALUES (?,?,?)", (thread_id, message, now()))
    _conn.commit()


def outbox() -> list[sqlite3.Row]:
    return _conn.execute("SELECT * FROM outbox ORDER BY sent_at").fetchall()


def manual_queue() -> list[sqlite3.Row]:
    return _conn.execute("SELECT * FROM manual_queue ORDER BY at").fetchall()


def notifications() -> list[sqlite3.Row]:
    return _conn.execute("SELECT * FROM notifications ORDER BY id DESC").fetchall()


def reset() -> None:
    for t in ["threads", "outbox", "manual_queue", "notifications"]:
        _conn.execute(f"DELETE FROM {t}")
    _conn.commit()
