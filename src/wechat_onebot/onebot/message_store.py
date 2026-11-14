from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .message_id import hash_message_id_int32


class MessageStore:
    """Durable per-account OneBot message and identity store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS message_identities (
                message_key TEXT PRIMARY KEY,
                account TEXT NOT NULL,
                message_id INTEGER NOT NULL UNIQUE,
                event_name TEXT NOT NULL,
                session_key TEXT NOT NULL,
                sequence_key TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER PRIMARY KEY,
                account TEXT NOT NULL,
                event_name TEXT NOT NULL,
                session_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS message_order (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL UNIQUE
            )
            """
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS messages_account_time ON messages(account, created_at)")
        self._db.execute("""CREATE TABLE IF NOT EXISTS native_message_ids (
            account TEXT NOT NULL, session_key TEXT NOT NULL, native_id TEXT NOT NULL,
            message_id INTEGER NOT NULL, PRIMARY KEY(account, session_key, native_id))""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS reference_snapshots (
            message_id INTEGER PRIMARY KEY, account TEXT NOT NULL, payload TEXT NOT NULL)""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS forward_messages (
            account TEXT NOT NULL, forward_id TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(account, forward_id))""")
        # Backfill pre-existing rows with a deterministic local order.
        self._db.execute(
            "INSERT OR IGNORE INTO message_order(message_id) "
            "SELECT message_id FROM messages ORDER BY created_at, message_id"
        )
        self._db.commit()

        self._db.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                account TEXT NOT NULL, user_id TEXT NOT NULL, group_id TEXT NOT NULL,
                nickname TEXT NOT NULL, card TEXT NOT NULL, source TEXT NOT NULL,
                observed_at INTEGER NOT NULL,
                PRIMARY KEY (account, user_id, group_id)
            )
        """)
        self._db.commit()

        self._db.execute("""
            CREATE TABLE IF NOT EXISTS group_memberships (
                account TEXT NOT NULL, group_id TEXT NOT NULL, user_id TEXT NOT NULL,
                PRIMARY KEY (account, group_id, user_id)
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS mention_labels (
                account TEXT NOT NULL, group_id TEXT NOT NULL, user_id TEXT NOT NULL,
                label TEXT NOT NULL, updated_at INTEGER NOT NULL,
                PRIMARY KEY (account, group_id, user_id)
            )
        """)
        self._db.commit()

    def remember_profiles(self, account: str, rows: list[tuple[str, str, str, str, str]]) -> None:
        """Persist observed identities; missing fields never erase known names."""
        with self._lock:
            self._db.executemany("""
                INSERT INTO user_profiles VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account, user_id, group_id) DO UPDATE SET
                    nickname=CASE WHEN excluded.nickname!='' THEN excluded.nickname ELSE nickname END,
                    card=CASE WHEN excluded.card!='' THEN excluded.card ELSE card END,
                    source=excluded.source, observed_at=excluded.observed_at
            """, [(account, user, group, nickname.strip(), card.strip(), source, int(time.time()))
                  for user, group, nickname, card, source in rows if user])
            self._db.executemany("INSERT OR IGNORE INTO group_memberships VALUES (?, ?, ?)",
                                 [(account, group, user) for user, group, *_ in rows if user and group])
            self._db.commit()

    def replace_memberships(self, account: str, group: str, users: list[str]) -> None:
        """A complete native roster invalidates absent memberships, not historical names."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM group_memberships WHERE account=? AND group_id=?", (account, group))
            self._db.executemany("INSERT INTO group_memberships VALUES (?, ?, ?)",
                                 [(account, group, user) for user in set(users) if user])

    def profile(self, account: str, user: str, group: str = "") -> dict[str, str] | None:
        with self._lock:
            identity = self._db.execute(
                "SELECT nickname FROM user_profiles WHERE account=? AND user_id=? AND group_id=''",
                (account, user)).fetchone()
            membership = self._db.execute(
                "SELECT card FROM user_profiles WHERE account=? AND user_id=? AND group_id=?",
                (account, user, group)).fetchone() if group else None
            if identity is None and membership is None:
                return None
            return {"nickname": identity[0] if identity else "",
                    "card": membership[0] if membership else ""}

    def has_membership(self, account: str, user: str, group: str) -> bool:
        with self._lock:
            return self._db.execute(
                "SELECT 1 FROM group_memberships WHERE account=? AND user_id=? AND group_id=?",
                (account, user, group)).fetchone() is not None

    def mention_label(self, account: str, user: str, group: str) -> str:
        with self._lock:
            row = self._db.execute(
                "SELECT label FROM mention_labels WHERE account=? AND user_id=? AND group_id=?",
                (account, user, group)).fetchone()
            return str(row[0]) if row else ""

    def set_mention_label(self, account: str, user: str, group: str, label: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO mention_labels VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(account, group_id, user_id) DO UPDATE SET "
                "label=excluded.label, updated_at=excluded.updated_at",
                (account, group, user, label, int(time.time())),
            )

    def delete_mention_label(self, account: str, user: str, group: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "DELETE FROM mention_labels WHERE account=? AND group_id=? AND user_id=?",
                (account, group, user),
            )

    def list_mention_labels(self, account: str, group: str = "") -> list[dict[str, str]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT group_id, user_id, label FROM mention_labels "
                "WHERE account=? AND (?='' OR group_id=?) ORDER BY group_id, user_id",
                (account, group, group),
            ).fetchall()
        return [{"group": str(row[0]), "user": str(row[1]), "label": str(row[2])}
                for row in rows]

    def forget_membership(self, account: str, user: str, group: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM group_memberships WHERE account=? AND user_id=? AND group_id=?",
                             (account, user, group))

    @staticmethod
    def _key(account: str, event_name: str, session_key: str, sequence_key: str) -> str:
        return f"{account}\0{event_name}\0{session_key}\0{sequence_key}"

    def get_or_allocate_id(
        self,
        *,
        account: str,
        event_name: str,
        session_key: str,
        sequence_key: str,
    ) -> int:
        key = self._key(account, event_name, session_key, sequence_key)
        with self._lock:
            row = self._db.execute(
                "SELECT message_id FROM message_identities WHERE message_key=?", (key,)
            ).fetchone()
            if row is not None:
                return int(row[0])
            salt = 0
            while True:
                candidate = hash_message_id_int32(sequence_key, session_key, event_name, salt)
                occupied = self._db.execute(
                    "SELECT message_key FROM message_identities WHERE message_id=?", (candidate,)
                ).fetchone()
                if occupied is None:
                    try:
                        self._db.execute(
                            "INSERT INTO message_identities(message_key, account, message_id, event_name, session_key, sequence_key, created_at) "
                            "VALUES(?,?,?,?,?,?,?)",
                            (key, account, candidate, event_name, session_key, sequence_key, int(time.time())),
                        )
                        self._db.commit()
                        return candidate
                    except sqlite3.IntegrityError:
                        self._db.rollback()
                        row = self._db.execute(
                            "SELECT message_id FROM message_identities WHERE message_key=?", (key,)
                        ).fetchone()
                        if row is not None:
                            return int(row[0])
                salt += 1

    def allocate_sent_id(self, *, account: str, session_key: str, nonce: str) -> int:
        return self.get_or_allocate_id(
            account=account,
            event_name="private_message_sent",
            session_key=session_key,
            sequence_key=f"sent:{nonce}",
        )

    def put(self, message_id: int, *, account: str, event_name: str, session_key: str,
            payload: dict[str, Any]) -> bool:
        """Persist a message and report whether its content changed."""
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            row = self._db.execute(
                "SELECT payload FROM messages WHERE message_id=?", (int(message_id),)
            ).fetchone()
            if row is not None and row[0] == encoded:
                return False
            self._db.execute(
                "INSERT INTO messages(message_id, account, event_name, session_key, payload, created_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET payload=excluded.payload",
                (int(message_id), account, event_name, session_key, encoded, int(time.time())),
            )
            self._db.execute(
                "INSERT OR IGNORE INTO message_order(message_id) VALUES(?)", (int(message_id),)
            )
            self._db.commit()
            return True

    def get(self, message_id: int, *, account: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            if account is None:
                row = self._db.execute(
                    "SELECT payload FROM messages WHERE message_id=? UNION ALL "
                    "SELECT payload FROM reference_snapshots WHERE message_id=? LIMIT 1",
                    (int(message_id), int(message_id))
                ).fetchone()
            else:
                row = self._db.execute(
                    "SELECT payload FROM messages WHERE message_id=? AND account=? UNION ALL "
                    "SELECT payload FROM reference_snapshots WHERE message_id=? AND account=? LIMIT 1",
                    (int(message_id), account, int(message_id), account),
                ).fetchone()
            return json.loads(row[0]) if row else None

    def remember_native_id(self, account: str, session_key: str, native_id: str, message_id: int) -> None:
        if not native_id or len(native_id) > 20 or not native_id.isascii() or not native_id.isdecimal() or int(native_id) == 0:
            return
        with self._lock:
            self._db.execute("INSERT INTO native_message_ids VALUES (?,?,?,?) "
                             "ON CONFLICT(account,session_key,native_id) DO UPDATE SET message_id=excluded.message_id",
                             (account, session_key, native_id, message_id))
            self._db.commit()

    def native_message(self, account: str, session_key: str, native_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT message_id FROM native_message_ids "
                                   "WHERE account=? AND session_key=? AND native_id=?",
                                   (account, session_key, native_id)).fetchone()
            return self.get(row[0], account=account) if row else None

    def put_reference(self, message_id: int, account: str, payload: dict[str, Any]) -> None:
        # Quoted metadata is queryable, but is not a newly received history event.
        with self._lock:
            self._db.execute("INSERT INTO reference_snapshots VALUES (?,?,?) ON CONFLICT(message_id) "
                             "DO UPDATE SET payload=excluded.payload WHERE reference_snapshots.account=excluded.account",
                             (message_id, account, json.dumps(payload, ensure_ascii=False)))
            self._db.commit()

    def is_reference(self, message_id: int, account: str) -> bool:
        with self._lock:
            return self._db.execute('SELECT 1 FROM reference_snapshots WHERE message_id=? AND account=?',
                                    (message_id, account)).fetchone() is not None

    def put_forward(self, account: str, forward_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute('INSERT INTO forward_messages VALUES (?,?,?) ON CONFLICT(account,forward_id) '
                             'DO UPDATE SET payload=excluded.payload',
                             (account, forward_id, json.dumps(payload, ensure_ascii=False)))
            self._db.commit()

    def get_forward(self, account: str, forward_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute('SELECT payload FROM forward_messages WHERE account=? AND forward_id=?',
                                   (account, forward_id)).fetchone()
            return json.loads(row[0]) if row else None

    def history(
        self,
        *,
        account: str,
        session_key: str,
        count: int = 20,
        message_id: int | None = None,
        reverse_order: bool = False,
    ) -> list[dict[str, Any]]:
        """Return runtime-persisted messages in chronological order.

        With no anchor, the latest ``count`` messages are returned. With an
        anchor and ``reverse_order=False``, older messages are returned. With
        ``reverse_order=True``, newer messages after the anchor are returned.
        """
        count = max(1, min(int(count), 100))
        with self._lock:
            if message_id is None:
                rows = self._db.execute(
                    "SELECT o.seq, m.payload FROM messages m JOIN message_order o ON o.message_id=m.message_id "
                    "WHERE m.account=? AND m.session_key=? ORDER BY o.seq DESC LIMIT ?",
                    (account, session_key, count),
                ).fetchall()
                return [json.loads(row[1]) for row in reversed(rows)]
            anchor = self._db.execute(
                "SELECT o.seq FROM messages m JOIN message_order o ON o.message_id=m.message_id "
                "WHERE m.account=? AND m.session_key=? AND m.message_id=?",
                (account, session_key, int(message_id)),
            ).fetchone()
            if anchor is None:
                return []
            anchor_seq = int(anchor[0])
            if reverse_order:
                rows = self._db.execute(
                    "SELECT o.seq, m.payload FROM messages m JOIN message_order o ON o.message_id=m.message_id "
                    "WHERE m.account=? AND m.session_key=? AND o.seq>? ORDER BY o.seq ASC LIMIT ?",
                    (account, session_key, anchor_seq, count),
                ).fetchall()
                return [json.loads(row[1]) for row in rows]
            rows = self._db.execute(
                "SELECT o.seq, m.payload FROM messages m JOIN message_order o ON o.message_id=m.message_id "
                "WHERE m.account=? AND m.session_key=? AND o.seq<? ORDER BY o.seq DESC LIMIT ?",
                (account, session_key, anchor_seq, count),
            ).fetchall()
            return [json.loads(row[1]) for row in reversed(rows)]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) FROM messages").fetchone()
            identity_row = self._db.execute("SELECT COUNT(*) FROM message_identities").fetchone()
            size = self.path.stat().st_size if self.path.is_file() else 0
            return {
                "message_count": int(row[0] if row else 0),
                "identity_count": int(identity_row[0] if identity_row else 0),
                "database_bytes": size,
            }

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "MessageStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
