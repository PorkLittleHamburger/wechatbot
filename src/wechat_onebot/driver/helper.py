from __future__ import annotations

import asyncio
import ctypes
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..config import AppConfig, account_directories
from ..models import (
    DriverStatus,
    FriendInfo,
    GroupInfo,
    GroupMemberInfo,
    IncomingMessage,
    LoginInfo,
    NoticeEvent,
    RequestEvent,
    Segment,
)
from .base import Driver, DriverError, EventCallback


def _query_process_executable(pid: int) -> Path | None:
    """Return a process image path without requiring psutil or process elevation."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)
    ]
    kernel.QueryFullProcessImageNameW.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, 0, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.c_uint32(len(buffer))
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            return None
        return Path(buffer.value)
    finally:
        kernel.CloseHandle(handle)


def _detect_wechat_data_directory(pid: int | None, account: str) -> Path | None:
    """Find the active WeChat 4.x account directory using bounded known roots."""
    if not pid or not account:
        return None
    roots: list[Path] = []
    executable = _query_process_executable(pid)
    if executable is not None and len(executable.parents) >= 2:
        roots.append(executable.parents[1] / "Documents" / "xwechat_files")
    home = Path.home()
    roots.extend((home / "Documents" / "xwechat_files", home / "Documents" / "WeChat Files"))
    candidates: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        entries: list[Path] = []
        if root.name == account or root.name.startswith(account + "_"):
            entries.append(root)
        elif root.is_dir():
            entries.extend(child for child in root.iterdir()
                           if child.name == account or child.name.startswith(account + "_"))
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir():
                continue
            try:
                resolved = entry.resolve(strict=True)
            except OSError:
                continue
            temp = resolved / "temp"
            if not temp.is_dir() or temp.is_symlink() or resolved in seen:
                continue
            seen.add(resolved)
            candidates.append(resolved)
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item / "temp").stat().st_mtime_ns)


class HelperDriver(Driver):
    """Adapter for the vendored helper native Windows bridge.

    The helper service performs native DLL calls. This project does not use OCR,
    screenshots, mouse clicks, or keyboard simulation for sending messages.
    """

    capabilities = frozenset({
        "text.receive", "text.send", "group.text.mention", "image.receive", "image.send",
        "file.receive", "file.send", "voice.send", "emoticon.send", "contact.read",
        "group.members.read", "request.friend", "message.recall",
    })

    def __init__(self, app_config: AppConfig):
        self.config = app_config
        self._online = False
        self._backend: Any = None
        self._service: Any = None
        self._emit: EventCallback | None = None
        self._loop = None
        self._self_id = app_config.driver.wechat_account
        self._wechat_data_directory: Path | None = app_config.driver.wechat_data_directory

    def rebind(self, app_config: AppConfig) -> None:
        if self._online:
            raise DriverError("运行中的微信驱动不能重新绑定 PID")
        self.config = app_config
        self._self_id = app_config.driver.wechat_account
        self._wechat_data_directory = app_config.driver.wechat_data_directory

    @property
    def online(self) -> bool:
        return self._online

    def _helper_config(self) -> dict[str, Any]:
        root = self.config.storage.data_dir
        _account_dir, media = account_directories(self.config, self.config.driver.wechat_account)
        return {
            "backend": "native_text",
            "wechat_account": self.config.driver.wechat_account,
            "wechat_pid": self.config.driver.wechat_pid,
            "chat_allowlist": [],
            "image_directory": str(media / "images"),
            "file_directory": str(media / "files"),
            "emoticon_directory": str(media / "emoticons"),
            "wechat_data_directory": (str(self._wechat_data_directory)
                                      if self._wechat_data_directory is not None else None),
            "reconnect_seconds": 5,
            "plugin_queue_size": 128,
            "console": False,
            "log_level": "INFO",
            "llm_config_file": str(root / "helper" / "llm_config.json"),
            "tts_config_file": str(root / "helper" / "tts_config.json"),
            "members_config_file": None,
            "text_send_delay": {
                "enabled": True,
                "characters_per_group": 10,
                "min_seconds": 0.1,
                "max_seconds": 0.3,
            },
            "plugins": {},
        }

    async def start(self, emit: EventCallback) -> None:
        import asyncio
        import sys

        if sys.platform != "win32":
            raise DriverError("helper 原生驱动只支持 Windows")
        if not self.config.driver.wechat_account:
            raise DriverError("尚未选择微信账号，请在 WebUI 的进程与账号页面选择")
        helper_root = self.config.driver.helper_root
        if not (helper_root / "native").is_dir():
            raise DriverError(f"未找到 helper 原生目录: {helper_root / 'native'}")
        if str(helper_root) not in sys.path:
            sys.path.insert(0, str(helper_root))

        from wxbot_framework.backend import NativeTextBackend
        from wxbot_framework.sdk import ServiceFacade

        self._loop = asyncio.get_running_loop()
        self._emit = emit
        self._self_id = self.config.driver.wechat_account
        if self._wechat_data_directory is None:
            self._wechat_data_directory = _detect_wechat_data_directory(
                self.config.driver.wechat_pid, self.config.driver.wechat_account
            )
        try:
            self._backend = NativeTextBackend(
                helper_root,
                self._helper_config(),
                data_root=self.config.storage.data_dir,
            )
            self._service = ServiceFacade(text_delay=self._helper_config()["text_send_delay"])
            self._service.bind(self._backend)
            await asyncio.to_thread(self._start_raw_backend)
            self._online = True
        except Exception as error:
            self._online = False
            self._service = None
            self._backend = None
            raise DriverError(f"helper 启动失败: {error}") from error

    def _start_raw_backend(self) -> None:
        raw = self._backend.raw
        raw.monitor_handler = lambda data: self._on_raw_data(11046, data)
        raw.register_msg_handler(lambda _client, protocol, data, _service: self._on_raw_data(protocol, data))
        raw.start()

    def _on_raw_data(self, protocol: int, data: Any) -> None:
        try:
            event = self._convert_raw_data(data)
        except Exception:
            import logging
            logging.getLogger("wechat-onebot.helper").exception(
                "原始微信消息转换失败 type=%r keys=%r",
                data.get("wx_type") if isinstance(data, dict) else None,
                sorted(data.keys()) if isinstance(data, dict) else type(data).__name__,
            )
            return
        if event is not None:
            self._dispatch_event(event)

    def _on_raw_event(self, message: Any) -> None:
        event = self._convert_message(message)
        if event is not None:
            self._dispatch_event(event)

    def _dispatch_event(self, event: Any) -> None:
        if self._loop is None or self._emit is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._emit(event), self._loop)
        future.add_done_callback(self._log_emit_error)

    @staticmethod
    def _log_emit_error(future) -> None:
        try:
            future.result()
        except Exception:
            import logging
            logging.getLogger("wechat-onebot.helper").exception("事件分发失败")

    @staticmethod
    def _raw_timestamp(data: dict[str, Any]) -> int:
        import time
        for key in ("create_time", "createTime", "timestamp"):
            value = data.get(key)
            if type(value) is int and value > 0:
                return value
        return int(time.time())

    def _convert_raw_data(self, raw: Any) -> IncomingMessage | NoticeEvent | RequestEvent | None:
        data = dict(raw) if isinstance(raw, dict) else {}
        try:
            msg_type = int(data.get("wx_type", data.get("type", 1)))
        except (TypeError, ValueError):
            msg_type = 1
        # Image and app-message monitors are provisional. The media receiver emits
        # the same identity again with ready/failed metadata; suppressing this copy
        # avoids duplicate placeholder events and false text-only content.
        if data.get("monitor_only") and msg_type in (3, 49):
            return None
        if msg_type == 37:
            request = data.get("friend_request") or {}
            return RequestEvent(
                request_type="friend",
                external_user_id=str(request.get("sender_id") or request.get("from_wxid") or data.get("sender_id") or ""),
                comment=str(request.get("verification") or request.get("content") or ""),
                flag=str(request.get("public_id") or request.get("id") or ""),
                raw=data,
            )
        if msg_type in (10000, 10002):
            recall = data.get("recall")
            if isinstance(recall, dict):
                chat = str(recall.get("chat_id") or data.get("chat_id") or "")
                return NoticeEvent(
                    notice_type="group_recall" if chat.endswith("@chatroom") else "friend_recall",
                    external_chat_id=chat,
                    external_user_id=str(recall.get("sender_id") or ""),
                    external_operator_id=str(recall.get("operator_id") or ""),
                    timestamp=self._raw_timestamp(data),
                    raw=data,
                )
            group_event = data.get("group_event")
            if isinstance(group_event, dict):
                kind = group_event.get("kind")
                notice_type = "group_increase" if kind == "join" else "group_decrease" if kind == "leave" else ""
                if notice_type:
                    return NoticeEvent(
                        notice_type=notice_type,
                        external_chat_id=str(group_event.get("room_id") or data.get("chat_id") or ""),
                        external_user_id=str(group_event.get("member_id") or ""),
                        timestamp=self._raw_timestamp(data),
                        raw=data,
                    )
            return None

        segments: list[Segment] = []
        at_members = tuple(str(item) for item in data.get("at_members", ()) if item)
        for member in at_members:
            segments.append(Segment("at", {"qq": member}))
        text = str(data.get("msg") or data.get("content") or "") if msg_type == 1 else ""
        if at_members and text:
            try:
                from wxbot_framework.mentions import strip_at_labels
                text = strip_at_labels(text)
            except Exception:
                pass
        if text:
            segments.append(Segment("text", {"text": text}))
        if msg_type == 3:
            path = data.get("image_path") or ""
            segments.append(Segment("image", {"file": str(path)}) if path else Segment("text", {"text": "[图片]"}))
        elif msg_type == 49:
            path = data.get("file_path") or ""
            segments.append(Segment("file", {"file": str(path), "name": data.get("file_name", "")}) if path else Segment("text", {"text": "[文件或卡片]"}))
        elif msg_type == 34:
            segments.append(Segment("text", {"text": "[语音]"}))
        elif msg_type == 43:
            segments.append(Segment("text", {"text": "[视频]"}))
        elif msg_type == 47:
            segments.append(Segment("text", {"text": "[表情]"}))
        if not segments:
            return None
        chat_id = str(data.get("chat_id") or data.get("room_wxid") or data.get("from_wxid") or data.get("sender_id") or "")
        sender_id = str(data.get("sender_id") or data.get("native_sender") or data.get("from_wxid") or "")
        is_group = bool(data.get("room_wxid")) or chat_id.endswith("@chatroom")
        return IncomingMessage(
            external_chat_id=chat_id,
            external_sender_id=sender_id,
            sender_name=str(data.get("sender_wechat_name") or data.get("sender_name") or ""),
            group_name=str(data.get("group_name") or data.get("room_name") or ""),
            sender_role=data.get("sender_role") if data.get("sender_role") in ("owner", "admin", "member") else "unknown",
            segments=tuple(segments),
            external_message_id=str(data.get("native_message_id") or data.get("sequence") or ""),
            is_group=is_group,
            is_self=bool(data.get("is_self")),
            sequence=int(data.get("sequence") or 0),
            timestamp=self._raw_timestamp(data),
            raw=data,
        )

    def _convert_message(self, message: Any) -> IncomingMessage | NoticeEvent | RequestEvent | None:
        data = dict(message.data)
        msg_type = int(message.type)
        if msg_type == 37:
            request = data.get("friend_request") or {}
            return RequestEvent(
                request_type="friend",
                external_user_id=str(request.get("sender_id") or message.sender_id or ""),
                comment=str(request.get("verification") or ""),
                flag=str(request.get("public_id") or request.get("id") or ""),
                raw=data,
            )
        if msg_type in (10000, 10002):
            recall = data.get("recall")
            if isinstance(recall, dict):
                chat = str(recall.get("chat_id") or message.chat_id or "")
                return NoticeEvent(
                    notice_type="group_recall" if chat.endswith("@chatroom") else "friend_recall",
                    external_chat_id=chat,
                    external_user_id=str(recall.get("sender_id") or ""),
                    external_operator_id=str(recall.get("operator_id") or ""),
                    timestamp=self._timestamp(data),
                    raw=data,
                )
            group_event = data.get("group_event")
            if isinstance(group_event, dict):
                kind = group_event.get("kind")
                notice_type = "group_increase" if kind == "join" else "group_decrease" if kind == "leave" else ""
                if notice_type:
                    return NoticeEvent(
                        notice_type=notice_type,
                        external_chat_id=str(group_event.get("room_id") or message.chat_id),
                        external_user_id=str(group_event.get("member_id") or ""),
                        external_operator_id="",
                        timestamp=self._timestamp(data),
                        raw=data,
                    )
            return None

        segments: list[Segment] = []
        at_members = tuple(str(item) for item in data.get("at_members", ()) if item)
        for member in at_members:
            segments.append(Segment("at", {"qq": member}))
        text = message.text or ""
        if at_members and text:
            try:
                from wxbot_framework.mentions import strip_at_labels
                text = strip_at_labels(text)
            except Exception:
                pass
        if text:
            segments.append(Segment("text", {"text": text}))
        if msg_type == 3:
            path = message.image_path
            segments.append(Segment("image", {"file": str(path)}) if path else Segment("text", {"text": "[图片]"}))
        elif msg_type == 49:
            path = message.file_path
            segments.append(Segment("file", {"file": str(path), "name": data.get("file_name", "")}) if path else Segment("text", {"text": "[文件或卡片]"}))
        elif msg_type == 34:
            segments.append(Segment("text", {"text": "[语音]"}))
        elif msg_type == 43:
            segments.append(Segment("text", {"text": "[视频]"}))
        elif msg_type == 47:
            segments.append(Segment("text", {"text": "[表情]"}))
        if not segments:
            return None
        return IncomingMessage(
            external_chat_id=str(message.chat_id),
            external_sender_id=str(message.sender_id or ""),
            sender_name=str(data.get("sender_wechat_name") or ""),
            group_name=str(data.get("group_name") or data.get("room_name") or ""),
            sender_role=data.get("sender_role") if data.get("sender_role") in ("owner", "admin", "member") else "unknown",
            segments=tuple(segments),
            external_message_id=str(data.get("native_message_id") or message.sequence or ""),
            is_group=bool(message.is_group),
            is_self=bool(message.is_self),
            sequence=int(message.sequence or 0),
            timestamp=self._timestamp(data),
            raw=data,
        )

    @staticmethod
    def _timestamp(data: dict[str, Any]) -> int:
        import time
        for key in ("create_time", "createTime", "timestamp"):
            value = data.get(key)
            if type(value) is int and value > 0:
                return value
        return int(time.time())

    async def stop(self) -> None:
        import asyncio
        self._online = False
        if self._service is not None:
            await asyncio.to_thread(self._service.close)
        self._service = None
        self._backend = None
        self._emit = None

    async def send_text(self, chat_id: str, text: str) -> bool:
        import asyncio
        self._require_service()
        try:
            result = await asyncio.to_thread(self._service.send_text, chat_id, text)
            return result is not False
        except Exception as error:
            raise DriverError(f"文本发送失败: {error}") from error

    async def send_at_text(self, chat_id: str, text: str, user_ids: Sequence[str], display_names: Sequence[str] | None = None) -> bool:
        import asyncio
        self._require_service()
        try:
            result = await asyncio.to_thread(
                self._service.send_at_text, chat_id, text, list(user_ids),
                display_names=list(display_names) if display_names else None,
            )
            return result is not False
        except Exception as error:
            raise DriverError(f"群 @ 发送失败: {error}") from error

    async def send_image(self, chat_id: str, path: str) -> bool:
        import asyncio
        self._require_service()
        try:
            result = await asyncio.to_thread(self._service.send_image, chat_id, path)
            return result is not False
        except Exception as error:
            raise DriverError(f"图片发送失败: {error}") from error

    async def send_file(self, chat_id: str, path: str) -> bool:
        import asyncio
        self._require_service()
        try:
            result = await asyncio.to_thread(self._service.send_file, chat_id, path)
            return result is not False
        except Exception as error:
            raise DriverError(f"文件发送失败: {error}") from error

    async def send_voice(self, chat_id: str, path: str) -> bool:
        import asyncio
        self._require_service()
        try:
            result = await asyncio.to_thread(self._service.send_voice, chat_id, path)
            return result is not False
        except Exception as error:
            raise DriverError(f"语音发送失败: {error}") from error

    async def send_emoticon(self, chat_id: str, path: str) -> bool:
        import asyncio
        self._require_service()
        try:
            result = await asyncio.to_thread(self._service.send_emoticon, chat_id, path)
            return result is not False
        except Exception as error:
            raise DriverError(f"表情发送失败: {error}") from error

    async def get_login_info(self) -> LoginInfo:
        return LoginInfo(self._self_id, self.config.driver.nickname)

    async def get_friend_list(self) -> Sequence[FriendInfo]:
        import asyncio
        self._require_service()
        try:
            snapshot = await asyncio.to_thread(self._service.contacts)
        except Exception as error:
            raise DriverError(f"读取联系人失败: {error}") from error
        rows = snapshot.get("contacts", []) if isinstance(snapshot, dict) else []
        return tuple(FriendInfo(
            external_user_id=str(row.get("wxid") or ""),
            nickname=str(row.get("nickname") or ""),
            remark=str(row.get("remark") or ""),
        ) for row in rows if row.get("wxid"))

    async def get_group_list(self) -> Sequence[GroupInfo]:
        self._require_service()
        chats = self._backend.chats() if self._backend is not None and hasattr(self._backend, "chats") else []
        rows: list[GroupInfo] = []
        for row in chats:
            chat_id = str(row.get("chat_id") or "")
            if row.get("is_group") or chat_id.endswith("@chatroom"):
                rows.append(GroupInfo(chat_id, str(row.get("name") or row.get("chat_name") or "")))
        return tuple(rows)

    async def get_group_member_list(self, group_id: str) -> Sequence[GroupMemberInfo]:
        import asyncio
        self._require_service()
        try:
            rows = await asyncio.to_thread(self._service.group_members, group_id)
        except Exception as error:
            raise DriverError(f"读取群成员失败: {error}") from error
        if rows is None:
            raise DriverError("微信尚未提供完整群成员缓存，请先在微信中打开该群成员列表")
        return tuple(GroupMemberInfo(
            external_group_id=group_id,
            external_user_id=str(row.get("wxid") or ""),
            nickname=str(row.get("wechat_name") or ""),
            card=str(row.get("group_nickname") or row.get("nickname") or ""),
            role=str(row.get("role") or "member"),
        ) for row in rows if row.get("wxid"))

    async def get_status(self) -> DriverStatus:
        details: dict[str, Any] = {}
        try:
            if self._backend is not None and hasattr(self._backend, "diagnostics"):
                details = self._backend.diagnostics()
        except Exception:
            details = {}
        return DriverStatus(online=self._online, good=self._online,
                            message="" if self._online else "微信未连接", details=details)

    def _require_service(self) -> None:
        if not self._online or self._service is None:
            raise DriverError("微信尚未连接")
