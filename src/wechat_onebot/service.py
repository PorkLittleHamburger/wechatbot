from __future__ import annotations

import asyncio
import copy
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .config import AppConfig
from .driver.base import Driver, DriverError, DriverUnsupported
from .audio import VoiceError, ensure_silk_chunks, export_format
from .ids import IdMapper
from .media import MediaError, MediaStore
from .onebot import MessageStore, message_event_name, message_sequence
from .onebot.actions import ACTION_REGISTRY, ActionValidationError, action_catalog
from .models import (
    DriverEvent,
    FriendInfo,
    GroupInfo,
    GroupMemberInfo,
    IncomingMessage,
    NoticeEvent,
    RequestEvent,
    Segment,
)
from .protocol import failure, message_event, meta_event, notice_event, ok, request_event
from .protocol.rich_content import canonical_forward, materialize_forwards, rich_segments
from .protocol.codec import normalize_message, serialize_message
from .protocol import outbound_forward as forwarding


ECHO_UNSET = object()
logger = logging.getLogger("wechat-onebot.actions")


class OneBotActionError(Exception):
    def __init__(self, retcode: int, message: str, data: Any = None):
        super().__init__(message)
        self.retcode = retcode
        self.data = data


@dataclass(frozen=True)
class PreparedSegment:
    type: str
    data: dict[str, Any]


EventSink = Callable[[dict[str, Any]], Awaitable[None]]


class OneBotService:
    def __init__(
        self,
        config: AppConfig,
        driver: Driver,
        mapper: IdMapper,
        media: MediaStore,
        *,
        message_store: MessageStore | None = None,
        account: str | None = None,
    ):
        self.config = config
        self.driver = driver
        self.mapper = mapper
        self.media = media
        self.account = account or config.driver.wechat_account or "unknown"
        self._owns_message_store = message_store is None
        self.message_store = message_store or MessageStore(":memory:")
        self.self_id: int = 0
        self.started = False
        self.group_mention_only = False
        self._event_sink: EventSink | None = None
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=200)
        self.rate_limit_interval_ms = 500
        self._action_tasks: set[asyncio.Task] = set()
        self._rate_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._rate_worker: asyncio.Task | None = None
        self._stopping_actions = False
        self._send_lock = asyncio.Lock()
        self._event_queue: asyncio.Queue[DriverEvent | None] = asyncio.Queue()
        self._event_worker: asyncio.Task | None = None
        self._events_closed = False

    @property
    def supported_actions(self) -> frozenset[str]:
        return frozenset(ACTION_REGISTRY)

    def set_event_sink(self, sink: EventSink | None) -> None:
        self._event_sink = sink

    async def start(self) -> None:
        if self.started:
            return
        self._stopping_actions = False
        self._events_closed = False
        login = await self.driver.get_login_info()
        if not login.external_user_id:
            raise DriverError("微信账号 ID 为空，无法启动 OneBot")
        self.self_id = self.mapper.get_or_create("user", login.external_user_id)
        self.message_store.remember_profiles(self.account, [
            (login.external_user_id, "", login.nickname, "", "login")])
        self._event_worker = asyncio.create_task(self._run_event_queue(), name="onebot-events")
        try:
            await self.driver.start(self._enqueue_event)
        except Exception:
            await self._drain_events()
            raise
        self.started = True

    async def _enqueue_event(self, event: DriverEvent) -> None:
        """Queue one driver event and wait for this account's serial processing."""
        if self._events_closed:
            return
        self._event_queue.put_nowait(event)
        await self._event_queue.join()

    async def _run_event_queue(self) -> None:
        while True:
            event = await self._event_queue.get()
            try:
                if event is None:
                    return
                await self._on_driver_event(event)
            except Exception:
                logger.exception("处理驱动事件失败")
            finally:
                self._event_queue.task_done()

    async def _drain_events(self) -> None:
        self._events_closed = True
        worker = self._event_worker
        if worker is None:
            return
        if not worker.done():
            self._event_queue.put_nowait(None)
            await worker
        self._event_worker = None

    async def stop(self) -> None:
        if not self.started:
            if self._owns_message_store:
                self.message_store.close()
            return
        self.started = False
        try:
            self._stopping_actions = True
            # Cancelling a to_thread await does not stop its native operation.
            # Stop queue scheduling, then drain owned sends before closing helper.
            if self._rate_worker is not None:
                self._rate_worker.cancel()
                await asyncio.gather(self._rate_worker, return_exceptions=True)
            await asyncio.gather(*self._action_tasks, return_exceptions=True)
            self._rate_worker = None
            while not self._rate_queue.empty():
                self._rate_queue.get_nowait()
                self._rate_queue.task_done()
            try:
                await self.driver.stop()
            finally:
                await self._drain_events()
        finally:
            if self._owns_message_store:
                self.message_store.close()

    async def _on_driver_event(self, event: DriverEvent) -> None:
        payload: dict[str, Any]
        if isinstance(event, IncomingMessage):
            user = event.external_sender_id or ("" if event.is_group else event.external_chat_id)
            group = event.external_chat_id if event.is_group else ""
            known_member = bool(group and self.message_store.has_membership(self.account, user, group))
            self.message_store.remember_profiles(self.account, [
                (user, "", event.sender_name, "", "message"),
                *([(user, group, "", event.sender_card, "message")] if group else []),
            ])
            profile = self.message_store.profile(self.account, user, group)
            if group and (not known_member or not (profile and profile["nickname"])):
                try:
                    self._remember_members(group, await self.driver.get_group_member_list(group))
                except DriverError:
                    pass
                profile = self.message_store.profile(self.account, user, group)
            if not group and not (profile and profile["nickname"]):
                try:
                    await self._get_friend_list({})
                except DriverError:
                    pass
                profile = self.message_store.profile(self.account, user)
            if profile:
                event = replace(event, sender_name=event.sender_name or profile["nickname"],
                                sender_card=event.sender_card or profile["card"])
            event_name = message_event_name(event)
            session_key = event.external_chat_id
            sequence_key = message_sequence(event)
            message_id = self.message_store.get_or_allocate_id(
                account=self.account,
                event_name=event_name,
                session_key=session_key,
                sequence_key=sequence_key,
            )
            payload = message_event(event, self.mapper, self.media, self.self_id,
                                    message_id=message_id)
            self._materialize_forwards(payload, message_id)
            reference_id = self._resolve_reference(event)
            if reference_id is not None:
                payload["message"].insert(0, {"type": "reply", "data": {"id": str(reference_id)}})
            payload["raw_message"] = serialize_message(payload["message"])
            changed = self.message_store.put(
                message_id,
                account=self.account,
                event_name=event_name,
                session_key=session_key,
                payload=payload,
            )
            self.message_store.remember_native_id(self.account, session_key, event.external_message_id, message_id)
            if not changed:
                return
        elif isinstance(event, NoticeEvent):
            if event.notice_type == "group_decrease":
                self.message_store.forget_membership(self.account, event.external_user_id, event.external_chat_id)
            recalled_id = None
            if event.notice_type in ("group_recall", "friend_recall"):
                recall = event.raw.get("recall", {})
                native_id = str(recall.get("message_id", "")) if isinstance(recall, dict) else ""
                original = self.message_store.native_message(self.account, event.external_chat_id, native_id)
                if original is None:
                    logger.warning("Skipping recall without a stored original message")
                    return
                recalled_id = original["message_id"]
            payload = notice_event(event, self.mapper, self.self_id, message_id=recalled_id)
        elif isinstance(event, RequestEvent):
            payload = request_event(event, self.mapper, self.self_id)
        else:
            return
        if payload is None:
            return
        self._recent_events.append(copy.deepcopy(payload))
        if (isinstance(event, IncomingMessage) and event.is_group and self.group_mention_only
                and not any(segment.type == "at" and str(segment.data.get("qq")) ==
                            self.mapper.reverse("user", self.self_id) for segment in event.segments)):
            return
        if self._event_sink is not None:
            await self._event_sink(payload)

    def _resolve_reference(self, event: IncomingMessage) -> int | None:
        reference = event.raw.get("reply")
        if not isinstance(reference, dict):
            return None
        native_id = reference.get("native_message_id", "")
        sender = reference.get("sender_id", "")
        if (not isinstance(native_id, str) or not native_id.isascii() or not native_id.isdecimal()
                or not 0 < len(native_id) <= 20 or int(native_id) == 0 or not isinstance(sender, str) or not sender):
            return None
        cached = self.message_store.native_message(self.account, event.external_chat_id, native_id)
        user_id = self.mapper.get_or_create("user", sender)
        if (cached and cached.get("user_id") == user_id and
                not self.message_store.is_reference(cached['message_id'], self.account)):
            return cached["message_id"]
        if cached and cached.get("user_id") != user_id:
            return None  # Do not replace a known message's identity with quoted metadata.
        if cached and reference.get("forwarded_availability") == "unavailable":
            for segment in cached.get("message", []):
                if segment.get("type") == "forward":
                    forward = self.message_store.get_forward(self.account, segment["data"]["id"])
                    if forward is not None and forward.get("status") == "ready":
                        return cached["message_id"]
        kind = reference.get("type")
        text = reference.get("content", "") if kind == 1 else {
            3: "[引用图片：原图未缓存]", 34: "[引用语音：原消息未缓存]",
            43: "[引用视频：原消息未缓存]", 49: "[引用文件或卡片：原消息未缓存]",
        }.get(kind, "[引用消息：原消息未缓存]")
        if not isinstance(text, str):
            return None
        snapshot = IncomingMessage(
            external_chat_id=event.external_chat_id, external_sender_id=sender,
            segments=(Segment("text", {"text": text[:65536]}),),
            sender_name=str(reference.get("sender_name", ""))[:256], is_group=event.is_group)
        if isinstance(reference.get('records'), list):
            snapshot = replace(snapshot, segments=rich_segments(reference['records'],
                is_forward=bool(reference.get('is_forward')), title=reference.get('title', ''),
                truncated=bool(reference.get('truncated'))))
        reference_id = self.message_store.get_or_allocate_id(
            account=self.account, event_name="reference_snapshot", session_key=event.external_chat_id,
            sequence_key=native_id)
        payload = message_event(snapshot, self.mapper, self.media, self.self_id, message_id=reference_id)
        self._materialize_forwards(payload, reference_id)
        payload["raw_message"] = serialize_message(payload["message"])
        payload["time"] = 0  # The quote does not provide a verified original timestamp.
        self.message_store.put_reference(reference_id, self.account, payload)
        self.message_store.remember_native_id(self.account, event.external_chat_id, native_id, reference_id)
        return reference_id

    def _materialize_forwards(self, payload, message_id):
        materialize_forwards(payload, store=self.message_store, mapper=self.mapper, media=self.media,
                             account=self.account, message_id=message_id)

    async def _get_forward_msg(self, params):
        value = params.get('id', params.get('message_id'))
        if isinstance(value, bool) or not isinstance(value, (str, int)) or not 0 < len(str(value)) <= 128:
            raise OneBotActionError(1400, '需要有效的转发 id 或 message_id')
        payload = self.message_store.get_forward(self.account, str(value))
        if payload is None:
            raise OneBotActionError(1404, '合并转发不在当前账号的消息存储中')
        return await asyncio.to_thread(self.media.refresh_payload, canonical_forward(payload))

    async def dispatch(self, action: str, params: dict[str, Any] | None = None, echo: Any = ECHO_UNSET) -> dict[str, Any]:
        result = await self._dispatch(action, params)
        if echo is not ECHO_UNSET:
            result["echo"] = echo
        return result

    async def _dispatch(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return failure(1400, "params 必须是 JSON 对象")
        try:
            if not isinstance(action, str):
                return failure(1400, "action 必须是字符串")
            mode = ""
            for suffix in ("_rate_limited", "_async"):
                if action.endswith(suffix):
                    mode, action = suffix, action[:-len(suffix)]
                    break
            definition = ACTION_REGISTRY.get(action)
            if definition is None:
                return failure(1404, f"不支持 OneBot 动作: {action}")
            params = definition.normalize(params)
            if mode:
                if not self.started or self._stopping_actions:
                    return failure(1500, "服务尚未启动")
                if mode == "_rate_limited":
                    if self._rate_queue.full():
                        return failure(1500, "限速队列已满")
                    self._rate_queue.put_nowait((action, copy.deepcopy(params)))
                    if self._rate_worker is None or self._rate_worker.done():
                        self._rate_worker = self._background(self._run_rate_queue())
                else:
                    if len(self._action_tasks) >= 1000:
                        return failure(1500, "异步动作过多")
                    self._background(self._run_background(action, copy.deepcopy(params)))
                return {"status": "async", "retcode": 1, "data": None}
            handler = getattr(self, definition.handler, None)
            if not callable(handler):
                raise RuntimeError(f"动作注册表指向不存在的处理器: {definition.handler}")
            data = await handler(params)
            return ok(data)
        except ActionValidationError as error:
            return failure(1400, str(error))
        except OneBotActionError as error:
            return failure(error.retcode, str(error), data=error.data)
        except DriverUnsupported as error:
            return failure(1404, str(error))
        except DriverError as error:
            return failure(1500, str(error))
        except (ValueError, TypeError) as error:
            return failure(1400, str(error))
        except Exception as error:
            import logging
            logging.getLogger("wechat-onebot.actions").exception("OneBot 动作失败: %s", action)
            return failure(1500, f"内部错误: {error}")

    def _background(self, operation) -> asyncio.Task:
        task = asyncio.create_task(operation)
        self._action_tasks.add(task)
        task.add_done_callback(self._action_done)
        return task

    def _action_done(self, task: asyncio.Task) -> None:
        self._action_tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.warning("Owned action failed: %s", error)

    async def _run_background(self, action, params) -> None:
        result = await self.dispatch(action, params)
        if result["status"] == "failed":
            logger.warning("Async action %s failed: %s", action, result.get("message"))

    async def _run_rate_queue(self) -> None:
        while True:
            action, params = await self._rate_queue.get()
            try:
                task = self._background(self._run_background(action, params))
                await asyncio.shield(task)
            finally:
                self._rate_queue.task_done()
            await asyncio.sleep(self.rate_limit_interval_ms / 1000)

    async def handle_quick_operation(self, event: dict[str, Any], operations: dict[str, Any]) -> None:
        """Execute standard HTTP POST replies through the same validated send path."""
        if self._stopping_actions:
            raise OneBotActionError(1500, "服务正在关闭，拒绝新的快速操作")
        task = self._background(self._handle_quick_operation(event, operations))
        await asyncio.shield(task)

    async def _handle_quick_operation(self, event: dict[str, Any], operations: dict[str, Any]) -> None:
        if not isinstance(operations, dict):
            raise OneBotActionError(1400, "快速操作必须是 JSON 对象")
        for key in ("delete", "kick", "ban", "approve"):
            if key in operations and type(operations[key]) is not bool:
                raise OneBotActionError(1400, f"{key} 必须是布尔值")
        if "ban_duration" in operations and (
                isinstance(operations["ban_duration"], bool) or
                not isinstance(operations["ban_duration"], (int, float))):
            raise OneBotActionError(1400, "ban_duration 必须是数字")
        unsupported = [key for key in ("delete", "kick", "ban")
                       if operations.get(key) is True]
        if "approve" in operations:
            unsupported.append("approve")
        if unsupported:
            # Quick-operation fields are independent; a field this native bridge
            # cannot perform must not discard a supported reply in the same body.
            logger.warning("HTTP 快速操作包含不支持项 %s，已按字段独立执行", ",".join(unsupported))
        if "reply" not in operations:
            return
        if event.get("post_type") != "message" or event.get("self_id") != self.self_id:
            raise OneBotActionError(1400, "仅可回复本账号的消息事件")
        for key in ("auto_escape", "at_sender"):
            if key in operations and type(operations[key]) is not bool:
                raise OneBotActionError(1400, f"{key} 必须是布尔值")
        message = normalize_message(operations["reply"], auto_escape=operations.get("auto_escape", False))
        is_group = event.get("message_type") == "group"
        if is_group and operations.get("at_sender", True) and not event.get("anonymous"):
            message.insert(0, {"type": "at", "data": {"qq": str(event["user_id"])}})
        target = "group_id" if is_group else "user_id"
        response = await self.dispatch("send_group_msg" if is_group else "send_private_msg",
                                       {target: event[target], "message": message})
        if response["status"] != "ok":
            raise OneBotActionError(response["retcode"], response.get("message", "快速回复失败"))

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        count = max(1, min(int(limit), 200))
        return copy.deepcopy(list(self._recent_events)[-count:])

    def heartbeat_event(self) -> dict[str, Any]:
        status = {
            "online": self.driver.online,
            "good": self.driver.online,
        }
        return meta_event(self.self_id, "heartbeat", status=status,
                          interval=self.config.onebot.heartbeat_interval_seconds * 1000)

    def lifecycle_event(self, sub_type: str = "connect") -> dict[str, Any]:
        return meta_event(self.self_id, "lifecycle", sub_type=sub_type)

    @staticmethod
    def _required_int(params: dict[str, Any], key: str, *, allow_negative: bool = False) -> int:
        value = params.get(key)
        if isinstance(value, bool):
            raise OneBotActionError(1400, f"{key} 必须是整数")
        if isinstance(value, int):
            number = value
        elif isinstance(value, str) and value.isascii() and (value.isdecimal() or (allow_negative and value.startswith("-") and value[1:].isdecimal())):
            number = int(value)
        else:
            raise OneBotActionError(1400, f"{key} 必须是 OneBot 数字 ID")
        if allow_negative:
            if number == 0 or not -(2**31) <= number <= 2**31 - 1:
                raise OneBotActionError(1400, f"{key} 必须是非零 signed int32")
        elif number <= 0:
            raise OneBotActionError(1400, f"{key} 必须是正整数")
        return number

    def _external_user(self, number: int) -> str:
        external = self.mapper.reverse("user", number)
        if not external:
            raise OneBotActionError(1400, f"未知 OneBot user_id: {number}")
        return external

    def _external_group(self, number: int) -> str:
        external = self.mapper.reverse("group", number)
        if not external:
            raise OneBotActionError(1400, f"未知 OneBot group_id: {number}")
        return external

    async def _send_private_msg(self, params: dict[str, Any]) -> dict[str, Any]:
        user_id = self._required_int(params, "user_id")
        chat_id = self._external_user(user_id)
        message_id = await self._send_message(chat_id, params.get("message"), is_group=False,
                                              auto_escape=params.get("auto_escape", False))
        return {"message_id": message_id}

    async def _send_group_msg(self, params: dict[str, Any]) -> dict[str, Any]:
        group_id = self._required_int(params, "group_id")
        chat_id = self._external_group(group_id)
        message_id = await self._send_message(chat_id, params.get("message"), is_group=True,
                                              auto_escape=params.get("auto_escape", False))
        return {"message_id": message_id}

    async def _send_msg(self, params: dict[str, Any]) -> dict[str, Any]:
        message_type = params.get("message_type")
        if message_type == "group" or params.get("group_id") is not None:
            return await self._send_group_msg(params)
        if message_type == "private" or params.get("user_id") is not None:
            return await self._send_private_msg(params)
        raise OneBotActionError(1400, "send_msg 需要 message_type 或 user_id/group_id")

    async def _send_group_forward_msg(self, params: dict[str, Any]) -> dict[str, Any]:
        group = self._external_group(self._required_int(params, "group_id"))
        return await self._send_forward(group, params["messages"], is_group=True)

    async def _send_private_forward_msg(self, params: dict[str, Any]) -> dict[str, Any]:
        user = self._external_user(self._required_int(params, "user_id"))
        return await self._send_forward(user, params["messages"], is_group=False)

    async def _send_forward(self, chat_id: str, messages: Any, *, is_group: bool) -> dict[str, Any]:
        if self._stopping_actions or len(self._action_tasks) >= 1000:
            raise OneBotActionError(1500, "服务正在关闭或待处理动作过多")
        task = self._background(self._send_forward_owned(chat_id, messages, is_group=is_group))
        return await asyncio.shield(task)

    async def _send_forward_owned(self, chat_id: str, messages: Any, *, is_group: bool) -> dict[str, Any]:
        try:
            plan = forwarding.expand_forward(messages,
                lookup_message=lambda mid: self.message_store.get(mid, account=self.account),
                lookup_forward=lambda fid: (
                    canonical_forward(value) if (value := self.message_store.get_forward(self.account, fid)) else None))
        except forwarding.ForwardSendError as error:
            raise OneBotActionError(error.retcode, str(error)) from error
        media_kinds = ("image", "file", "record", "face")
        if sum(segment["type"] in media_kinds for chain in plan.chains for segment in chain) > forwarding.MAX_MEDIA:
            raise OneBotActionError(1400, "转发发送超出媒体额度")
        prepared = []
        media_budget = {"count": 0, "bytes": 0}
        estimated_sends = 0
        for chain in plan.chains:
            segments = await self._prepare_segments(chain, is_group=is_group, media_budget=media_budget)
            self._validate_sendable(segments, is_group=is_group)
            required = {"text": "text.send", "at": "group.text.mention", "image": "image.send",
                        "file": "file.send", "record": "voice.send", "face": "emoticon.send"}
            for segment in segments:
                if required[segment.type] not in self.driver.capabilities:
                    raise OneBotActionError(1404, f"当前驱动不支持 {segment.type} 发送")
            pending_text: list[str] = []
            has_at = False

            def count_text():
                nonlocal estimated_sends, has_at
                text = "".join(pending_text)
                if text or has_at:
                    estimated_sends += len(_split_utf8(text or " ", 900 if has_at else 1023))
                pending_text.clear()
                has_at = False

            for segment in segments:
                if segment.type in ("image", "file", "record", "face"):
                    count_text()
                    estimated_sends += 1
                elif segment.type == "text":
                    pending_text.append(segment.data["text"])
                else:
                    has_at = True
            count_text()
            if estimated_sends > forwarding.MAX_NATIVE_SENDS:
                raise OneBotActionError(1400, "转发发送超出媒体或分段额度")
            prepared.append(segments)
        ids: list[int] = []
        sent = 0
        async with self._send_lock:
            try:
                for segments in prepared:
                    message_id, count = await self._send_prepared_message(chat_id, segments, is_group=is_group)
                    ids.append(message_id)
                    sent += count
            except OneBotActionError as error:
                partial = int((error.data or {}).get("sent_segments", 0))
                error.data = {"forward_mode": "normal_messages", "native_forward": False,
                              "message_ids": ids, "sent_segments": sent + partial}
                raise
        return {"message_id": ids[0], "message_ids": ids, "forward_mode": "normal_messages",
                "native_forward": False, "node_count": plan.node_count, "sent_segments": sent}

    async def _send_message(self, chat_id: str, raw_message: Any, *, is_group: bool,
                            auto_escape: bool = False) -> int:
        if self._stopping_actions or len(self._action_tasks) >= 1000:
            raise OneBotActionError(1500, "服务正在关闭或待处理动作过多")
        task = self._background(self._send_message_owned(chat_id, raw_message,
                                                        is_group=is_group, auto_escape=auto_escape))
        return await asyncio.shield(task)

    async def _send_message_owned(self, chat_id: str, raw_message: Any, *, is_group: bool,
                                 auto_escape: bool = False) -> int:
        segments = await self._prepare_segments(raw_message, is_group=is_group, auto_escape=auto_escape)
        self._validate_sendable(segments, is_group=is_group)
        async with self._send_lock:
            message_id, _ = await self._send_prepared_message(chat_id, segments, is_group=is_group)
            return message_id

    @staticmethod
    def _validate_sendable(segments: list[PreparedSegment], *, is_group: bool) -> None:
        if not any(segment.type in ("image", "file", "record", "face", "at") or
                   (segment.type == "text" and segment.data["text"]) for segment in segments):
            raise OneBotActionError(1400, "消息没有可发送的内容")
        if any(segment.type == "at" for segment in segments) and not is_group:
            raise OneBotActionError(1400, "私聊消息不支持 at 段")

    async def _send_prepared_message(self, chat_id: str, segments: list[PreparedSegment],
                                     *, is_group: bool) -> tuple[int, int]:
        sent = 0
        pending_text: list[str] = []
        pending_at_ids: list[str] = []
        pending_at_names: list[str] = []
        members_loaded = False

        async def flush_text() -> None:
            nonlocal sent, members_loaded
            text = "".join(pending_text)
            at_ids = tuple(pending_at_ids)
            display = tuple(pending_at_names)
            pending_text.clear()
            pending_at_ids.clear()
            pending_at_names.clear()
            if not text and not at_ids:
                return
            if at_ids and not members_loaded:
                members_loaded = True
                try:
                    rows = await self.driver.get_group_member_list(chat_id)
                except DriverError:
                    rows = None
                if rows is not None:
                    self._remember_members(chat_id, rows)
            names: list[str] = []
            sources: list[str] = []
            fallback_labels: list[str] = []
            for external, supplied in zip(at_ids, display):
                manual = self.message_store.mention_label(
                    self.account, external, chat_id)
                if manual:
                    names.append(manual)
                    sources.append("manual")
                    fallback_labels.append(manual)
                    continue
                cached = ""
                try:
                    cached = await self.driver.resolve_mention_label(chat_id, external) or ""
                except DriverError:
                    cached = ""
                if cached:
                    names.append(cached)
                    sources.append("cache")
                    fallback_labels.append(cached)
                    continue
                profile = self._display_name(external, chat_id)
                if profile:
                    names.append(profile)
                    sources.append("profile")
                    fallback_labels.append(profile)
                    continue
                names.append("")
                sources.append("none")
                fallback_labels.append(supplied or external)
            has_manual = "manual" in sources
            attempt_native = False
            display_names: tuple[str, ...] | None = None
            labels_verified = True
            if at_ids:
                if not all(names):
                    # Without a manual label the SDK may still resolve all
                    # targets from its cache; with a manual label an incomplete
                    # set must not turn the other names into unverified labels.
                    attempt_native = not has_manual
                elif not has_manual:
                    attempt_native = True
                    display_names = tuple(names)
                elif all(source in ("manual", "cache") for source in sources):
                    attempt_native = True
                    display_names = tuple(names)
                    labels_verified = False
                else:
                    # A profile/history name is not proof of the current
                    # display name. Mixing it with manual labels under
                    # labels_verified=False would look like D组: native
                    # completed but no real @. Degrade the whole batch.
                    logger.warning("多目标 @ 名称来源混合，降级为普通文本")
            chunks = list(_split_utf8(text or " ", 900 if at_ids else 1023))
            for index, chunk in enumerate(chunks):
                if at_ids and index == 0:
                    accepted = False
                    if attempt_native:
                        try:
                            accepted = await self.driver.send_at_text(
                                chat_id, chunk, at_ids, display_names,
                                labels_verified=labels_verified)
                        except DriverError as error:
                            if not _mention_validation_error(error):
                                raise
                            # SDK native mentions require a verified/real
                            # display name. When neither the cache nor a manual
                            # binding has one, keep the reply usable by sending
                            # visible text instead of failing the chain.
                            logger.warning("原生 @ 发送失败，降级为普通文本: %s", error)
                    if accepted:
                        sent += 1
                        continue
                    prefix = " ".join(f"@{name}" for name in fallback_labels) + " "
                    for piece in _split_utf8(prefix + chunk, 1023):
                        if not await self.driver.send_text(chat_id, piece):
                            raise OneBotActionError(1500, "微信原生发送返回失败", {"sent_segments": sent})
                        sent += 1
                    continue
                if not await self.driver.send_text(chat_id, chunk):
                    raise OneBotActionError(1500, "微信原生发送返回失败", {"sent_segments": sent})
                sent += 1

        try:
            for segment in segments:
                if segment.type == "text":
                    pending_text.append(str(segment.data.get("text") or ""))
                elif segment.type == "at":
                    pending_at_ids.append(str(segment.data["qq"]))
                    pending_at_names.append(str(segment.data.get("name") or ""))
                elif segment.type == "image":
                    await flush_text()
                    if not await self.driver.send_image(chat_id, str(segment.data["path"])):
                        raise OneBotActionError(1500, "图片发送失败", {"sent_segments": sent})
                    sent += 1
                elif segment.type == "file":
                    await flush_text()
                    if not await self.driver.send_file(chat_id, str(segment.data["path"])):
                        raise OneBotActionError(1500, "文件发送失败", {"sent_segments": sent})
                    sent += 1
                elif segment.type == "record":
                    await flush_text()
                    if not await self.driver.send_voice(chat_id, str(segment.data["path"])):
                        raise OneBotActionError(1500, "语音发送失败", {"sent_segments": sent})
                    sent += 1
                elif segment.type == "face":
                    await flush_text()
                    if not await self.driver.send_emoticon(chat_id, str(segment.data["path"])):
                        raise OneBotActionError(1500, "表情发送失败", {"sent_segments": sent})
                    sent += 1
                else:
                    raise OneBotActionError(1404, f"不支持的 OneBot 消息段: {segment.type}")
            await flush_text()
        except OneBotActionError as error:
            if error.data is None and sent:
                error.data = {"sent_segments": sent}
            raise
        except DriverError as error:
            code = 1404 if isinstance(error, DriverUnsupported) else 1500
            raise OneBotActionError(code, str(error), {"sent_segments": sent}) from error
        except Exception as error:
            logger.exception("普通消息发送期间发生内部错误")
            raise OneBotActionError(1500, "消息发送期间发生内部错误",
                                    {"sent_segments": sent}) from error
        native_ids = self.driver.take_native_message_ids()
        try:
            message_id = self.message_store.allocate_sent_id(
                account=self.account,
                session_key=chat_id,
                nonce=uuid.uuid4().hex,
            )
            snapshot = IncomingMessage(
                external_chat_id=chat_id,
                external_sender_id=self.mapper.reverse("user", self.self_id),
                is_group=is_group,
                segments=tuple(Segment(item.type, dict(item.data)) for item in segments),
                timestamp=int(time.time()))
            sent_event = message_event(snapshot, self.mapper, self.media, self.self_id, message_id=message_id)
            sent_event["post_type"] = "message_sent"  # Internal history only; not broadcast as a standard event.
            if not is_group:
                sent_event["target_id"] = self.mapper.get_or_create("user", chat_id)
            self.message_store.put(
                message_id,
                account=self.account,
                event_name="private_message_sent",
                session_key=chat_id,
                payload=sent_event,
            )
            for native_id in native_ids:
                if native_id.isdigit():
                    self.message_store.remember_native_id(
                        self.account, chat_id, native_id, message_id)
        except Exception as error:
            logger.exception("发送后保存消息记录失败")
            raise OneBotActionError(1500, "微信发送已执行，但消息记录保存失败",
                                    {"sent_segments": sent}) from error
        return message_id, sent

    async def _prepare_segments(self, raw_message: Any, *, is_group: bool,
                                auto_escape: bool = False,
                                media_budget: dict[str, int] | None = None) -> list[PreparedSegment]:
        raw_segments = normalize_message(raw_message, auto_escape=auto_escape)
        prepared: list[PreparedSegment] = []
        for raw in raw_segments:
            if not isinstance(raw, dict):
                raise OneBotActionError(1400, "消息段必须是 JSON 对象")
            segment_type = raw.get("type")
            data = raw.get("data") or {}
            if not isinstance(segment_type, str) or not isinstance(data, dict):
                raise OneBotActionError(1400, "消息段必须包含 type 和 data")
            if segment_type == "reply":
                raise OneBotActionError(1404, "当前原生桥接不支持引用回复；消息未发送")
            if segment_type in ("node", "forward", "json", "xml", "video", "contact"):
                raise OneBotActionError(1404, f"当前原生桥接不支持发送 {segment_type} 段")
            if segment_type == "music":
                prepared.extend(await self._prepare_music(
                    data, is_group=is_group, media_budget=media_budget))
            elif segment_type == "text":
                text = data.get("text")
                if not isinstance(text, str):
                    raise OneBotActionError(1400, "text 段的 text 必须是字符串")
                prepared.append(PreparedSegment("text", {"text": text}))
            elif segment_type == "at":
                if not is_group:
                    raise OneBotActionError(1400, "私聊消息不能包含 at 段")
                qq = data.get("qq")
                if str(qq) == "all":
                    raise OneBotActionError(1404, "helper 原生桥接不支持 @全体成员")
                user_number = self._required_int({"qq": qq}, "qq")
                external = self._external_user(user_number)
                prepared.append(PreparedSegment("at", {
                    "qq": external,
                    "name": str(data.get("name") or ""),
                }))
            elif segment_type in ("image", "file", "record", "face"):
                if media_budget is not None and media_budget["count"] >= forwarding.MAX_MEDIA:
                    raise OneBotActionError(1400, "转发发送超出媒体额度")
                file_value = data.get("file")
                if not file_value:
                    raise OneBotActionError(1404, f"{segment_type} 段缺少 file，且当前桥接不支持 QQ 表情 ID 映射")
                if segment_type == "image" and data.get("type"):
                    raise OneBotActionError(1404, "当前原生桥接不支持闪照")
                if segment_type == "record" and str(data.get("magic", "0")).lower() not in ("0", "false", "no"):
                    raise OneBotActionError(1404, "当前原生桥接不支持变声")
                options = {}
                for key in ("cache", "proxy"):
                    value = str(data.get(key, "1")).lower()
                    if value not in ("0", "1", "true", "false", "yes", "no"):
                        raise OneBotActionError(1400, f"{key} 必须是布尔值")
                    options[key] = value in ("1", "true", "yes")
                if "timeout" in data:
                    options["timeout"] = float(data["timeout"])
                if media_budget is not None:
                    remaining = forwarding.MAX_MEDIA_BYTES - media_budget["bytes"]
                    if remaining <= 0:
                        raise OneBotActionError(1400, "转发发送超出媒体字节额度")
                    options["max_bytes"] = remaining
                try:
                    path = await self.media.resolve_input(str(file_value), **options)
                    if segment_type == "record":
                        # WeChat voice bubbles accept only Tencent SILK v3 and at
                        # most 60 seconds each. Decode/split here so long or
                        # high-bitrate audio is delivered as native voice
                        # bubbles instead of a file.
                        try:
                            chunks = await asyncio.to_thread(
                                ensure_silk_chunks, path,
                                cache_dir=self.media.media_dir / "cache" / "voice",
                                max_bytes=self.media.max_bytes,
                            )
                        except VoiceError as error:
                            raise OneBotActionError(1400, f"语音转换失败: {error}") from error
                        for chunk in chunks:
                            if media_budget is not None:
                                media_budget["count"] += 1
                                media_budget["bytes"] += chunk.stat().st_size
                                if media_budget["bytes"] > forwarding.MAX_MEDIA_BYTES:
                                    raise OneBotActionError(1400, "转发发送超出媒体字节额度")
                                await self.driver.validate_media("record", str(chunk))
                            prepared.append(PreparedSegment("record", {"path": str(chunk)}))
                        continue
                    if media_budget is not None:
                        media_budget["count"] += 1
                        media_budget["bytes"] += path.stat().st_size
                        if media_budget["bytes"] > forwarding.MAX_MEDIA_BYTES:
                            raise OneBotActionError(1400, "转发发送超出媒体字节额度")
                        await self.driver.validate_media(segment_type, str(path))
                except MediaError as error:
                    raise OneBotActionError(1400, str(error)) from error
                prepared.append(PreparedSegment(segment_type, {"path": str(path)}))
            else:
                raise OneBotActionError(1404, f"不支持的 OneBot 消息段: {segment_type}")
        return prepared

    async def _prepare_music(self, data: dict[str, Any], *, is_group: bool,
                             media_budget: dict[str, int] | None) -> list[PreparedSegment]:
        """Degrade a standard music segment to media WeChat can actually send."""
        audio = data.get("audio")
        if audio:
            return await self._prepare_segments(
                [{"type": "record", "data": {"file": str(audio)}}],
                is_group=is_group, media_budget=media_budget)
        url = str(data.get("url") or "").strip()
        if url:
            title = str(data.get("title") or data.get("content") or "音乐").strip()
            return [PreparedSegment("text", {"text": f"{title}\n{url}"})]
        raise OneBotActionError(1404, "music 段缺少可播放的 audio 或 url")

    async def _get_login_info(self, params: dict[str, Any]) -> dict[str, Any]:
        info = await self.driver.get_login_info()
        return {"user_id": self.mapper.get_or_create("user", info.external_user_id), "nickname": info.nickname}

    async def _get_status(self, params: dict[str, Any]) -> dict[str, Any]:
        status = await self.driver.get_status()
        return {"online": status.online, "good": status.good, "message": status.message, "details": status.details}

    async def _get_version_info(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "app_name": self.config.app_name,
            "app_version": self.config.version,
            "protocol_version": "v11",
            "version": f"{self.config.app_name}/{self.config.version}",
        }

    async def _get_friend_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows = await self.driver.get_friend_list()
        self.message_store.remember_profiles(self.account, [
            (row.external_user_id, "", row.nickname, "", "friend") for row in rows])
        return [
            {
                "user_id": self.mapper.get_or_create("user", row.external_user_id),
                "nickname": row.nickname,
                "remark": row.remark,
            }
            for row in rows
        ]

    async def _get_group_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        rows = await self.driver.get_group_list()
        return [
            {
                "group_id": self.mapper.get_or_create("group", row.external_group_id),
                "group_name": row.group_name,
                "member_count": row.member_count,
                "max_member_count": row.max_member_count,
            }
            for row in rows
        ]

    async def _get_group_info(self, params: dict[str, Any]) -> dict[str, Any]:
        group_number = self._required_int(params, "group_id")
        external = self._external_group(group_number)
        rows = await self.driver.get_group_list()
        row = next((item for item in rows if item.external_group_id == external), None)
        if row is None:
            raise OneBotActionError(1404, "群不存在或当前不可读取")
        return {
            "group_id": group_number,
            "group_name": row.group_name,
            "member_count": row.member_count,
            "max_member_count": row.max_member_count,
        }

    async def _get_group_member_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        group_number = self._required_int(params, "group_id")
        external = self._external_group(group_number)
        rows = await self.driver.get_group_member_list(external)
        self._remember_members(external, rows)
        result = []
        for row in rows:
            profile = self.message_store.profile(self.account, row.external_user_id, external)
            result.append(self._member_defaults({
                "group_id": group_number,
                "user_id": self.mapper.get_or_create("user", row.external_user_id),
                "nickname": profile["nickname"],
                "card": profile["card"],
                "role": row.role,
            }))
        return result

    @staticmethod
    def _member_defaults(result: dict[str, Any]) -> dict[str, Any]:
        unknown = {"sex": "unknown", "age": 0, "area": "", "join_time": 0,
                   "last_sent_time": 0, "level": "", "unfriendly": False, "title": "",
                   "title_expire_time": 0, "card_changeable": False}
        unknown_fields = list(unknown)
        if result.get("role") not in ("owner", "admin", "member"):
            result["role"] = "member"
            unknown_fields.append("role")
        return {**unknown, **result, "unavailable_fields": unknown_fields}

    async def _get_group_member_info(self, params: dict[str, Any]) -> dict[str, Any]:
        group_number = self._required_int(params, "group_id")
        user_number = self._required_int(params, "user_id")
        external_group = self._external_group(group_number)
        external_user = self._external_user(user_number)
        try:
            rows = await self.driver.get_group_member_list(external_group)
        except DriverError:
            rows = None
        if rows is not None:
            self._remember_members(external_group, rows)
        row = next((item for item in rows or () if item.external_user_id == external_user), None)
        profile = self.message_store.profile(self.account, external_user, external_group)
        # A global contact alone is not evidence of membership in this group.
        membership = self.message_store.has_membership(self.account, external_user, external_group)
        if row is None and (rows is not None or not membership):
            raise OneBotActionError(1404, "群成员不存在或成员缓存尚未就绪")
        result = {
            "group_id": group_number,
            "user_id": user_number,
            "nickname": profile["nickname"],
            "card": profile["card"],
        }
        if row is not None:
            result["role"] = row.role
        return self._member_defaults(result)

    def _remember_members(self, group: str, rows: Sequence[GroupMemberInfo]) -> None:
        profiles = []
        for row in rows:
            profiles.extend([(row.external_user_id, "", row.nickname, "", "group_member"),
                             (row.external_user_id, group, "", row.card, "group_member")])
        self.message_store.remember_profiles(self.account, profiles)
        self.message_store.replace_memberships(self.account, group, [row.external_user_id for row in rows])

    def _display_name(self, user: str, group: str) -> str:
        profile = self.message_store.profile(self.account, user, group)
        name = (profile["card"] or profile["nickname"]) if profile else ""
        return "".join(" " if ord(ch) < 32 else ch for ch in name).strip()[:64]

    @staticmethod
    def _mention_label_value(value: Any) -> str:
        if not isinstance(value, str):
            raise OneBotActionError(1400, "显示名必须是字符串")
        label = "".join(" " if ord(ch) < 32 else ch for ch in value).strip()
        if not 1 <= len(label) <= 64:
            raise OneBotActionError(1400, "显示名必须为 1–64 个可见字符")
        return label

    def set_mention_label(self, group_number: int, user_number: int,
                          label: Any) -> dict[str, Any]:
        external_group = self._external_group(group_number)
        external_user = self._external_user(user_number)
        value = self._mention_label_value(label)
        if value == external_user:
            raise OneBotActionError(1400, "显示名不能是 wxid；请填写群内真实显示名")
        self.message_store.set_mention_label(
            self.account, external_user, external_group, value)
        return {"group_id": group_number, "user_id": user_number, "label": value}

    def remove_mention_label(self, group_number: int, user_number: int) -> dict[str, Any]:
        external_group = self._external_group(group_number)
        external_user = self._external_user(user_number)
        self.message_store.delete_mention_label(
            self.account, external_user, external_group)
        return {"group_id": group_number, "user_id": user_number, "deleted": True}

    def mention_labels(self, group_number: int | None = None) -> list[dict[str, Any]]:
        external_group = (self._external_group(group_number)
                          if group_number is not None else "")
        rows = self.message_store.list_mention_labels(self.account, external_group)
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                group = self.mapper.get_or_create("group", row["group"])
                user = self.mapper.get_or_create("user", row["user"])
            except ValueError:
                continue
            result.append({"group_id": group, "user_id": user, "label": row["label"]})
        return result

    async def _get_stranger_info(self, params: dict[str, Any]) -> dict[str, Any]:
        number = self._required_int(params, "user_id")
        user = self._external_user(number)
        profile = self.message_store.profile(self.account, user)
        if params.get("no_cache", False) or not (profile and profile["nickname"]):
            try:
                await self._get_friend_list({})
            except DriverError:
                pass
            profile = self.message_store.profile(self.account, user)
        if profile is None:
            raise OneBotActionError(1404, "尚未观测到此用户的资料")
        return {"user_id": number, "nickname": profile["nickname"], "sex": "unknown", "age": 0,
                "unavailable_fields": ["sex", "age"]}

    def _history_params(self, params: dict[str, Any]) -> tuple[int, int | None, int, bool]:
        count = int(params.get("count", 20))
        if not 1 <= count <= 100:
            raise OneBotActionError(1400, "count 必须在 1–100 之间")
        message_id = None
        if params.get("message_id") is not None:
            message_id = self._required_int(params, "message_id", allow_negative=True)
        reverse_order = params.get("reverse_order", False)
        if type(reverse_order) is not bool:
            raise OneBotActionError(1400, "reverse_order 必须是布尔值")
        return count, message_id, count, reverse_order

    async def _get_friend_msg_history(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        user_number = self._required_int(params, "user_id")
        external = self._external_user(user_number)
        count, message_id, _, reverse_order = self._history_params(params)
        rows = self.message_store.history(
            account=self.account, session_key=external, count=count, message_id=message_id,
            reverse_order=reverse_order)
        return await asyncio.to_thread(self._refresh_history_media, rows)

    async def _get_group_msg_history(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        group_number = self._required_int(params, "group_id")
        external = self._external_group(group_number)
        count, message_id, _, reverse_order = self._history_params(params)
        rows = self.message_store.history(
            account=self.account, session_key=external, count=count, message_id=message_id,
            reverse_order=reverse_order)
        return await asyncio.to_thread(self._refresh_history_media, rows)

    def _refresh_history_media(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # One refresh call shares verified resolutions throughout this batch.
        result = self.media.refresh_payload(rows)
        for row in result:
            row["raw_message"] = serialize_message(row["message"])
        return result

    def _refresh_message_media(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.media.refresh_payload(payload)
        if "message" in result:
            result["raw_message"] = serialize_message(result["message"])
        return result

    async def _get_msg(self, params: dict[str, Any]) -> dict[str, Any]:
        message_id = self._required_int(params, "message_id", allow_negative=True)
        event = self.message_store.get(message_id, account=self.account)
        if event is None:
            raise OneBotActionError(1404, "消息不在当前账号的消息存储中")
        result = await asyncio.to_thread(self._refresh_message_media, event)
        result.pop("post_type", None)
        result.pop("self_id", None)
        result["real_id"] = message_id
        return result

    async def _can_send_image(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"yes": self.driver.online and "image.send" in self.driver.capabilities}

    async def _can_send_record(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"yes": self.driver.online and "voice.send" in self.driver.capabilities}

    def _received_media_path(self, params: dict[str, Any], label: str) -> Path:
        value = params.get("file")
        if not isinstance(value, str) or not value.strip():
            raise OneBotActionError(1400, "file 必须是非空字符串")
        path = self.media.received_path(value)
        if path is None:
            raise OneBotActionError(1404, f"{label}不在当前账号的媒体存储中")
        return path

    async def _get_image(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"file": str(self._received_media_path(params, "图片"))}

    async def _get_record(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._received_media_path(params, "语音")
        out_format = params.get("out_format")
        try:
            converted = await asyncio.to_thread(
                export_format, path, out_format,
                cache_dir=self.media.media_dir / "cache" / "voice-export")
        except VoiceError as error:
            raise OneBotActionError(1400, f"语音转换失败: {error}") from error
        return {"file": str(converted)}

    async def _get_capabilities(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "driver": self.config.driver.type,
            "online": self.driver.online,
            "capabilities": sorted(self.driver.capabilities),
            "actions": sorted(self.supported_actions),
            "action_catalog": action_catalog(),
            "forward_send": {"mode": "normal_messages", "native_forward": False,
                             "max_nodes": forwarding.MAX_NODES, "max_depth": forwarding.MAX_DEPTH,
                             "max_segments": forwarding.MAX_SEGMENTS, "max_text_bytes": forwarding.MAX_TEXT_BYTES,
                             "max_media": forwarding.MAX_MEDIA, "max_media_bytes": forwarding.MAX_MEDIA_BYTES,
                             "max_estimated_sends": forwarding.MAX_NATIVE_SENDS},
        }


def _split_utf8(text: str, max_bytes: int) -> Sequence[str]:
    if not text:
        return ("",)
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for character in text:
        encoded = character.encode("utf-8")
        if size + len(encoded) > max_bytes and current:
            chunks.append("".join(current))
            current = []
            size = 0
        current.append(character)
        size += len(encoded)
    if current:
        chunks.append("".join(current))
    return tuple(chunks)


def _mention_validation_error(error: BaseException) -> bool:
    message = str(error)
    return ("NOT_VERIFIED" in message or "No verified name" in message
            or "INVALID_ARGUMENT" in message)
