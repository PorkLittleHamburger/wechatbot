"""Driver for the independent ``wechatbot_api`` framework (WeChat 4.2.0.52).

The framework owns the native bridge: process/account discovery, injection,
event decoding and media download. This adapter only maps its typed public API
onto the gateway's :class:`Driver` contract and keeps the OneBot/WebUI layers
unchanged.

Importing ``wechatbot_api`` is side-effect free, but injection happens on
``Session.connect``. Nothing in this module connects unless ``start`` runs.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
import logging
import queue
import re
import threading
import time
import uuid
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
    Segment,
)
from .base import Driver, DriverError, DriverUnsupported, EventCallback

logger = logging.getLogger("wechat-onebot.sdk")

# Public SDK capabilities that have a direct OneBot mapping. Rich content is
# decoded by the framework; the gateway still owns the OneBot projection.
_CAPABILITIES = frozenset({
    "text.receive", "text.send", "group.text.mention",
    "image.receive", "image.send", "file.receive", "file.send",
    "voice.receive", "voice.send", "video.receive",
    "contact.read", "group.members.read", "forwarded.receive",
})

_IMAGE_SUFFIXES = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "WEBP": ".webp",
                   "BMP": ".bmp", "TIFF": ".tif"}
_VIDEO_SUFFIXES = {"MP4": ".mp4", "MOV": ".mov"}
_VOICE_SUFFIX = ".wav"

_AT_LABEL = re.compile(r"^(?:@[^\u2005@\n]{1,64}\u2005\s*)+")


def _now() -> int:
    return int(time.time())


def _code(error: BaseException) -> str:
    """Return the stable SDK error code without importing the package eagerly."""
    code = getattr(error, "code", None)
    value = getattr(code, "value", None) or (str(code) if code is not None else "")
    return value or type(error).__name__


_SESSION_BUSY_RETRY_SECONDS = 20.0
_SESSION_BUSY_RETRY_INTERVAL = 1.0


def _text(value: Any, limit: int) -> str:
    return str(value)[:limit] if value is not None else ""


def _thaw(value: Any) -> Any:
    """Copy the SDK's frozen mappings/tuples into plain JSON-able structures.

    The bot's rich-content projection checks ``isinstance(row, dict)``; a
    ``MappingProxyType`` would silently drop every record, so thaw first.
    """
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


class SdkDriver(Driver):
    """OneBot driver backed by the packaged ``wechatbot_api`` SDK."""

    def __init__(self, app_config: AppConfig):
        self.config = app_config
        self._online = False
        self._emit: EventCallback | None = None
        self._loop: Any = None
        self._client: Any = None
        self._session: Any = None
        self._subscription: Any = None
        self._target: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.RLock()
        self._updates: dict[tuple[str, str], Any] = {}
        self._deferred: deque[Any] = deque()
        self._media_timeout = app_config.driver.sdk_media_timeout
        self._inbound_dir: Path | None = None
        self._self_id = app_config.driver.wechat_account
        self._native_ids: deque[str] = deque(maxlen=64)

    # ------------------------------------------------------------------ state
    @property
    def capabilities(self) -> frozenset[str]:
        # Static before the session starts, matching the helper driver: the
        # service needs the vocabulary to validate actions and expose them.
        return _CAPABILITIES

    @property
    def online(self) -> bool:
        return self._online

    @property
    def account(self) -> str:
        return self._self_id

    def rebind(self, app_config: AppConfig) -> None:
        if self._online:
            raise DriverError("运行中的 SDK 驱动不能更换配置")
        self.config = app_config
        self._media_timeout = app_config.driver.sdk_media_timeout
        self._self_id = app_config.driver.wechat_account

    # ------------------------------------------------------------- lifecycle
    async def start(self, emit: EventCallback) -> None:
        import asyncio
        import sys

        if sys.platform != "win32":
            raise DriverError("wechatbot_api 驱动只支持 Windows x64")
        if not self.config.driver.wechat_account:
            raise DriverError("尚未选择微信账号，请在 WebUI 的进程与账号页面选择")
        if self._online:
            raise DriverError("微信驱动已启动")
        self._loop = asyncio.get_running_loop()
        self._emit = emit
        self._self_id = self.config.driver.wechat_account
        self._stop.clear()
        self._deferred.clear()
        self._updates.clear()
        account_dir, media_dir = account_directories(self.config, self._self_id)
        self._inbound_dir = media_dir / "inbound"
        self._inbound_dir.mkdir(parents=True, exist_ok=True)
        data_root = self.config.driver.sdk_data_root or (account_dir / "sdk")
        data_root.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wechat-sdk")
        try:
            await self._loop.run_in_executor(
                self._executor, self._open, data_root,
            )
            self._online = True
            self._thread = threading.Thread(
                target=self._consume, name="wechat-sdk-events", daemon=True,
            )
            self._thread.start()
        except Exception as error:
            self._online = False
            await self._shutdown()
            if isinstance(error, DriverError):
                raise
            hint = {
                "SESSION_BUSY": "微信进程已被其他桥接占用；已等待 20 秒仍未释放。请停止旧实例/旧桥接或重启微信后再试",
                "ACCESS_DENIED": "Bot 权限低于微信进程，请用与微信相同或更高的权限启动",
                "UNSUPPORTED_BUILD": "当前微信构建未适配；4.2.0.52 需要 sdk 驱动并核对核心指纹",
                "ABI_MISMATCH": "wechatbot_api 包与 bridge DLL 版本不匹配，请使用固定版本 wheel",
                "ACCOUNT_CHANGED": "微信账号在连接期间发生变化，请重新扫描并启动",
            }.get(_code(error), "")
            detail = f"{_code(error)}: {hint}" if hint else str(error)
            raise DriverError(f"wechatbot_api 启动失败（{detail}）") from error

    def _select_target(self, results: Sequence[Any]) -> Any:
        account = self.config.driver.wechat_account
        pid = self.config.driver.wechat_pid
        usable = [row for row in results
                  if getattr(row, "account", None) is not None and getattr(row, "error", None) is None]
        if pid is not None:
            exact = [row for row in usable if row.pid == pid]
            if exact and exact[0].account.wxid == account:
                return exact[0]
        matches = [row for row in usable if row.account.wxid == account]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise DriverError(f"发现多个使用 {account} 登录的微信进程，请在 WebUI 重新选择 PID")
        details = "; ".join(
            f"pid={row.pid} {getattr(row, 'detail', '') or ''}".strip() for row in results
        ) or "没有发现微信进程"
        version = ""
        for row in results:
            build = getattr(row, "build", None)
            if build is not None and getattr(build, "version", ""):
                version = build.version
                break
        hint = f"（当前核心版本 {version}）" if version else ""
        raise DriverError(
            f"未发现已登录且受支持的微信账号 {account}{hint}；诊断: {details}"
        )

    def _open(self, data_root: Path) -> None:
        try:
            import wechatbot_api
        except ImportError as error:  # pragma: no cover - packaging check
            raise DriverError(
                "未安装 wechatbot_api 运行时；请安装 vendor/wechatbot_api 下的 wheel"
            ) from error
        client = wechatbot_api.Client(data_root=data_root)
        target = self._select_target(client.discover())
        deadline = time.monotonic() + _SESSION_BUSY_RETRY_SECONDS
        attempt = 0
        while True:
            session = client.session(pid=target.pid, account_id=target.account.wxid)
            subscription = session.subscribe()
            try:
                session.connect(
                    timeout=30,
                    allow_unverified=self.config.driver.sdk_allow_unverified,
                    receive=True,
                )
            except Exception as error:
                try:
                    subscription.close(wait=False)
                except Exception:
                    logger.debug("关闭失败的 SDK 订阅时出错", exc_info=True)
                try:
                    session.close(timeout=1)
                except Exception:
                    logger.debug("关闭失败的 SDK 会话时出错", exc_info=True)
                if (_code(error) == "SESSION_BUSY"
                        and time.monotonic() < deadline):
                    attempt += 1
                    if attempt == 1:
                        logger.warning(
                            "微信进程仍被旧桥接占用，等待其释放后重试（最多 %.0f 秒）",
                            _SESSION_BUSY_RETRY_SECONDS,
                        )
                    time.sleep(_SESSION_BUSY_RETRY_INTERVAL)
                    continue
                if attempt:
                    logger.warning("旧桥接在 %.0f 秒内未释放，放弃重试", _SESSION_BUSY_RETRY_SECONDS)
                raise
            with self._lock:
                self._client = client
                self._session = session
                self._subscription = subscription
                self._target = target
            return

    async def stop(self) -> None:
        self._online = False
        self._stop.set()
        await self._shutdown()

    async def _shutdown(self) -> None:
        subscription, session = self._subscription, self._session
        self._subscription = None
        self._session = None
        self._client = None
        self._target = None
        if subscription is not None:
            try:
                subscription.close(wait=False)
            except Exception:
                logger.debug("关闭 SDK 订阅失败", exc_info=True)
        if session is not None and self._loop is not None and self._executor is not None:
            try:
                await self._loop.run_in_executor(self._executor, self._close_session, session)
            except Exception:
                logger.exception("关闭 wechatbot_api 会话失败")
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    @staticmethod
    def _close_session(session: Any) -> None:
        try:
            session.close(timeout=30)
        except Exception as error:
            if _code(error) != "SHUTDOWN_PENDING":
                raise
            # The session still owns native work; keep the object and wait for
            # the real teardown instead of forcing an unload.
            session.wait_closed(timeout=180)
            return
        try:
            session.cleanup_media()
        except Exception:
            logger.warning("SDK 媒体清理仍待重试", exc_info=True)

    # ---------------------------------------------------------------- events
    def _consume(self) -> None:
        while not self._stop.is_set():
            try:
                if self._deferred:
                    event = self._deferred.popleft()
                else:
                    subscription = self._subscription
                    if subscription is None:
                        break
                    event = subscription.get(timeout=0.5)
            except queue.Empty:
                continue
            except (StopIteration, RuntimeError):
                break
            try:
                self._handle(event)
            except Exception:
                logger.exception("处理 wechatbot_api 事件失败 kind=%r",
                                 getattr(event, "kind", None))

    def _handle(self, event: Any) -> None:
        kind = getattr(event, "kind", "")
        if kind == "message":
            message = self._convert_message(event)
            if message is not None:
                self._dispatch(message)
        elif kind == "media_update":
            content = event.content or {}
            key = (str(content.get("message_event_id") or ""),
                   str(content.get("media_id") or ""))
            if key != ("", ""):
                with self._lock:
                    if len(self._updates) >= 64:
                        self._updates.pop(next(iter(self._updates)))
                    self._updates[key] = event
        elif kind == "diagnostic":
            logger.warning("wechatbot_api 诊断事件: %s", dict(event.content or {}))
        # Notifications (system messages) have no verified OneBot mapping yet.

    def _dispatch(self, message: IncomingMessage) -> None:
        if self._loop is None or self._emit is None:
            return
        import asyncio

        future = asyncio.run_coroutine_threadsafe(self._emit(message), self._loop)
        future.add_done_callback(self._log_emit_error)

    @staticmethod
    def _log_emit_error(future: Any) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("事件分发失败")

    # ------------------------------------------------------------- conversion
    def _convert_message(self, event: Any) -> IncomingMessage | None:
        content = event.content or {}
        chat_id = str(event.chat_id or "")
        sender_id = str(event.sender_id or "")
        is_group = chat_id.endswith("@chatroom")
        segments: list[Segment] = []
        mentions = tuple(str(item) for item in (event.mentions or ()) if item)
        for member in mentions:
            segments.append(Segment("at", {"qq": member}))
        kind = str(event.content_type or "unknown")
        raw: dict[str, Any] = {"sdk": {
            "event_id": str(event.event_id),
            "kind": kind,
            "media_state": content.get("media_state"),
            "media_reason": content.get("media_reason"),
            "native_type": content.get("native_type"),
        }}
        if mentions and self._self_id:
            raw["is_at_self"] = self._self_id in mentions
        text = self._strip_at_labels(str(event.text or ""), mentions) if mentions else str(event.text or "")
        body = self._body_segments(event, content, kind)
        if text and body and body[0].type == "text":
            body = (Segment("text", {"text": text + body[0].data.get("text", "")}),) + body[1:]
        elif text:
            body = (Segment("text", {"text": text}),) + body
        segments.extend(body)
        if kind == "reference":
            reply = self._reference_payload(event)
            if reply is not None:
                raw["reply"] = reply
        if not segments:
            logger.debug("跳过无法映射的 SDK 消息 kind=%r", kind)
            return None
        return IncomingMessage(
            external_chat_id=chat_id,
            external_sender_id=sender_id,
            segments=tuple(segments),
            external_message_id=str(event.native_message_id or ""),
            sender_name="",
            sender_card="",
            group_name="",
            sender_role="unknown",
            is_group=is_group,
            is_self=bool(content.get("is_self")),
            sequence=int(event.sequence or 0),
            timestamp=int(event.native_timestamp) if event.native_timestamp else _now(),
            raw=raw,
        )

    def _body_segments(self, event: Any, content: Any, kind: str) -> tuple[Segment, ...]:
        if kind == "text":
            return ()
        if kind == "link":
            title = _text(content.get("title"), 4096)
            url = _text(content.get("url"), 8192)
            text = "\n".join(part for part in (title, url) if part) or "[链接内容不可用]"
            return (Segment("text", {"text": text}),)
        if kind == "forwarded":
            from ..protocol.rich_content import rich_segments

            records = _thaw(content.get("forwarded_records") or [])
            if not isinstance(records, list) or not records:
                return (Segment("text", {"text": "[聊天记录内容不可用]"}),)
            return rich_segments(records, is_forward=True,
                                 title=_text(content.get("forwarded_title"), 4096),
                                 truncated=bool(content.get("forwarded_truncated")))
        if kind == "emoticon":
            return (Segment("text", {"text": "[表情]"}),)
        if kind in ("image", "file", "voice", "video"):
            path, reason = self._resolve_media(event, content, kind)
            if path is None:
                label = {"image": "[图片]", "file": "[文件或卡片]",
                         "voice": "[语音]", "video": "[视频]"}[kind]
                if reason:
                    logger.info("入站媒体不可用 kind=%s reason=%s", kind, reason)
                return (Segment("text", {"text": label}),)
            if kind == "image":
                return (Segment("image", {"file": str(path)}),)
            if kind == "file":
                metadata = content.get("file_metadata")
                name = _text(metadata.get("title") if isinstance(metadata, Mapping) else "", 256)
                return (Segment("file", {"file": str(path), "name": name or Path(path).name}),)
            if kind == "voice":
                return (Segment("record", {"file": str(path)}),)
            return (Segment("video", {"file": str(path)}),)
        return ()

    def _resolve_media(self, event: Any, content: Any, kind: str = "") -> tuple[Path | None, str]:
        if content.get("media_state") != "pending":
            return None, _text(content.get("media_reason"), 200)
        media_id = str(content.get("media_id") or "")
        if not media_id:
            return None, "缺少媒体标识"
        session = self._session
        if session is None:
            return None, "会话已关闭"
        try:
            session.request_media(event, media_id=media_id)
        except Exception as error:
            logger.debug("request_media 失败 event=%s media=%s", event.event_id, media_id, exc_info=True)
            return None, f"request_media {_code(error)}"
        update = self._await_update(str(event.event_id), media_id, time.monotonic() + self._media_timeout)
        if update is None:
            return None, "等待媒体更新超时"
        state = str((update.content or {}).get("media_state") or "")
        if state != "ready" or getattr(update, "media", None) is None:
            return None, str((update.content or {}).get("reason")
                             or (update.content or {}).get("error")
                             or f"媒体状态 {state or 'unknown'}")
        suffix = self._media_suffix(update, kind)
        destination = self._inbound_dir / f"{event.event_id}-{media_id}{suffix}"
        handle = update.media
        try:
            if destination.exists():
                destination = self._inbound_dir / f"{event.event_id}-{media_id}-{uuid.uuid4().hex[:8]}{suffix}"
            handle.export(destination)
            return destination, ""
        except Exception as error:
            logger.warning("导出 SDK 媒体失败: %s", error)
            return None, "媒体导出失败"
        finally:
            try:
                handle.release()
            except Exception:
                logger.debug("释放 SDK 媒体句柄失败", exc_info=True)

    @staticmethod
    def _media_suffix(update: Any, kind: str = "") -> str:
        content = update.content or {}
        metadata = content.get("media_metadata") or {}
        fmt = str(metadata.get("format") or "").upper() if isinstance(metadata, Mapping) else ""
        kind = kind or str(getattr(update, "content_type", "") or "")
        if fmt in _IMAGE_SUFFIXES:
            return _IMAGE_SUFFIXES[fmt]
        if fmt in _VIDEO_SUFFIXES:
            return _VIDEO_SUFFIXES[fmt]
        if kind == "voice":
            return _VOICE_SUFFIX
        if kind == "video":
            return ".mp4"
        if kind == "image":
            return ".png"
        return ".bin"

    def _await_update(self, event_id: str, media_id: str, deadline: float) -> Any:
        key = (event_id, media_id)
        with self._lock:
            cached = self._updates.pop(key, None)
        if cached is not None:
            return cached
        subscription = self._subscription
        while subscription is not None and time.monotonic() < deadline and not self._stop.is_set():
            remaining = max(0.05, deadline - time.monotonic())
            try:
                candidate = subscription.get(timeout=remaining)
            except queue.Empty:
                return None
            except (StopIteration, RuntimeError):
                return None
            if candidate.kind == "media_update":
                content = candidate.content or {}
                if (str(content.get("message_event_id") or "") == event_id and
                        str(content.get("media_id") or "") == media_id):
                    return candidate
            # Keep unrelated events in order; the consume loop replays them.
            self._deferred.append(candidate)
        return None

    def _reference_payload(self, event: Any) -> dict[str, Any] | None:
        reference = event.reference
        if not isinstance(reference, Mapping):
            return None
        rtype = reference.get("type")
        if type(rtype) is not int:
            return None
        reply: dict[str, Any] = {
            "native_message_id": _text(reference.get("native_message_id"), 20),
            "sender_id": _text(reference.get("sender_id"), 127),
            "sender_name": _text(reference.get("sender_name"), 256),
            "type": rtype,
            "content": _text(reference.get("content"), 32768) if rtype == 1 else "",
        }
        availability = reference.get("forwarded_availability")
        if isinstance(availability, str):
            reply["forwarded_availability"] = availability
        structured = reference.get("structured_content")
        if isinstance(structured, Mapping):
            records = _thaw(structured.get("forwarded_records"))
            if (rtype == 49 and isinstance(records, list) and records
                    and structured.get("app_type") == 19):
                reply["records"] = records
                reply["is_forward"] = True
                reply["title"] = _text(structured.get("forwarded_title"), 4096)
                reply["truncated"] = bool(structured.get("forwarded_truncated"))
        return reply

    @staticmethod
    def _strip_at_labels(text: str, mentions: Sequence[str]) -> str:
        if not mentions or not text.startswith("@"):
            return text
        stripped = _AT_LABEL.sub("", text)
        return stripped if stripped != text else text

    # ---------------------------------------------------------------- sending
    async def send_text(self, chat_id: str, text: str) -> bool:
        return await self._send("send_text", chat_id, text)

    async def send_at_text(self, chat_id: str, text: str, user_ids: Sequence[str],
                           display_names: Sequence[str] | None = None) -> bool:
        members = [str(item) for item in user_ids if item]
        if not members:
            raise DriverError("群聊 @ 消息缺少目标成员")
        names = [str(item) for item in display_names] if display_names else None
        return await self._send("send_at_text", chat_id, text, members=members, display_names=names)

    async def send_image(self, chat_id: str, path: str) -> bool:
        return await self._send("send_image", chat_id, str(path))

    async def send_file(self, chat_id: str, path: str) -> bool:
        return await self._send("send_file", chat_id, str(path))

    async def send_voice(self, chat_id: str, path: str) -> bool:
        return await self._send("send_voice", chat_id, str(path))

    async def send_emoticon(self, chat_id: str, path: str) -> bool:
        raise DriverUnsupported("wechatbot_api 驱动不支持发送表情")

    def take_native_message_ids(self) -> tuple[str, ...]:
        ids = tuple(self._native_ids)
        self._native_ids.clear()
        return ids

    async def _send(self, method: str, *args: Any, **kwargs: Any) -> bool:
        import asyncio

        session = self._session
        if session is None or not self._online:
            raise DriverError("微信驱动尚未启动")
        call = getattr(session, method)
        try:
            result = await asyncio.to_thread(call, *args, timeout=60, **kwargs)
        except Exception as error:
            raise DriverError(f"发送失败: {_code(error)}") from error
        state_value = getattr(result, "state", "")
        state = getattr(state_value, "value", str(state_value))
        if state == "completed":
            native = getattr(result, "native_message_id", None)
            if native:
                self._native_ids.append(str(native))
            return True
        if state in ("accepted", "unknown"):
            # A native send may still complete. Never retry it automatically.
            raise DriverError(
                f"发送结果未知（{state}），请勿自动重发；operation={getattr(result, 'operation_id', '')}"
            )
        error = getattr(result, "error", None)
        detail = str(getattr(result, "detail", "") or "").strip()
        raise DriverError(f"发送失败: {_code(error) if error is not None else state} {detail}".strip())

    # ----------------------------------------------------------- read queries
    @property
    def _pid(self) -> int:
        return int(getattr(self._target, "pid", 0) or self.config.driver.wechat_pid or 0)

    async def get_login_info(self) -> LoginInfo:
        account = getattr(self._target, "account", None)
        account_id = self._self_id
        nickname = ""
        if account is not None:
            account_id = str(account.wxid)
            nickname = _text(getattr(account, "wechat_name", ""), 256)
        if not nickname:
            import asyncio

            nickname = await asyncio.to_thread(self._cached_self_name)
        if not nickname:
            nickname = self.config.driver.nickname
        return LoginInfo(external_user_id=account_id or self._self_id, nickname=nickname)

    def _cached_self_name(self) -> str:
        """Read the bound account's own WeChat name from the read-only cache."""
        client, target = self._client, self._target
        if client is None or target is None:
            return ""
        try:
            row = client.contact(self._self_id, pid=int(getattr(target, "pid", 0) or 0),
                                 account_id=self._self_id)
        except Exception:
            logger.debug("读取账号昵称缓存失败", exc_info=True)
            return ""
        return _text(getattr(row, "wechat_name", None) or getattr(row, "remark", None), 256)

    async def _snapshot(self, method: str, *args: Any, **kwargs: Any) -> Any:
        import asyncio

        if self._client is None or self._session is None:
            raise DriverError("微信驱动尚未启动")
        call = getattr(self._client, method)
        try:
            return await asyncio.to_thread(call, *args, pid=self._pid,
                                           account_id=self._self_id, **kwargs)
        except Exception as error:
            raise DriverError(f"读取微信缓存失败: {_code(error)}") from error

    async def get_friend_list(self) -> Sequence[FriendInfo]:
        snapshot = await self._snapshot("contacts")
        return [FriendInfo(external_user_id=str(row.wxid),
                           nickname=_text(row.wechat_name, 256),
                           remark=_text(row.remark, 256))
                for row in snapshot.records]

    async def get_group_list(self) -> Sequence[GroupInfo]:
        snapshot = await self._snapshot("groups")
        return [GroupInfo(external_group_id=str(row.wxid), group_name=_text(row.name, 256))
                for row in snapshot.records]

    async def get_group_member_list(self, group_id: str) -> Sequence[GroupMemberInfo]:
        snapshot = await self._snapshot("group_members", group_id)
        rows: list[GroupMemberInfo] = []
        for row in snapshot.records:
            role = row.role if row.role in ("owner", "admin", "member") else "member"
            rows.append(GroupMemberInfo(
                external_group_id=group_id, external_user_id=str(row.wxid),
                nickname=_text(row.wechat_name, 256), card=_text(row.group_nickname, 256),
                role=role,
            ))
        return rows

    async def get_status(self) -> DriverStatus:
        session = self._session
        if session is None:
            return DriverStatus(online=False, good=False, message="微信驱动尚未启动")
        details: dict[str, Any] = {"driver": "wechatbot_api"}
        try:
            status = session.status()
            details.update(state=getattr(status, "state", ""),
                           pid=getattr(status, "pid", self._pid),
                           pending_operations=getattr(status, "pending_operations", 0))
        except Exception as error:
            details["status_error"] = _code(error)
        try:
            diagnostics = session.diagnostics()
            watchdog = diagnostics.get("watchdog") if isinstance(diagnostics, dict) else None
            if isinstance(watchdog, dict):
                details["watchdog"] = {key: watchdog.get(key) for key in
                                       ("state", "running", "warning") if key in watchdog}
        except Exception:
            logger.debug("读取 SDK 诊断失败", exc_info=True)
        if self._target is not None and getattr(self._target, "build", None) is not None:
            build = self._target.build
            details.update(wechat_version=getattr(build, "version", ""),
                           wechat_sha256=getattr(build, "sha256", ""))
        return DriverStatus(online=self._online, good=self._online,
                            message="wechatbot_api 已连接" if self._online else "微信驱动离线",
                            details=details)
