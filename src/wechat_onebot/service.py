from __future__ import annotations

import asyncio
import copy
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .config import AppConfig
from .driver.base import Driver, DriverError, DriverUnsupported
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
from .protocol.rich_content import materialize_forwards, rich_segments


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

    @property
    def supported_actions(self) -> frozenset[str]:
        return frozenset(ACTION_REGISTRY)

    def set_event_sink(self, sink: EventSink | None) -> None:
        self._event_sink = sink

    async def start(self) -> None:
        if self.started:
            return
        login = await self.driver.get_login_info()
        if not login.external_user_id:
            raise DriverError("微信账号 ID 为空，无法启动 OneBot")
        self.self_id = self.mapper.get_or_create("user", login.external_user_id)
        self.message_store.remember_profiles(self.account, [
            (login.external_user_id, "", login.nickname, "", "login")])
        await self.driver.start(self._on_driver_event)
        self.started = True

    async def stop(self) -> None:
        if not self.started:
            if self._owns_message_store:
                self.message_store.close()
            return
        self.started = False
        try:
            await self.driver.stop()
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
            payload = notice_event(event, self.mapper, self.self_id)
        elif isinstance(event, RequestEvent):
            payload = request_event(event, self.mapper, self.self_id)
        else:
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
        return payload

    async def dispatch(self, action: str, params: dict[str, Any] | None = None, echo: Any = None) -> dict[str, Any]:
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return failure(1400, "params 必须是 JSON 对象", echo)
        try:
            definition = ACTION_REGISTRY.get(action)
            if definition is None:
                return failure(1404, f"不支持 OneBot 动作: {action}", echo)
            definition.validate(params)
            handler = getattr(self, definition.handler, None)
            if not callable(handler):
                raise RuntimeError(f"动作注册表指向不存在的处理器: {definition.handler}")
            data = await handler(params)
            return ok(data, echo)
        except ActionValidationError as error:
            return failure(1400, str(error), echo)
        except OneBotActionError as error:
            return failure(error.retcode, str(error), echo, error.data)
        except DriverUnsupported as error:
            return failure(1404, str(error), echo)
        except DriverError as error:
            return failure(1500, str(error), echo)
        except (ValueError, TypeError) as error:
            return failure(1400, str(error), echo)
        except Exception as error:
            import logging
            logging.getLogger("wechat-onebot.actions").exception("OneBot 动作失败: %s", action)
            return failure(1500, f"内部错误: {error}", echo)

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
        message_id = await self._send_message(chat_id, params.get("message"), is_group=False)
        return {"message_id": message_id}

    async def _send_group_msg(self, params: dict[str, Any]) -> dict[str, Any]:
        group_id = self._required_int(params, "group_id")
        chat_id = self._external_group(group_id)
        message_id = await self._send_message(chat_id, params.get("message"), is_group=True)
        return {"message_id": message_id}

    async def _send_msg(self, params: dict[str, Any]) -> dict[str, Any]:
        message_type = params.get("message_type")
        if message_type == "group" or params.get("group_id") is not None:
            return await self._send_group_msg(params)
        if message_type == "private" or params.get("user_id") is not None:
            return await self._send_private_msg(params)
        raise OneBotActionError(1400, "send_msg 需要 message_type 或 user_id/group_id")

    async def _send_message(self, chat_id: str, raw_message: Any, *, is_group: bool) -> int:
        segments = await self._prepare_segments(raw_message, is_group=is_group)
        if not any(segment.type in ("image", "file", "record", "face") or
                   (segment.type == "text" and segment.data["text"]) for segment in segments):
            raise OneBotActionError(1400, "消息没有可发送的内容")
        if any(segment.type == "at" for segment in segments) and not is_group:
            raise OneBotActionError(1400, "私聊消息不支持 at 段")
        if any(segment.type == "at" for segment in segments) and not any(
                segment.type == "text" and segment.data.get("text") for segment in segments):
            raise OneBotActionError(1400, "at 段后必须包含非空 text 段")
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
            names = tuple(
                supplied or self._display_name(external, chat_id) or external
                for external, supplied in zip(at_ids, display)
            ) if at_ids else None
            chunks = list(_split_utf8(text or " ", 900 if at_ids else 1023))
            for index, chunk in enumerate(chunks):
                if at_ids and index == 0:
                    accepted = await self.driver.send_at_text(chat_id, chunk, at_ids, names)
                else:
                    accepted = await self.driver.send_text(chat_id, chunk)
                if not accepted:
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
        message_id = self.message_store.allocate_sent_id(
            account=self.account,
            session_key=chat_id,
            nonce=uuid.uuid4().hex,
        )
        sent_event = {
            "time": int(time.time()),
            "self_id": self.self_id,
            "post_type": "message_sent",
            "message_type": "group" if is_group else "private",
            "sub_type": "normal" if is_group else "friend",
            "message_id": message_id,
            "group_id": self.mapper.get_or_create("group", chat_id) if is_group else None,
            "user_id": self.mapper.get_or_create("user", self.account),
            "target_id": None if is_group else self.mapper.get_or_create("user", chat_id),
            "message": [{"type": "text", "data": {"text": "[sent]"} }],
            "raw_message": "",
        }
        if is_group:
            sent_event.pop("target_id", None)
        else:
            sent_event.pop("group_id", None)
        self.message_store.put(
            message_id,
            account=self.account,
            event_name="private_message_sent",
            session_key=chat_id,
            payload=sent_event,
        )
        return message_id

    async def _prepare_segments(self, raw_message: Any, *, is_group: bool) -> list[PreparedSegment]:
        if isinstance(raw_message, str):
            raw_segments = [{"type": "text", "data": {"text": raw_message}}]
        elif isinstance(raw_message, list):
            raw_segments = raw_message
        else:
            raise OneBotActionError(1400, "message 必须是字符串或消息段数组")
        prepared: list[PreparedSegment] = []
        for raw in raw_segments:
            if not isinstance(raw, dict):
                raise OneBotActionError(1400, "消息段必须是 JSON 对象")
            segment_type = raw.get("type")
            data = raw.get("data") or {}
            if not isinstance(segment_type, str) or not isinstance(data, dict):
                raise OneBotActionError(1400, "消息段必须包含 type 和 data")
            if segment_type == "reply":
                # The native bridge has no quoted-reply send primitive. Keep other
                # segments, especially a real group @, instead of failing the chain.
                continue
            if segment_type in ("node", "forward", "json", "xml", "video", "contact", "music"):
                raise OneBotActionError(1404, f"当前原生桥接不支持发送 {segment_type} 段")
            if segment_type == "text":
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
                file_value = data.get("file")
                if not file_value:
                    raise OneBotActionError(1404, f"{segment_type} 段缺少 file，且当前桥接不支持 QQ 表情 ID 映射")
                try:
                    path = await self.media.resolve_input(str(file_value))
                except MediaError as error:
                    raise OneBotActionError(1400, str(error)) from error
                prepared.append(PreparedSegment(segment_type, {"path": str(path)}))
            else:
                raise OneBotActionError(1404, f"不支持的 OneBot 消息段: {segment_type}")
        return prepared

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
            return {"group_id": group_number, "group_name": "", "member_count": 0, "max_member_count": 0}
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
            result.append({
                "group_id": group_number,
                "user_id": self.mapper.get_or_create("user", row.external_user_id),
                "nickname": profile["nickname"],
                "card": profile["card"],
                "role": row.role,
            })
        return result

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
        return result

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
        return {"user_id": number, "nickname": profile["nickname"]}

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
        return self.message_store.history(account=self.account, session_key=external,
                                          count=count, message_id=message_id,
                                          reverse_order=reverse_order)

    async def _get_group_msg_history(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        group_number = self._required_int(params, "group_id")
        external = self._external_group(group_number)
        count, message_id, _, reverse_order = self._history_params(params)
        return self.message_store.history(account=self.account, session_key=external,
                                          count=count, message_id=message_id,
                                          reverse_order=reverse_order)

    async def _get_msg(self, params: dict[str, Any]) -> dict[str, Any]:
        message_id = self._required_int(params, "message_id", allow_negative=True)
        event = self.message_store.get(message_id, account=self.account)
        if event is None:
            raise OneBotActionError(1404, "消息不在当前账号的消息存储中")
        result = dict(event)
        result.pop("post_type", None)
        result.pop("self_id", None)
        result["real_id"] = message_id
        return result

    async def _can_send_image(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"yes": self.driver.online and "image.send" in self.driver.capabilities}

    async def _can_send_record(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"yes": self.driver.online and "voice.send" in self.driver.capabilities}

    async def _get_capabilities(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "driver": self.config.driver.type,
            "online": self.driver.online,
            "capabilities": sorted(self.driver.capabilities),
            "actions": sorted(self.supported_actions),
            "action_catalog": action_catalog(),
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
