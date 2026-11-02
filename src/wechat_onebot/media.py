from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import os
import uuid
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
        self.media_dir = media_dir
        self.max_bytes = max_bytes
        self.public_base_url = public_base_url.rstrip("/")
        self._records: dict[str, MediaRecord] = {}
        self.media_dir.mkdir(parents=True, exist_ok=True)
        (self.media_dir / "cache").mkdir(parents=True, exist_ok=True)
        (self.media_dir / "inbound").mkdir(parents=True, exist_ok=True)

    def register(self, path: str | Path, *, name: str = "") -> MediaRecord:
        source = Path(path).resolve()
        if not source.is_file():
            raise MediaError(f"媒体文件不存在: {source}")
        size = source.stat().st_size
        if size > self.max_bytes:
            raise MediaError(f"媒体文件超过限制: {size} > {self.max_bytes}")
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        media_id = digest.hexdigest()[:32]
        content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        record = MediaRecord(media_id, source, name or source.name, content_type)
        self._records[media_id] = record
        return record

    def get(self, media_id: str) -> MediaRecord | None:
        return self._records.get(media_id)

    def url_for(self, record: MediaRecord) -> str:
        encoded_name = quote(record.name, safe="")
        return f"{self.public_base_url}/media/{record.media_id}/{encoded_name}"

    async def resolve_input(self, value: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise MediaError("媒体 file 字段不能为空")
        value = value.strip()
        if value.startswith("base64://"):
            return await self._resolve_base64(value[9:])
        parsed = urlparse(value)
        if parsed.scheme in ("http", "https"):
            return await self._download(value)
        if parsed.scheme == "file":
            path = Path(unquote(parsed.path.lstrip('/')))
            if os.name == "nt" and parsed.netloc:
                path = Path(f"//{parsed.netloc}/{unquote(parsed.path.lstrip('/'))}")
        elif parsed.scheme:
            raise MediaError(f"不支持的媒体地址协议: {parsed.scheme}")
        else:
            path = Path(value)
            if not path.is_absolute():
                path = self.media_dir / path
        return self._validate_path(path)

    def _validate_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_file():
            raise MediaError(f"媒体文件不存在: {resolved}")
        if resolved.stat().st_size > self.max_bytes:
            raise MediaError("媒体文件超过大小限制")
        return resolved

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

    async def _download(self, url: str) -> Path:
        limit = self.max_bytes
        chunks: list[bytes] = []
        total = 0
        headers = {"User-Agent": "wechat-onebot/0.1"}
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > limit:
                        raise MediaError("远程媒体超过大小限制")
                    chunks.append(chunk)
        data = b"".join(chunks)
        suffix = _sniff_suffix(data)
        if suffix == ".bin":
            guessed = mimetypes.guess_extension(response.headers.get("content-type", "").split(";", 1)[0].strip() or "")
            if guessed:
                suffix = guessed
        target = self.media_dir / "cache" / f"{uuid.uuid4().hex}{suffix}"
        target.write_bytes(data)
        return target
