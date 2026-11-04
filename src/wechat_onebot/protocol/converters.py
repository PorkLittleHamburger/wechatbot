from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .codec import serialize_message
from ..ids import IdMapper
from ..media import MediaError, MediaStore
from ..models import IncomingMessage, NoticeEvent, RequestEvent, Segment


def ok(data: Any = None, echo: Any = None) -> dict[str, Any]:
    response = {"status": "ok", "retcode": 0, "data": data}
    if echo is not None:
        response["echo"] = echo
    return response


def failure(retcode: int, message: str, echo: Any = None, data: Any = None) -> dict[str, Any]:
    response = {"status": "failed", "retcode": retcode, "data": data, "message": message}
    if echo is not None:
        response["echo"] = echo
    return response


logger = logging.getLogger(__name__)


def _segment_to_onebot(segment: Segment, mapper: IdMapper, media_store: MediaStore) -> dict[str, Any]:
    data = dict(segment.data)
    kind = segment.type
    if kind == "text":
        return {"type": "text", "data": {"text": str(data.get("text", ""))}}
    if kind == "at":
        qq = str(data.get("qq", ""))
        if not qq:
            return {"type": "text", "data": {"text": "[提及对象不可用]"}}
        return {"type": "at", "data": {"qq": "all" if qq == "all" else str(mapper.get_or_create("user", qq))}}
    if kind == "face" and (data.get("file") or data.get("path")):
        kind = "image"  # WeChat stickers do not carry a QQ face ID.
    if kind == "face":
        if data.get("id") is None:
            return {"type": "text", "data": {"text": "[表情内容不可用]"}}
        return {"type": "face", "data": {"id": str(data["id"])}}
    if kind == "file":
        name = str(data.get("name") or Path(str(data.get("file") or data.get("path") or "内容不可用")).name)
        # A downloadable URL in ordinary text works for every standard consumer.
        text = "[文件] " + name
        file_value = data.get("file") or data.get("path")
        try:
            if not file_value:
                raise MediaError("missing inbound file")
            record = media_store.register(str(file_value))
            text += "\n" + media_store.url_for(record)
        except (MediaError, OSError):
            text += "\n[文件内容不可用]"
        return {"type": "text", "data": {"text": text}}
    if kind in ("image", "record", "video"):
        file_value = data.get("file") or data.get("path")
        allowed = {"file", "url", "type"} if kind == "image" else {"file", "url", "magic"} if kind == "record" else {"file", "url"}
        data = {key: str(value) for key, value in data.items() if key in allowed and value is not None}
        if file_value:
            try:
                record = media_store.register(str(file_value))
                data["file"] = record.media_id
                data["url"] = media_store.url_for(record)
            except (MediaError, OSError):
                return {"type": "text", "data": {"text": {"image": "[图片内容不可用]", "record": "[语音内容不可用]", "video": "[视频内容不可用]"}[kind]}}
        if not data.get("file"):
            return {"type": "text", "data": {"text": "[媒体内容不可用]"}}
    if kind == "forward" and "records" in data:
        return {"type": kind, "data": data}  # Internal only, replaced before storage/reporting.
    return {"type": kind, "data": {key: str(value) for key, value in data.items() if value is not None and not isinstance(value, (dict, list))}}


def message_event(
    message: IncomingMessage,
    mapper: IdMapper,
    media_store: MediaStore,
    self_id: int,
    *,
    message_id: int | None = None,
) -> dict[str, Any]:
    user_id = mapper.get_or_create("user", message.external_sender_id or message.external_chat_id)
    if message_id is None:
        raise ValueError("message_id is required; allocate it in the OneBot message store")
    event: dict[str, Any] = {
        "time": message.timestamp or int(time.time()),
        "self_id": self_id,
        "post_type": "message",
        "message_type": "group" if message.is_group else "private",
        "sub_type": "normal" if message.is_group else "friend",
        "message_id": message_id,
        "message_seq": message.sequence,
        "user_id": user_id,
        "message": [_segment_to_onebot(segment, mapper, media_store) for segment in message.segments],
        "raw_message": "",
        "font": 0,
        "sender": {
            "user_id": user_id,
            "nickname": message.sender_name,
            "card": message.sender_card,
        },
    }
    # Internal forward trees are materialized by the service before final CQ refresh.
    event["raw_message"] = serialize_message([
        {"type": "text", "data": {"text": "[聊天记录]"}}
        if row["type"] == "forward" and "records" in row["data"] else row
        for row in event["message"]
    ])
    if message.is_group:
        event["sender"]["role"] = message.sender_role if message.sender_role in ("owner", "admin", "member") else "member"
        event["group_id"] = mapper.get_or_create("group", message.external_chat_id)
        event["anonymous"] = None
        if message.group_name:
            event["group_name"] = message.group_name
    if message.raw.get("is_at_self"):
        event["to_me"] = True
    return event


def notice_event(
    notice: NoticeEvent, mapper: IdMapper, self_id: int, *, message_id: int | None = None,
) -> dict[str, Any] | None:
    """Return a standard notice only when its required semantics are known.

    message_id must be resolved by the caller's account/session MessageStore index;
    this converter never allocates message IDs. Unknown actors/subtypes suppress
    the notice rather than claiming an unrelated operation occurred.
    """
    def unavailable():
        logger.warning("Suppressing unrepresentable OneBot notice: %s", notice.notice_type)
        return None

    kind = notice.notice_type
    raw = notice.raw if isinstance(notice.raw, dict) else {}
    detail = raw.get("group_event", raw)
    if not isinstance(detail, dict):
        detail = {}
    event: dict[str, Any] = {
        "time": notice.timestamp or int(time.time()), "self_id": self_id,
        "post_type": "notice", "notice_type": kind,
    }
    if not notice.external_user_id:
        return unavailable()
    event["user_id"] = mapper.get_or_create("user", notice.external_user_id)
    if kind.startswith("group_"):
        if not notice.external_chat_id.endswith("@chatroom"):
            return unavailable()
        event["group_id"] = mapper.get_or_create("group", notice.external_chat_id)
    if kind in ("group_increase", "group_decrease"):
        subtype = detail.get("sub_type")
        allowed = ("approve", "invite") if kind == "group_increase" else ("leave", "kick", "kick_me")
        if subtype not in allowed:
            return unavailable()
        event["sub_type"] = subtype
        if subtype == "leave":
            if notice.external_operator_id and notice.external_operator_id != notice.external_user_id:
                return unavailable()
            event["operator_id"] = event["user_id"]
        elif notice.external_operator_id:
            event["operator_id"] = mapper.get_or_create("user", notice.external_operator_id)
        else:
            return unavailable()
    elif kind in ("group_recall", "friend_recall"):
        if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id == 0:
            return unavailable()
        event["message_id"] = message_id
        if kind == "group_recall":
            if not notice.external_operator_id:
                return unavailable()
            event["operator_id"] = mapper.get_or_create("user", notice.external_operator_id)
    elif kind != "friend_add":
        return unavailable()
    return event


def request_event(request: RequestEvent, mapper: IdMapper, self_id: int) -> dict[str, Any] | None:
    raw = request.raw if isinstance(request.raw, dict) else {}
    detail = raw.get("request", raw)
    subtype = detail.get("sub_type") if isinstance(detail, dict) else None
    if (not request.external_user_id or request.request_type not in ("friend", "group") or
            (request.request_type == "group" and (not request.external_group_id.endswith("@chatroom") or
                                                  subtype not in ("add", "invite")))):
        logger.warning("Suppressing unrepresentable OneBot request: %s", request.request_type)
        return None
    event = {
        "time": int(time.time()), "self_id": self_id, "post_type": "request",
        "request_type": request.request_type,
        "user_id": mapper.get_or_create("user", request.external_user_id),
        "comment": request.comment, "flag": request.flag or request.external_user_id,
    }
    if request.request_type == "group":
        event.update(group_id=mapper.get_or_create("group", request.external_group_id), sub_type=subtype)
    return event


def meta_event(self_id: int, meta_type: str, **data: Any) -> dict[str, Any]:
    return {
        "time": int(time.time()),
        "self_id": self_id,
        "post_type": "meta_event",
        "meta_event_type": meta_type,
        **data,
    }
