from __future__ import annotations

import base64
import binascii
import hashlib
import math
import mimetypes
import os
import re
import sqlite3
import uuid
from contextlib import closing
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

import httpx


class MediaError(ValueError):
    pass


@dataclass(frozen=True)
class MediaRecord:
    media_id: str
    path: Path
    name: str
    content_type: str


def _sniff_suffix(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"%PDF"):
        return ".pdf"
    if data.startswith(b"ID3") or data.startswith(b"\xff\xfb"):
        return ".mp3"
    if data.startswith(b"OggS"):
        return ".ogg"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return ".wav"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return ".mp4"
    return ".bin"


class MediaStore:
    def __init__(self, media_dir: Path, *, max_bytes: int, public_base_url: str):
        self.media_dir = Path(media_dir).resolve()
        self.max_bytes = max_bytes
        self.public_base_url = public_base_url.rstrip("/")
        self.media_dir.mkdir(parents=True, exist_ok=True)
        (self.media_dir / "cache").mkdir(parents=True, exist_ok=True)
        (self.media_dir / "inbound").mkdir(parents=True, exist_ok=True)
        # The caller supplies an account-specific directory. Connections are local
        # to each operation so worker threads and separate store instances share
        # SQLite's locking without sharing Python connection state.
        self._index_path = self.media_dir / "media.sqlite3"
        with closing(self._connect()) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS media_sources (
                    media_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    PRIMARY KEY (media_id, path, name)
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS media_names ON media_sources(name)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS media_downloads (
                    url TEXT PRIMARY KEY,
                    media_id TEXT NOT NULL
                )
            """)
            connection.execute("CREATE TABLE IF NOT EXISTS media_url_bases (url TEXT PRIMARY KEY)")
            connection.execute("INSERT OR IGNORE INTO media_url_bases VALUES (?)", (self.public_base_url,))

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._index_path, timeout=30)

    def _fingerprint(self, path: Path) -> str:
        """Bounded read; reject files that change while being registered/read."""
        try:
            before = path.stat()
            if not path.is_file():
                raise MediaError(f"媒体文件不存在: {path}")
            if before.st_size > self.max_bytes:
                raise MediaError("媒体文件超过大小限制")
            digest = hashlib.sha256()
            total = 0
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise MediaError("媒体文件超过大小限制")
                    digest.update(chunk)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns
            ) or total != after.st_size:
                raise MediaError(f"媒体文件在读取时发生变化: {path}")
            return digest.hexdigest()
        except OSError as error:
            raise MediaError(f"媒体文件不可用: {path}") from error

    def register(self, path: str | Path, *, name: str = "") -> MediaRecord:
        source = self._validate_path(Path(path))
        if source == self._index_path or str(source).startswith(str(self._index_path) + "-"):
            raise MediaError("不能注册媒体索引文件")
        digest = self._fingerprint(source)
        media_id = digest[:32]
        content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        record = MediaRecord(media_id, source, name or source.name, content_type)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO media_sources VALUES (?, ?, ?, ?, ?)",
                (media_id, str(source), record.name, content_type, digest),
            )
        return record

    def get(self, media_id: str) -> MediaRecord | None:
        rows = self._lookup("media_id", media_id)
        return self._available_record(rows)

    def _lookup(self, column: str, value: str) -> list[tuple]:
        # column is an internal constant, never media input.
        with closing(self._connect()) as connection:
            return connection.execute(
                f"SELECT media_id, path, name, content_type, digest FROM media_sources "
                f"WHERE {column} = ? ORDER BY rowid DESC", (value,)
            ).fetchall()

    def _available_record(self, rows: list[tuple]) -> MediaRecord | None:
        for media_id, path, name, content_type, digest in rows:
            try:
                source = self._validate_path(Path(path))
                if self._fingerprint(source) == digest:
                    return MediaRecord(media_id, source, name, content_type)
            except MediaError:
                continue
        return None

    def _resolve_registered(self, value: str) -> Path | None:
        rows = self._lookup("media_id", value)
        if not rows:
            rows = self._lookup("name", value)
        if not rows:
            return None
        # Old entries remain to keep their public IDs from pointing at changed
        # content. Only usable sources participate in filename ambiguity.
        available = []
        for row in rows:
            record = self._available_record([row])
            if record is not None:
                available.append(record)
        if len({record.media_id for record in available}) > 1:
            raise MediaError(f"媒体文件名不唯一 (ambiguous): {value}；请使用 media ID 或完整路径")
        if not available:
            raise MediaError(f"已注册媒体不存在、已变化或超过大小限制: {value}")
        return available[0].path

    def url_for(self, record: MediaRecord) -> str:
        # Remember issued account routes across listener/base changes. This is
        # account-local evidence, never inferred from a URL supplied for refresh.
        with closing(self._connect()) as connection, connection:
            connection.execute("INSERT OR IGNORE INTO media_url_bases VALUES (?)", (self.public_base_url,))
        encoded_name = quote(record.name, safe="")
        return f"{self.public_base_url}/media/{record.media_id}/{encoded_name}"

    def _record_for_url(self, value: Any) -> MediaRecord | None:
        """Resolve an old gateway URL locally, never by fetching its host."""
        if not isinstance(value, str):
            return None
        try:
            parsed = urlparse(value)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                return None
            match = re.fullmatch(r"(.*)/media/([0-9a-f]{32})/([^/]+)", parsed.path)
            if match is None:
                return None
            prefix, media_id, encoded_name = match.groups()
            current_prefix = urlparse(self.public_base_url).path.rstrip("/")
            # Legacy listeners used /media directly. Account routes must belong
            # to the current account, even when both accounts index equal bytes.
            if prefix and prefix != current_prefix:
                with closing(self._connect()) as connection:
                    bases = connection.execute("SELECT url FROM media_url_bases").fetchall()
                if not any(urlparse(base).path.rstrip("/") == prefix for (base,) in bases):
                    return None
            name = unquote(encoded_name)
            if not name or name in (".", "..") or any(c in name for c in ("/", "\\", "\x00")):
                return None
        except ValueError:
            return None

        rows = self._lookup("media_id", media_id)
        # A matching content ID alone must not rewrite an unrelated external
        # link with a different filename. Include stale aliases in this check.
        if rows and not any(row[2] == name for row in rows):
            return None
        record = self._available_record([row for row in rows if row[4][:32] == media_id])
        if record is not None:
            return record

        # Before persistent indexing, URLs held the original received filename.
        # Also recover moved indexed sources, but only under this account's root.
        # Do not use resolve_input: that can download URLs or accept other paths.
        names = {name, *(Path(row[1]).name for row in rows)}
        try:
            for candidate in self.media_dir.rglob("*"):
                if candidate.name not in names:
                    continue
                try:
                    source = self._validate_path(candidate)
                    if not source.is_relative_to(self.media_dir):
                        continue
                    if source == self._index_path or str(source).startswith(str(self._index_path) + "-"):
                        continue
                    if self._fingerprint(source)[:32] != media_id:
                        continue
                    recovered = self.register(source, name=name)
                    if recovered.media_id == media_id:
                        return recovered
                except MediaError:
                    continue
        except OSError:
            pass
        return None

    def refresh_payload(self, payload: Any) -> Any:
        """Copy a persisted payload and refresh validated account media links.

        Supports message segments, standard forward node.content, compatibility
        messages[].content and records message/children trees. Missing resources
        and explicit unavailable placeholders stay unchanged. The service must
        regenerate raw_message from the refreshed message before returning it.
        """
        result = deepcopy(payload)
        resolved: dict[str, MediaRecord | None] = {}

        def resolve(value: Any) -> MediaRecord | None:
            if not isinstance(value, str):
                return None
            if value not in resolved:
                resolved[value] = self._record_for_url(value)
            return resolved[value]

        def walk(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    walk(item)
            elif isinstance(value, dict):
                kind, data = value.get("type"), value.get("data")
                if isinstance(data, dict):
                    if kind in ("image", "record", "video", "file"):
                        record = resolve(data.get("url"))
                        if record is not None:
                            data["url"] = self.url_for(record)
                            data["file"] = record.media_id
                    elif kind == "text" and isinstance(data.get("text"), str):
                        # Only the converter's file-download projection is text
                        # media; ordinary prose and external links remain intact.
                        lines = data["text"].split("\n")
                        if len(lines) == 2 and lines[0].startswith("[文件] "):
                            record = resolve(lines[1])
                            if record is not None:
                                data["text"] = lines[0] + "\n" + self.url_for(record)
                for key in ("message", "messages", "records", "children", "content"):
                    if key in value:
                        walk(value[key])
                if kind in ("node", "forward") and isinstance(data, dict):
                    walk(data)

        walk(result)
        return result

    async def resolve_input(
        self, value: str, *, cache: bool = True, proxy: bool = True,
        timeout: float | None = None,
    ) -> Path:
        """Resolve media; cache/proxy/timeout control HTTP(S) downloads only."""
        if not isinstance(value, str) or not value.strip():
            raise MediaError("媒体 file 字段不能为空")
        if not isinstance(cache, bool) or not isinstance(proxy, bool):
            raise MediaError("媒体 cache/proxy 必须为布尔值")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0
        ):
            raise MediaError("媒体 timeout 必须为大于 0 的有限秒数")
        value = value.strip()
        if value.startswith("base64://"):
            return await self._resolve_base64(value[9:])
        # urlparse treats a raw Windows drive letter as a URL scheme.
        if os.name == "nt" and (Path(value).is_absolute() or Path(value).drive):
            return self._validate_path(Path(value))
        parsed = urlparse(value)
        if parsed.scheme in ("http", "https"):
            return await self._download(value, cache=cache, proxy=proxy, timeout=timeout)
        if parsed.scheme == "file":
            path = Path(unquote(parsed.path))
            if os.name == "nt" and parsed.netloc:
                path = Path(f"//{parsed.netloc}/{unquote(parsed.path.lstrip('/'))}")
            elif os.name == "nt":
                path = Path(unquote(parsed.path.lstrip('/')))
        elif parsed.scheme:
            raise MediaError(f"不支持的媒体地址协议: {parsed.scheme}")
        else:
            registered = self._resolve_registered(value)
            if registered is None and Path(value).name == value:
                # Older versions had no index. Recover account-local received
                # filenames lazily; external paths require registration again.
                for candidate in self.media_dir.rglob("*"):
                    if candidate.name == value and candidate.is_file() and candidate != self._index_path:
                        self.register(candidate)
                registered = self._resolve_registered(value)
            if registered is not None:
                return registered
            path = Path(value)
            if not path.is_absolute():
                path = self.media_dir / path
        return self._validate_path(path)

    def _validate_path(self, path: Path) -> Path:
        try:
            resolved = path.resolve()
            if not resolved.is_file():
                raise MediaError(f"媒体文件不存在: {resolved}")
            if resolved.stat().st_size > self.max_bytes:
                raise MediaError("媒体文件超过大小限制")
            return resolved
        except MediaError:
            raise
        except (OSError, ValueError) as error:
            raise MediaError(f"媒体文件不可用: {path}") from error

    async def _resolve_base64(self, encoded: str) -> Path:
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise MediaError("base64 媒体数据无效") from error
        if len(data) > self.max_bytes:
            raise MediaError("base64 媒体数据超过大小限制")
        suffix = _sniff_suffix(data)
        target = self.media_dir / "cache" / f"{uuid.uuid4().hex}{suffix}"
        target.write_bytes(data)
        return target

    async def _download(
        self, url: str, *, cache: bool = True, proxy: bool = True,
        timeout: float | None = None,
    ) -> Path:
        if cache:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT media_id FROM media_downloads WHERE url = ?", (url,)
                ).fetchone()
            if row is not None:
                record = self.get(row[0])
                if record is not None:
                    return record.path
        limit = self.max_bytes
        chunks: list[bytes] = []
        total = 0
        headers = {"User-Agent": "wechat-onebot/0.1"}
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=30.0 if timeout is None else timeout, trust_env=proxy,
            ) as client:
                async with client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > limit:
                            raise MediaError("远程媒体超过大小限制")
                        chunks.append(chunk)
        except httpx.HTTPError as error:
            raise MediaError(f"远程媒体下载失败: {type(error).__name__}") from error
        data = b"".join(chunks)
        suffix = _sniff_suffix(data)
        if suffix == ".bin":
            guessed = mimetypes.guess_extension(response.headers.get("content-type", "").split(";", 1)[0].strip() or "")
            if guessed:
                suffix = guessed
        target = self.media_dir / "cache" / f"{uuid.uuid4().hex}{suffix}"
        target.write_bytes(data)
        if cache:
            record = self.register(target)
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT OR REPLACE INTO media_downloads VALUES (?, ?)", (url, record.media_id)
                )
        return target
