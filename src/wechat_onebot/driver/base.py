from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
import asyncio
from pathlib import Path

from ..models import (
    DriverEvent,
    DriverStatus,
    FriendInfo,
    GroupInfo,
    GroupMemberInfo,
    LoginInfo,
)

EventCallback = Callable[[DriverEvent], Awaitable[None]]


class DriverError(RuntimeError):
    pass


class DriverUnsupported(DriverError):
    pass


class Driver(ABC):
    capabilities: frozenset[str] = frozenset()

    def take_native_message_ids(self) -> tuple[str, ...]:
        """Return and clear native message IDs produced by the last sends."""
        return ()

    async def validate_media(self, kind: str, path: str) -> None:
        """Preflight local input; native drivers additionally check their formats."""
        def validate():
            source = Path(path)
            if not source.is_file() or source.stat().st_size == 0:
                raise ValueError("媒体必须是非空本地文件")
        await asyncio.to_thread(validate)

    @property
    @abstractmethod
    def online(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def start(self, emit: EventCallback) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def send_text(self, chat_id: str, text: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def send_at_text(
        self,
        chat_id: str,
        text: str,
        user_ids: Sequence[str],
        display_names: Sequence[str] | None = None,
        *,
        labels_verified: bool = True,
    ) -> bool:
        raise NotImplementedError

    async def resolve_mention_label(self, chat_id: str, user_id: str) -> str | None:
        """Return the best native-cached display name, if the driver can read it."""
        return None

    @abstractmethod
    async def send_image(self, chat_id: str, path: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def send_file(self, chat_id: str, path: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def send_voice(self, chat_id: str, path: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def send_emoticon(self, chat_id: str, path: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def get_login_info(self) -> LoginInfo:
        raise NotImplementedError

    @abstractmethod
    async def get_friend_list(self) -> Sequence[FriendInfo]:
        raise NotImplementedError

    @abstractmethod
    async def get_group_list(self) -> Sequence[GroupInfo]:
        raise NotImplementedError

    @abstractmethod
    async def get_group_member_list(self, group_id: str) -> Sequence[GroupMemberInfo]:
        raise NotImplementedError

    @abstractmethod
    async def get_status(self) -> DriverStatus:
        raise NotImplementedError
