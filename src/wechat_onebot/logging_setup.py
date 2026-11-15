"""Gateway file logging with bounded rotation and runtime level switching.

Security boundary: the WebUI in-memory log (``webui.server._SafeRuntimeLogs``)
keeps only whitelisted summaries and never formats ``record.args``. File logging
reuses the same whitelist (``SAFE_MESSAGES``) and never writes ``record.msg`` or
``record.args`` for other records, so secrets, chat content and media payloads
cannot reach disk just because a caller logged them as arguments. Whitelisted
summaries still go through regex redaction as defense in depth, and exceptions
are stored as their type only instead of a full traceback. Only
``wechat-onebot.*`` loggers are captured so third-party output (uvicorn, httpx)
cannot fill the file.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .config import AppConfig, LOG_LEVELS

LOGGER_NAME = "wechat-onebot"
LOG_FILE_NAME = "gateway.log"
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
MAX_MESSAGE_CHARS = 4096
TAIL_READ_BYTES = 256 * 1024

# Message templates that are safe to render without their formatting arguments.
# The in-memory WebUI log and the file handler share this single mapping; adding
# an entry means the summary is safe to persist and to show in the management UI.
SAFE_MESSAGES: dict[str, str] = {
    "反向 WebSocket 已连接: %s": "反向 WebSocket 已连接",
    "反向 WebSocket 连接失败 %s: %s；%.1fs 后重试": "反向 WebSocket 连接失败，等待重试",
    "HTTP 上报队列已满，丢弃事件: %s": "HTTP 上报队列已满",
    "HTTP 上报失败 %s: %s": "HTTP 上报失败",
    "OneBot 动作失败: %s": "OneBot 动作失败",
    "网关文件日志已启用: %s（级别 %s）": "网关文件日志已启用",
    "文件日志未启用；仅输出到 stderr": "文件日志未启用",
    "日志级别已由 WebUI 切换: %s -> %s": "日志级别已由 WebUI 切换",
    "检测到不安全的 OneBot 入站端点配置: %s": "检测到不安全的 OneBot 入站端点配置",
}

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 <redacted>"),
    (re.compile(
        r"(?i)\b(access_token|refresh_token|token|password|passwd|secret|"
        r"api[_-]?key|authorization)\b(\s*[:=]\s*)[^\s,;&]+"),
     r"\1\2<redacted>"),
    (re.compile(r"(?i)([?&](?:access_token|token|password|secret)=)[^&\s]+"),
     r"\1<redacted>"),
    (re.compile(r"base64://[A-Za-z0-9+/=]{8,}"), "base64://<redacted>"),
)

_file_handler: RotatingFileHandler | None = None


class _GatewayOnlyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name == LOGGER_NAME or record.name.startswith(LOGGER_NAME + ".")


def redact(text: str) -> str:
    """Remove credentials and inline media payloads from one log line."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[:MAX_MESSAGE_CHARS] + "…<truncated>"
    return text


def safe_message(record: logging.LogRecord) -> str:
    """Return the persistable summary for a record, never record.msg/args."""
    try:
        mapped = SAFE_MESSAGES.get(record.msg)
    except TypeError:  # Unhashable msg object from an unusual caller.
        mapped = None
    if mapped is not None:
        return mapped
    return "运行异常" if record.levelno >= logging.ERROR else "运行事件"


class RedactingFormatter(logging.Formatter):
    """Render a record without leaking raw arguments, secrets or tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        safe = logging.LogRecord(
            record.name, record.levelno, record.pathname, record.lineno,
            redact(safe_message(record)), None, None,
        )
        safe.created = record.created
        safe.msecs = record.msecs
        safe.relativeCreated = record.relativeCreated
        rendered = super().format(safe)
        if record.exc_info is not None and record.exc_info[1] is not None:
            rendered += f"\n{type(record.exc_info[1]).__name__}"
        return rendered


def normalize_level(level: str) -> str:
    value = str(level).strip().upper()
    if value not in LOG_LEVELS:
        raise ValueError("日志级别必须是 " + "/".join(LOG_LEVELS) + " 之一")
    return value


def log_directory(config: AppConfig) -> Path:
    return config.logging.directory or (config.storage.data_dir / "logs")


def log_file_path(config: AppConfig) -> Path:
    return log_directory(config) / LOG_FILE_NAME


def current_level(config: AppConfig | None = None) -> str:
    """Return the effective gateway level, falling back to configured default."""
    level = logging.getLogger(LOGGER_NAME).level
    if level == logging.NOTSET:
        return normalize_level(config.logging.level) if config is not None else "INFO"
    return logging.getLevelName(level)


def set_log_level(level: str) -> str:
    """Hot-switch only the gateway logger; runtime only, never persisted."""
    canonical = normalize_level(level)
    logging.getLogger(LOGGER_NAME).setLevel(getattr(logging, canonical))
    return canonical


def _close_file_handler() -> None:
    global _file_handler
    handler, _file_handler = _file_handler, None
    if handler is None:
        return
    try:
        logging.getLogger(LOGGER_NAME).removeHandler(handler)
    finally:
        handler.close()


def configure_logging(config: AppConfig, level: str | None = None) -> dict[str, Any]:
    """Install the stderr logger and (when enabled) the rotating file handler.

    The explicit ``level`` argument wins over ``config.logging.level``. A
    missing/unwritable log directory warns on stderr and never blocks startup.
    """
    effective = normalize_level(level or config.logging.level)
    logging.basicConfig(level=getattr(logging, effective), format=LOG_FORMAT)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, effective))
    _close_file_handler()
    result: dict[str, Any] = {"level": effective, "file": None, "enabled": False}
    if not config.logging.file:
        return result
    path = log_file_path(config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=config.logging.max_bytes,
            backupCount=config.logging.backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(RedactingFormatter(LOG_FORMAT))
        handler.setLevel(logging.DEBUG)
        handler.addFilter(_GatewayOnlyFilter())
        logger.addHandler(handler)
    except OSError as error:
        print(f"警告: 无法启用文件日志 {path}: {error}", file=sys.stderr)
        return result
    global _file_handler
    _file_handler = handler
    result.update(file=str(path), enabled=True)
    return result


def webui_log_file(config: AppConfig) -> Path | None:
    """Return the active log file for the authenticated WebUI download route.

    The HTTP layer never accepts a path from the client. The resolved target
    must stay inside the configured log directory so a symlink cannot turn the
    download route into an arbitrary-file reader.
    """
    base = log_directory(config).resolve()
    try:
        resolved = log_file_path(config).resolve()
    except OSError:
        return None
    if resolved != base / LOG_FILE_NAME and not resolved.is_relative_to(base):
        return None
    return resolved if resolved.is_file() else None


def tail_log_file(path: Path, lines: int) -> tuple[list[str], bool]:
    """Read at most ``TAIL_READ_BYTES`` from the end and return the last lines."""
    if lines <= 0:
        raise ValueError("tail 必须是正整数")
    if not path.is_file():
        return [], False
    with path.open("rb") as source:
        source.seek(0, os.SEEK_END)
        size = source.tell()
        start = max(0, size - TAIL_READ_BYTES)
        source.seek(start)
        data = source.read()
    text = data.decode("utf-8", errors="replace")
    rows = text.splitlines()
    if start > 0 and rows:
        rows = rows[1:]  # Drop the partial first line of a bounded tail read.
    truncated = start > 0 or len(rows) > lines
    return rows[-lines:], truncated


def log_status(config: AppConfig) -> dict[str, Any]:
    path = log_file_path(config)
    return {
        "level": current_level(config),
        "levels": list(LOG_LEVELS),
        "file": str(path),
        "enabled": config.logging.file,
        "exists": path.is_file(),
        "maxBytes": config.logging.max_bytes,
        "backupCount": config.logging.backup_count,
    }
