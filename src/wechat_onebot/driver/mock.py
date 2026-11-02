from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..models import (
    DriverStatus,
    FriendInfo,
    GroupInfo,
    GroupMemberInfo,
    IncomingMessage,
    LoginInfo,
    Segment,
)
from .base import Driver, EventCallback


@dataclass
class SendRecord:
    kind: str
    chat_id: str
    payload: dict[str, Any] = field(default_factory=dict)


class MockDriver(Driver):
    capabilities = frozenset({
        "text.receive", "text.send", "group.text.mention", "image.receive", "image.send",
        "file.receive", "file.send", "voice.send", "emoticon.send", "contact.read",
        "group.members.read", "request.friend",
    })

    def __init__(self):
        self._online = False
        self._emit: EventCallback | None = None
        self.sent: list[SendRecord] = []
        self.friends = (
            FriendInfo("wxid_alice", "Alice", "A"),
            FriendInfo("wxid_bob", "Bob", "B"),
        )
        self.groups = (
            GroupInfo("10001@chatroom", "测试群", 2, 500),
        )
        self.members = {
            "10001@chatroom": (
                GroupMemberInfo("10001@chatroom", "wxid_alice", "Alice", "Alice", "owner"),
                GroupMemberInfo("10001@chatroom", "wxid_bob", "Bob", "Bob", "member"),
            ),
        }

    @property
    def online(self) -> bool:
        return self._online

    async def start(self, emit: EventCallback) -> None:
        self._emit = emit
        self._online = True

    async def stop(self) -> None:
        self._online = False
        self._emit = None

    async def send_text(self, chat_id: str, text: str) -> bool:
        self.sent.append(SendRecord("text", chat_id, {"text": text}))
        return True

    async def send_at_text(self, chat_id: str, text: str, user_ids: Sequence[str], display_names: Sequence[str] | None = None) -> bool:
        self.sent.append(SendRecord("at_text", chat_id, {
            "text": text, "user_ids": list(user_ids), "display_names": list(display_names or ()),
        }))
        return True

    async def send_image(self, chat_id: str, path: str) -> bool:
        self.sent.append(SendRecord("image", chat_id, {"path": path}))
        return True

    async def send_file(self, chat_id: str, path: str) -> bool:
        self.sent.append(SendRecord("file", chat_id, {"path": path}))
        return True

    async def send_voice(self, chat_id: str, path: str) -> bool:
        self.sent.append(SendRecord("voice", chat_id, {"path": path}))
        return True

    async def send_emoticon(self, chat_id: str, path: str) -> bool:
        self.sent.append(SendRecord("emoticon", chat_id, {"path": path}))
        return True

    async def get_login_info(self) -> LoginInfo:
        return LoginInfo("wxid_robot", "Mock Robot")

    async def get_friend_list(self) -> Sequence[FriendInfo]:
        return self.friends

    async def get_group_list(self) -> Sequence[GroupInfo]:
        return self.groups

    async def get_group_member_list(self, group_id: str) -> Sequence[GroupMemberInfo]:
        return self.members.get(group_id, ())

    async def get_status(self) -> DriverStatus:
        return DriverStatus(online=self.online, good=self.online, message="mock", details={})

    async def inject_message(
        self,
        *,
        chat_id: str = "wxid_alice",
        sender_id: str = "wxid_alice",
        text: str = "hello",
        is_group: bool = False,
        sender_name: str = "Alice",
        message_id: str | None = None,
        timestamp: int | None = None,
    ) -> None:
        if self._emit is None:
            raise RuntimeError("mock driver 尚未启动")
        segments = (Segment("text", {"text": text}),)
        message = IncomingMessage(
            external_chat_id=chat_id,
            external_sender_id=sender_id,
            sender_name=sender_name,
            segments=segments,
            external_message_id=message_id or f"mock-{time.time_ns()}",
            is_group=is_group,
            timestamp=timestamp or int(time.time()),
            raw={"mock": True},
        )
        await self._emit(message)
