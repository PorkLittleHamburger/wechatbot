from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from wechat_onebot.config import AppConfig, DriverConfig, OneBotConfig, StorageConfig
from wechat_onebot.driver.helper import HelperDriver


def make_config(tmp_path: Path) -> AppConfig:
    helper = Path(__file__).resolve().parents[1] / "vendor" / "helper"
    return AppConfig(
        path=tmp_path / "config.json",
        onebot=OneBotConfig(access_token="test"),
        reverse_ws=(),
        driver=DriverConfig(type="helper", helper_root=helper, wechat_account="wxid_robot"),
        storage=StorageConfig(tmp_path / "data", tmp_path / "media", 1024 * 1024),
    )


def test_vendored_helper_exposes_native_text_backend(tmp_path: Path):
    config = make_config(tmp_path)
    sys.path.insert(0, str(config.driver.helper_root))
    from wxbot_framework.backend import NativeTextBackend
    from wxbot_framework.sdk import ServiceFacade
    assert callable(NativeTextBackend)
    assert callable(ServiceFacade)


def test_helper_message_conversion_keeps_native_mention(tmp_path: Path):
    config = make_config(tmp_path)
    sys.path.insert(0, str(config.driver.helper_root))
    driver = HelperDriver(config)
    message = SimpleNamespace(
        data={
            "sender_wechat_name": "Alice",
            "sender_role": "member",
            "at_members": ("wxid_bob",),
            "is_at_self": False,
            "native_message_id": "991",
        },
        type=1,
        text="@Bob\u2005 hello",
        sender_id="wxid_alice",
        chat_id="123@chatroom",
        is_group=True,
        is_self=False,
        sequence=7,
    )
    event = driver._convert_message(message)
    assert event is not None
    assert event.external_chat_id == "123@chatroom"
    assert event.sender_name == "Alice"
    assert event.segments[0].type == "at"
    assert event.segments[0].data["qq"] == "wxid_bob"
    assert event.segments[1].type == "text"
    assert event.segments[1].data["text"] == "hello"


def test_raw_helper_dictionary_conversion_supports_text_image_file_and_group_mention(tmp_path: Path):
    config = make_config(tmp_path)
    sys.path.insert(0, str(config.driver.helper_root))
    driver = HelperDriver(config)
    text_event = driver._convert_raw_data({
        "wx_type": 1, "chat_id": "wxid_alice", "sender_id": "wxid_alice",
        "msg": "hello", "sequence": 3, "native_message_id": "101",
        "sender_wechat_name": "Alice", "is_self": False,
    })
    assert text_event is not None and text_event.segments[0].data["text"] == "hello"
    image_event = driver._convert_raw_data({
        "wx_type": 3, "chat_id": "wxid_alice", "sender_id": "wxid_alice",
        "image_path": str(tmp_path / "a.png"), "sequence": 4, "native_message_id": "102",
    })
    assert image_event is not None and image_event.segments[0].type == "image"
    file_event = driver._convert_raw_data({
        "wx_type": 49, "chat_id": "wxid_alice", "sender_id": "wxid_alice",
        "file_path": str(tmp_path / "a.txt"), "file_name": "a.txt",
        "sequence": 5, "native_message_id": "103",
    })
    assert file_event is not None and file_event.segments[0].type == "file"
    group_event = driver._convert_raw_data({
        "wx_type": 1, "chat_id": "123@chatroom", "room_wxid": "123@chatroom",
        "sender_id": "wxid_alice", "msg": "@Bot hello", "at_members": ("wxid_robot",),
        "is_at_self": True, "sequence": 6, "native_message_id": "104",
    })
    assert group_event is not None and group_event.is_group
    assert group_event.segments[0].type == "at"
    assert group_event.raw["is_at_self"] is True


def test_monitor_only_media_is_not_emitted_before_download_finishes(tmp_path: Path):
    config = make_config(tmp_path)
    sys.path.insert(0, str(config.driver.helper_root))
    driver = HelperDriver(config)
    assert driver._convert_raw_data({
        "wx_type": 3, "chat_id": "wxid_alice", "sender_id": "wxid_alice",
        "monitor_only": True, "sequence": 8, "native_message_id": "105",
    }) is None
    assert driver._convert_raw_data({
        "wx_type": 49, "chat_id": "wxid_alice", "sender_id": "wxid_alice",
        "monitor_only": True, "sequence": 9, "native_message_id": "106",
    }) is None
