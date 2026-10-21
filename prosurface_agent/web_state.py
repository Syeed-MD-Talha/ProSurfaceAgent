"""Private browser conversations and durable owner follow-up requests.

This database is separate from the existing customer workspace. Browser and
owner-link capabilities are stored as hashes; only their holders can use them.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class WebState:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "conversations.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY, access_hash TEXT NOT NULL UNIQUE,
                    model_messages TEXT NOT NULL DEFAULT '[]',
                    owner_cursor INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL REFERENCES chats(id), role TEXT NOT NULL,
                    content TEXT NOT NULL, attachment_name TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'chat', question TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS chat_messages_chat ON chat_messages(chat_id, id);
                CREATE TABLE IF NOT EXISTS turns (
                    chat_id TEXT NOT NULL REFERENCES chats(id), request_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL, reply_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'processing',
                    PRIMARY KEY(chat_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS owner_questions (
                    id TEXT PRIMARY KEY, chat_id TEXT NOT NULL REFERENCES chats(id),
                    client_question TEXT NOT NULL, owner_question TEXT NOT NULL,
                    context TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', answer TEXT NOT NULL DEFAULT '',
                    token_hash TEXT UNIQUE, expires_at REAL NOT NULL, created_at REAL NOT NULL,
                    delivery TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_question
                    ON owner_questions(chat_id, fingerprint) WHERE status = 'pending';
                CREATE UNIQUE INDEX IF NOT EXISTS turns_user ON turns(user_id);
                CREATE UNIQUE INDEX IF NOT EXISTS turns_reply ON turns(reply_id) WHERE reply_id IS NOT NULL;
            """)
            # A stopped process may have been interrupted during a model/mail call.
            db.execute("UPDATE turns SET status='failed' WHERE status='processing'")
            db.execute("UPDATE owner_questions SET delivery=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END WHERE delivery='sending'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def chat_for_token(self, token: str | None) -> str | None:
        if not token or len(token) > 128:
            return None
        with self.connect() as db:
            row = db.execute("SELECT id FROM chats WHERE access_hash=?", (digest(token),)).fetchone()
        return row["id"] if row else None

    def open_chat(self, token: str | None) -> tuple[str, str]:
        chat_id = self.chat_for_token(token)
        if chat_id:
            return chat_id, token
        chat_id, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("INSERT INTO chats(id,access_hash) VALUES (?,?)", (chat_id, digest(token)))
        return chat_id, token

    def snapshot(self, chat_id: str) -> tuple[list, int]:
        with self.connect() as db:
            row = db.execute("SELECT model_messages,owner_cursor FROM chats WHERE id=?", (chat_id,)).fetchone()
        return json.loads(row["model_messages"]), row["owner_cursor"]

    def history(self, chat_id: str, after: int = 0) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT m.id,m.role,m.content,m.attachment_name,m.source,m.question,t.request_id "
                "FROM chat_messages m LEFT JOIN turns t ON t.user_id=m.id OR t.reply_id=m.id "
                "WHERE m.chat_id=? AND m.id>? ORDER BY m.id LIMIT 500", (chat_id, after)
            ).fetchall()
        return [dict(row) for row in rows]

    def owner_updates(self, chat_id: str, after: int) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,content,question FROM chat_messages WHERE chat_id=? AND id>? "
                "AND source='owner' ORDER BY id", (chat_id, after)
            ).fetchall()
        return [dict(row) for row in rows]

    def turn_result(self, chat_id: str, request_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM turns WHERE chat_id=? AND request_id=?", (chat_id, request_id)).fetchone()
            if not row:
                return None
            result = dict(row)
            if row["status"] == "completed":
                result["reply"] = db.execute("SELECT content FROM chat_messages WHERE id=?", (row["reply_id"],)).fetchone()[0]
            return result

    def begin_turn(self, chat_id: str, request_id: str, text: str, attachment: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM turns WHERE chat_id=? AND request_id=?", (chat_id, request_id)).fetchone()
            if row:
                result = dict(row)
                if row["status"] == "completed":
                    result["reply"] = db.execute("SELECT content FROM chat_messages WHERE id=?", (row["reply_id"],)).fetchone()[0]
                elif row["status"] == "failed":
                    db.execute("UPDATE turns SET status='processing' WHERE chat_id=? AND request_id=?", (chat_id, request_id))
                    result["status"] = "retry"
                return result
            cur = db.execute(
                "INSERT INTO chat_messages(chat_id,role,content,attachment_name) VALUES (?,'user',?,?)",
                (chat_id, text, attachment),
            )
            db.execute("INSERT INTO turns(chat_id,request_id,user_id) VALUES (?,?,?)", (chat_id, request_id, cur.lastrowid))
            return {"status": "new", "user_id": cur.lastrowid}

    def finish_turn(self, chat_id: str, request_id: str, reply: str, model_messages: list, owner_cursor: int) -> int:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute("INSERT INTO chat_messages(chat_id,role,content) VALUES (?,'assistant',?)", (chat_id, reply))
            reply_id = cur.lastrowid
            db.execute("UPDATE turns SET status='completed',reply_id=? WHERE chat_id=? AND request_id=?", (reply_id, chat_id, request_id))
            db.execute("UPDATE chats SET model_messages=?,owner_cursor=? WHERE id=?", (json.dumps(model_messages, ensure_ascii=False), owner_cursor, chat_id))
            return reply_id

    def fail_turn(self, chat_id: str, request_id: str):
        with self.connect() as db:
            db.execute("UPDATE turns SET status='failed' WHERE chat_id=? AND request_id=?", (chat_id, request_id))

    def queue_question(self, chat_id: str, client_question: str, owner_question: str, context: str, ttl_hours: int) -> dict:
        now = time.time()
        fingerprint = digest(" ".join(client_question.casefold().split()))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE owner_questions SET status='expired' WHERE status='pending' AND expires_at<=?", (now,))
            existing = db.execute(
                "SELECT id,status,delivery,attempts FROM owner_questions WHERE chat_id=? AND fingerprint=? AND status='pending'",
                (chat_id, fingerprint),
            ).fetchone()
            if existing:
                return dict(existing)
            qid = "OQ-" + uuid.uuid4().hex.upper()
            db.execute(
                "INSERT INTO owner_questions(id,chat_id,client_question,owner_question,context,fingerprint,expires_at,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (qid, chat_id, client_question[:12000], owner_question[:4000], context[:6000], fingerprint, now + ttl_hours * 3600, now),
            )
            return {"id": qid, "status": "pending"}

    def pending(self, chat_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,delivery,attempts FROM owner_questions WHERE chat_id=? AND status='pending' AND expires_at>?",
                (chat_id, time.time()),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_email(self) -> dict | None:
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM owner_questions WHERE status='pending' AND expires_at>? "
                "AND delivery IN ('queued','failed') AND attempts<3 AND next_attempt<=? ORDER BY created_at LIMIT 1",
                (now, now),
            ).fetchone()
            if not row:
                return None
            token = secrets.token_urlsafe(32)
            db.execute("UPDATE owner_questions SET token_hash=?,delivery='sending',attempts=attempts+1 WHERE id=?", (digest(token), row["id"]))
            return {**dict(row), "token": token}

    def email_result(self, question_id: str, sent: bool):
        with self.connect() as db:
            db.execute(
                "UPDATE owner_questions SET delivery=?,next_attempt=? WHERE id=?",
                ("sent" if sent else "failed", time.time() + 60, question_id),
            )

    def owner_question(self, token: str) -> dict | None:
        if not token or len(token) > 128:
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT id,client_question,owner_question,context,status,answer,expires_at FROM owner_questions WHERE token_hash=?",
                (digest(token),),
            ).fetchone()
        if not row or row["expires_at"] <= time.time():
            return None
        return dict(row)

    def answer(self, token: str, answer: str) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM owner_questions WHERE token_hash=? AND status='pending' AND expires_at>?",
                (digest(token), time.time()),
            ).fetchone()
            if not row:
                return False
            db.execute("UPDATE owner_questions SET status='answered',answer=? WHERE id=?", (answer, row["id"]))
            db.execute(
                "INSERT INTO chat_messages(chat_id,role,content,source,question) VALUES (?,'assistant',?,'owner',?)",
                (row["chat_id"], answer, row["client_question"]),
            )
            return True
