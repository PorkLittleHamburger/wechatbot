from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias


@dataclass(frozen=True)
class Segment:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IncomingMessage:
    external_chat_id: str
    external_sender_id: str
    segments: tuple[Segment, ...]
    external_message_id: str = ""
    sender_name: str = ""
    group_name: str = ""
    sender_role: Literal["owner", "admin", "member", "unknown"] = "unknown"
    is_group: bool = False
    is_self: bool = False
    sequence: int = 0
    timestamp: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NoticeEvent:
    notice_type: str
    external_chat_id: str = ""
    external_user_id: str = ""
    external_operator_id: str = ""
    timestamp: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RequestEvent:
    request_type: Literal["friend", "group"]
    external_user_id: str
    comment: str = ""
    flag: str = ""
    external_group_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


DriverEvent: TypeAlias = IncomingMessage | NoticeEvent | RequestEvent


@dataclass(frozen=True)
class LoginInfo:
    external_user_id: str
    nickname: str = ""


@dataclass(frozen=True)
class FriendInfo:
    external_user_id: str
    nickname: str = ""
    remark: str = ""


@dataclass(frozen=True)
class GroupInfo:
    external_group_id: str
    group_name: str = ""
    member_count: int = 0
    max_member_count: int = 0


@dataclass(frozen=True)
class GroupMemberInfo:
    external_group_id: str
    external_user_id: str
    nickname: str = ""
    card: str = ""
    role: str = "member"


@dataclass(frozen=True)
class DriverStatus:
    online: bool
    good: bool = True
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SendResult:
    ok: bool
    message: str = ""
