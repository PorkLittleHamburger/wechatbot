from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WeChatLogin:
    pid: int
    wxid: str
    nickname: str = ""
    path: str = ""
    version: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
