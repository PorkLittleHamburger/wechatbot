from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path


class IdMapper:
    """Persist stable numeric OneBot IDs for arbitrary WeChat identifiers."""

    _MAX_ID = 0x7FFFFFFF

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS id_map (
                kind TEXT NOT NULL,
                external_id TEXT NOT NULL,
                number INTEGER NOT NULL UNIQUE,
                created_at INTEGER NOT NULL DEFAULT (unixepoch()),
                PRIMARY KEY (kind, external_id)
            )
            """
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS id_map_number ON id_map(number)")
        self._db.commit()

    @classmethod
    def _base_number(cls, kind: str, external_id: str) -> int:
        digest = hashlib.blake2b(f"{kind}\0{external_id}".encode("utf-8"), digest_size=4).digest()
        value = int.from_bytes(digest, "big") & cls._MAX_ID
        return value or 1

    def get_or_create(self, kind: str, external_id: str) -> int:
        if not isinstance(kind, str) or not kind:
            raise ValueError("kind 必须是非空字符串")
        if not isinstance(external_id, str) or not external_id:
            raise ValueError("external_id 必须是非空字符串")
        if len(external_id) > 4096:
            raise ValueError("external_id 过长")
        with self._lock:
            row = self._db.execute(
                "SELECT number FROM id_map WHERE kind=? AND external_id=?",
                (kind, external_id),
            ).fetchone()
            if row:
                return int(row[0])
            candidate = self._base_number(kind, external_id)
            for _ in range(self._MAX_ID):
                exists = self._db.execute(
                    "SELECT 1 FROM id_map WHERE number=?", (candidate,)
                ).fetchone()
                if not exists:
                    try:
                        self._db.execute(
                            "INSERT INTO id_map(kind, external_id, number) VALUES(?,?,?)",
                            (kind, external_id, candidate),
                        )
                        self._db.commit()
                        return candidate
                    except sqlite3.IntegrityError:
                        self._db.rollback()
                        row = self._db.execute(
                            "SELECT number FROM id_map WHERE kind=? AND external_id=?",
                            (kind, external_id),
                        ).fetchone()
                        if row:
                            return int(row[0])
                candidate = candidate % self._MAX_ID + 1
            raise RuntimeError("OneBot 数字 ID 空间已耗尽")

    def get(self, kind: str, external_id: str) -> int | None:
        with self._lock:
            row = self._db.execute(
                "SELECT number FROM id_map WHERE kind=? AND external_id=?",
                (kind, external_id),
            ).fetchone()
            return int(row[0]) if row else None

    def reverse(self, kind: str, number: int) -> str | None:
        if type(number) is not int or not 1 <= number <= self._MAX_ID:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT external_id FROM id_map WHERE kind=? AND number=?",
                (kind, number),
            ).fetchone()
            return str(row[0]) if row else None

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "IdMapper":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
