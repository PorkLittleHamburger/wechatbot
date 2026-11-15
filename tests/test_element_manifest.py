"""Behavior probes for the message-element direction manifest.

The manifest is only useful if the code still behaves exactly as declared. These
tests probe all four directions instead of comparing two hand-written tables:

- D: helper/sdk native input -> ``Segment``.
- S: ``Segment`` -> OneBot message segment.
- P: OneBot outbound segment -> prepare accept/reject.
- W: prepared segment -> ``Driver.send_*``.

Build-specific availability (for example native emoticon send on WeChat 4.2)
belongs to ``Driver.capabilities`` and is intentionally not tested here.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_service import make_config, make_service
from wechat_onebot.driver.helper import HelperDriver
from wechat_onebot.driver.sdk import SdkDriver
from wechat_onebot.ids import IdMapper
from wechat_onebot.media import MediaStore
from wechat_onebot.models import Segment
from wechat_onebot.protocol import converters
from wechat_onebot.protocol.element_manifest import (
    ELEMENT_DIRECTIONS,
    ELEMENT_MANIFEST,
    ELEMENT_SEND_CAPABILITIES,
    NATIVE_SEND_TYPES,
    OUTBOUND_SUPPORTED_TYPES,
    SUPPORT_VALUES,
)
from wechat_onebot.service import OneBotActionError, PreparedSegment

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_ELEMENTS = {
    "text", "at", "image", "record", "file", "video", "face", "reply",
    "forward", "node", "music", "json", "xml", "share", "contact",
}

# Minimal outbound data per P=yes element. Media entries are deliberately
# missing files: the manifest gate must pass first and the later media error
# must stay explicit.
OUTBOUND_MINIMAL = {
    "text": {"text": "hi"},
    "at": {"qq": "wxid_alice"},
    "image": {"file": "missing-manifest-probe.png"},
    "file": {"file": "missing-manifest-probe.bin"},
    "record": {"file": "missing-manifest-probe.wav"},
    "face": {"file": "missing-manifest-probe.gif"},
    "video": {"file": "missing-manifest-probe.mp4"},
    "music": {"url": "https://example.invalid/song"},
}

SEND_METHODS = {
    "text": "send_text",
    "at": "send_at_text",
    "image": "send_image",
    "file": "send_file",
    "record": "send_voice",
    "face": "send_emoticon",
    "video": "send_video",
}


def test_manifest_covers_every_direction_with_known_values():
    assert set(ELEMENT_MANIFEST) == EXPECTED_ELEMENTS
    for directions in ELEMENT_MANIFEST.values():
        assert tuple(directions) == ELEMENT_DIRECTIONS
        assert set(directions.values()) <= set(SUPPORT_VALUES)
    assert NATIVE_SEND_TYPES == frozenset(ELEMENT_SEND_CAPABILITIES)
    assert OUTBOUND_SUPPORTED_TYPES == frozenset(
        element for element, support in ELEMENT_MANIFEST.items() if support["P"] == "yes")
    # An element without an inbound projection must not be decodable either.
    assert all(support["D"] == "no" for support in ELEMENT_MANIFEST.values()
               if support["S"] == "no")


@pytest.mark.asyncio
@pytest.mark.parametrize("element", sorted(OUTBOUND_SUPPORTED_TYPES))
async def test_outbound_preparation_accepts_manifest_supported_elements(tmp_path, element):
    service, _, mapper, _ = await make_service(tmp_path)
    try:
        data = dict(OUTBOUND_MINIMAL[element])
        if element == "at":
            data = {"qq": str(mapper.get_or_create("user", "wxid_alice"))}
        try:
            prepared = await service._prepare_segments(
                [{"type": element, "data": data}], is_group=True)
        except OneBotActionError as error:
            # The probe media is intentionally missing. Reaching a media error
            # proves the manifest accepted the element; it must not be the
            # "unsupported segment" rejection.
            assert error.retcode == 1400
            assert "不支持发送" not in str(error)
        else:
            assert prepared
            assert {item.type for item in prepared} <= NATIVE_SEND_TYPES
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("element", sorted(set(ELEMENT_MANIFEST) - OUTBOUND_SUPPORTED_TYPES))
async def test_outbound_preparation_rejects_manifest_unsupported_elements(tmp_path, element):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        with pytest.raises(OneBotActionError) as caught:
            await service._prepare_segments([{"type": element, "data": {}}], is_group=True)
        assert caught.value.retcode == 1404
        if element == "reply":
            assert "引用回复" in str(caught.value)
        else:
            assert f"不支持发送 {element} 段" in str(caught.value)
        assert driver.sent == []
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_face_element_still_rejects_qq_face_ids_during_preparation(tmp_path):
    """P=yes covers file-based stickers; QQ face IDs stay an explicit 1404."""
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        user = (await service.dispatch("get_friend_list"))["data"][0]["user_id"]
        result = await service.dispatch("send_private_msg", {
            "user_id": user, "message": [{"type": "face", "data": {"id": "1"}}]})
        assert result["status"] == "failed" and result["retcode"] == 1404
        assert "QQ 表情 ID" in result["message"]
        assert driver.sent == []
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_native_send_dispatch_matches_manifest_w_direction(tmp_path):
    service, driver, mapper, _ = await make_service(tmp_path)
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")
    try:
        assert set(SEND_METHODS) == NATIVE_SEND_TYPES
        for element, method in SEND_METHODS.items():
            mocks = {name: AsyncMock(return_value=True) for name in SEND_METHODS.values()}
            for name, mock in mocks.items():
                setattr(driver, name, mock)
            if element == "at":
                data = {"qq": "wxid_alice", "name": "Alice"}
            elif element == "text":
                data = {"text": "hi"}
            else:
                data = {"path": str(payload)}
            await service._send_prepared_message(
                "10001@chatroom", [PreparedSegment(element, data)], is_group=True)
            mocks[method].assert_awaited_once()
            for other, mock in mocks.items():
                if other != method:
                    mock.assert_not_awaited()
    finally:
        await service.stop()
        mapper.close()


@pytest.fixture
def helper_paths(monkeypatch):
    helper = ROOT / "vendor" / "helper"
    monkeypatch.syspath_prepend(str(helper))
    monkeypatch.syspath_prepend(str(helper / "native"))


def test_helper_driver_decode_matches_manifest_d_direction(helper_paths, tmp_path):
    driver = HelperDriver(make_config(tmp_path))
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")
    common = {"chat_id": "wxid_alice", "sender_id": "wxid_alice"}
    cases = {
        "text": ({"wx_type": 1, "msg": "hi"}, "text"),
        "at": ({"wx_type": 1, "msg": "hi", "chat_id": "1@chatroom",
                "at_members": ["wxid_bob"]}, "at"),
        "image": ({"wx_type": 3, "image_path": str(payload)}, "image"),
        "file": ({"wx_type": 49, "file_path": str(payload), "file_name": "x.bin"}, "file"),
        "record": ({"wx_type": 34, "voice_status": "ready",
                    "voice_path": str(payload)}, "record"),
        "video": ({"wx_type": 43, "video_status": "ready",
                   "video_path": str(payload)}, "video"),
        "forward": ({"wx_type": 49, "app_type": 19, "forwarded_records": []}, "forward"),
    }
    assert set(cases) == {element for element, support in ELEMENT_MANIFEST.items()
                          if support["D"] == "yes"}
    for element, (raw, expected) in cases.items():
        message = driver._convert_raw_data({**common, **raw})
        assert message is not None, element
        assert expected in {segment.type for segment in message.segments}, element
    # WeChat file stickers are text, not a Segment("face"), matching D="no".
    sticker = driver._convert_raw_data({**common, "wx_type": 47})
    assert [segment.type for segment in sticker.segments] == ["text"]


def _sdk_event(**overrides) -> SimpleNamespace:
    values = dict(content={}, chat_id="wxid_alice", sender_id="wxid_alice",
                  mentions=(), content_type="text", text="", event_id="event-1",
                  native_message_id="1", sequence=1, native_timestamp=1700000000,
                  reference=None)
    values.update(overrides)
    return SimpleNamespace(**values)


def test_sdk_driver_decode_matches_manifest_d_direction(monkeypatch, tmp_path):
    driver = SdkDriver(make_config(tmp_path))
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")
    monkeypatch.setattr(driver, "_resolve_media",
                        lambda event, content, kind="": (payload, ""))
    cases = {
        "text": (dict(content_type="text", text="hi"), "text"),
        "at": (dict(content_type="text", text="hi", mentions=("wxid_bob",)), "at"),
        "image": (dict(content_type="image", content={"media_state": "ready"}), "image"),
        "file": (dict(content_type="file", content={"media_state": "ready"}), "file"),
        "record": (dict(content_type="voice", content={"media_state": "ready"}), "record"),
        "video": (dict(content_type="video", content={"media_state": "ready"}), "video"),
        "forward": (dict(content_type="forwarded",
                          content={"forwarded_records": [{"kind": "text", "text": "in"}]}),
                    "forward"),
    }
    assert set(cases) == {element for element, support in ELEMENT_MANIFEST.items()
                          if support["D"] == "yes"}
    for element, (event, expected) in cases.items():
        message = driver._convert_message(_sdk_event(**event))
        assert message is not None, element
        assert expected in {segment.type for segment in message.segments}, element
    # SDK stickers become text and inbound quotes stay in raw["reply"].
    sticker = driver._convert_message(_sdk_event(content_type="emoticon"))
    assert [segment.type for segment in sticker.segments] == ["text"]
    quoted = driver._convert_message(_sdk_event(
        content_type="reference", text="quoted",
        reference={"native_message_id": "9001", "sender_id": "wxid_alice",
                   "sender_name": "Alice", "type": 1, "content": "original"}))
    assert "reply" not in {segment.type for segment in quoted.segments}
    assert quoted.raw["reply"]["native_message_id"] == "9001"


def test_segment_projection_matches_manifest_s_direction(tmp_path):
    mapper = IdMapper(tmp_path / "ids.sqlite3")
    media = MediaStore(tmp_path / "media", max_bytes=1024 * 1024,
                       public_base_url="http://test")
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")
    try:
        cases = {
            "text": ({"text": "hi"}, "text"),
            "at": ({"qq": "wxid_alice"}, "at"),
            "image": ({"file": str(payload)}, "image"),
            "record": ({"file": str(payload)}, "record"),
            "video": ({"file": str(payload)}, "video"),
            "file": ({"file": str(payload), "name": "x.bin"}, "text"),
            "face": ({"id": "1"}, "face"),
            "forward": ({"records": []}, "forward"),
        }
        assert set(cases) == {element for element, support in ELEMENT_MANIFEST.items()
                              if support["S"] == "yes"}
        for element, (data, expected) in cases.items():
            projected = converters._segment_to_onebot(
                Segment(element, data), mapper, media)
            assert projected["type"] == expected, element
        # face.file projects as an image; QQ face.id keeps the face type.
        sticker = converters._segment_to_onebot(
            Segment("face", {"file": str(payload)}), mapper, media)
        assert sticker["type"] == "image"
    finally:
        mapper.close()
