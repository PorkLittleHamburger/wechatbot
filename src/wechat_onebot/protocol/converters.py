from __future__ import annotations

import time
from typing import Any

from ..ids import IdMapper
from ..media import MediaStore
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


def _segment_to_onebot(segment: Segment, mapper: IdMapper, media_store: MediaStore) -> dict[str, Any]:
    data = dict(segment.data)
    if segment.type == "at" and data.get("qq") is not None:
        qq = str(data["qq"])
        if qq != "all":
            data["qq"] = mapper.get_or_create("user", qq)
    if segment.type in ("image", "file", "record", "face"):
        file_value = data.get("file") or data.get("path")
        if file_value:
            try:
                record = media_store.register(str(file_value))
                data["file"] = record.name
                data["url"] = media_store.url_for(record)
            except Exception:
                data["file"] = str(file_value)
                data["url"] = ""
        data.pop("path", None)
    return {"type": segment.type, "data": data}


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
        "sub_type": "group" if message.is_group else "friend",
        "message_id": message_id,
        "message_seq": message.sequence,
        "user_id": user_id,
        "message": [_segment_to_onebot(segment, mapper, media_store) for segment in message.segments],
        "raw_message": "".join(segment.data.get("text", "") for segment in message.segments if segment.type == "text"),
        "font": 0,
        "sender": {
            "user_id": user_id,
            "nickname": message.sender_name,
            "card": message.sender_name,
        },
    }
    if message.is_group:
        event["sender"]["role"] = message.sender_role if message.sender_role != "unknown" else "member"
        event["group_id"] = mapper.get_or_create("group", message.external_chat_id)
        event["anonymous"] = None
        if message.group_name:
            event["group_name"] = message.group_name
    if message.raw.get("is_at_self"):
        event["to_me"] = True
    return event


def notice_event(notice: NoticeEvent, mapper: IdMapper, self_id: int) -> dict[str, Any]:
    event: dict[str, Any] = {
        "time": notice.timestamp or int(time.time()),
        "self_id": self_id,
        "post_type": "notice",
        "notice_type": notice.notice_type,
    }
    if notice.external_chat_id:
        kind = "group" if notice.external_chat_id.endswith("@chatroom") else "user"
        key = "group_id" if kind == "group" else "user_id"
        event[key] = mapper.get_or_create(kind, notice.external_chat_id)
    if notice.external_user_id:
        event["user_id"] = mapper.get_or_create("user", notice.external_user_id)
    if notice.external_operator_id:
        event["operator_id"] = mapper.get_or_create("user", notice.external_operator_id)
    if notice.notice_type in ("group_recall", "friend_recall"):
        recall = notice.raw.get("recall") if isinstance(notice.raw, dict) else None
        if isinstance(recall, dict) and recall.get("message_id"):
            event["message_id"] = mapper.get_or_create("message", f"{notice.external_chat_id}\0{recall['message_id']}")
    event["raw"] = {}
    return event


def request_event(request: RequestEvent, mapper: IdMapper, self_id: int) -> dict[str, Any]:
    return {
        "time": int(time.time()),
        "self_id": self_id,
        "post_type": "request",
        "request_type": request.request_type,
        "user_id": mapper.get_or_create("user", request.external_user_id),
        "comment": request.comment,
        "flag": request.flag or request.external_user_id,
    }


def meta_event(self_id: int, meta_type: str, **data: Any) -> dict[str, Any]:
    return {
        "time": int(time.time()),
        "self_id": self_id,
        "post_type": "meta_event",
        "meta_event_type": meta_type,
        **data,
    }
