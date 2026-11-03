"""Local path spelling checks shared by file and voice handling."""
from __future__ import annotations

import re


def local_spelling(value: str) -> str:
    """Return a drive path for validation, preserving the caller's I/O spelling."""
    if re.match(r"^\\\\\?\\[A-Za-z]:\\", value):
        value = value[4:]
    # All other UNC/device namespaces remain unsupported, including mixed slashes.
    if value.replace("/", "\\").startswith("\\\\") or "://" in value:
        raise ValueError("文件路径必须是本地路径，不支持网络路径或 URL")
    return value
