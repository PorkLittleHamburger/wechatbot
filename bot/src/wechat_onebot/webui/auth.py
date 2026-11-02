from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from pathlib import Path

from ..config import ConfigError, _atomic_write_json


PASSWORD_ITERATIONS = 310_000


class PasswordStore:
    def __init__(self, path: Path):
        self.path = path
        self._record: dict | None = None
        if path.is_file():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if (not isinstance(record, dict) or record.get("iterations") != PASSWORD_ITERATIONS
                        or len(base64.b64decode(record["salt"], validate=True)) != 16
                        or len(base64.b64decode(record["digest"], validate=True)) != 32):
                    raise ValueError("invalid password record")
            except (OSError, ValueError, KeyError, TypeError, binascii.Error) as error:
                raise ConfigError(f"WebUI password file is invalid: {path}") from error
            self._record = record

    @property
    def configured(self) -> bool:
        return self._record is not None

    def verify(self, password: str) -> bool:
        if self._record is None:
            return False
        salt = base64.b64decode(self._record["salt"])
        expected = base64.b64decode(self._record["digest"])
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
        return hmac.compare_digest(actual, expected)

    def set(self, password: str) -> None:
        if not 8 <= len(password) <= 1024:
            raise ValueError("密码长度须为 8–1024 个字符")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
        record = {
            "iterations": PASSWORD_ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
            "digest": base64.b64encode(digest).decode("ascii"),
        }
        _atomic_write_json(self.path, record)
        self._record = record
