from __future__ import annotations

import time
from pathlib import Path

from wechat_onebot.ids import IdMapper
from wechat_onebot.media import MediaStore
from wechat_onebot.models import IncomingMessage, Segment
from wechat_onebot.onebot import hash_message_id_int32
from wechat_onebot.protocol import message_event


def test_message_event_maps_ids_and_media(tmp_path: Path):
    media_dir = tmp_path / "media"
    media = MediaStore(media_dir, max_bytes=1024 * 1024, public_base_url="http://127.0.0.1:6700")
    image = media_dir / "inbound" / "sample.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test")
    with IdMapper(tmp_path / "ids.sqlite3") as mapper:
        self_id = mapper.get_or_create("user", "wxid_robot")
        message = IncomingMessage(
            external_chat_id="123@chatroom",
            external_sender_id="wxid_alice",
            sender_name="Alice",
            group_name="测试群",
            sender_role="member",
            segments=(
                Segment("at", {"qq": "wxid_bob"}),
                Segment("text", {"text": "hello"}),
                Segment("image", {"file": str(image)}),
            ),
            external_message_id="42",
            is_group=True,
            timestamp=int(time.time()),
            raw={"is_at_self": True},
        )
        message_id = hash_message_id_int32("42", message.external_chat_id, "group_message")
        event = message_event(message, mapper, media, self_id, message_id=message_id)
        assert event["post_type"] == "message"
        assert event["message_type"] == "group"
        assert event["group_id"] == mapper.get_or_create("group", "123@chatroom")
        assert event["user_id"] == mapper.get_or_create("user", "wxid_alice")
        assert event["message"][0] == {
            "type": "at",
            "data": {"qq": str(mapper.get_or_create("user", "wxid_bob"))},
        }
        assert event["message"][1]["data"]["text"] == "hello"
        assert event["message"][2]["type"] == "image"
        assert event["message"][2]["data"]["url"].startswith("http://127.0.0.1:6700/media/")
        assert event["to_me"] is True
