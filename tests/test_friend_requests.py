"""U-08a: friend-request flag mapping and the read-only list extension."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from test_service import make_config, make_service
from wechat_onebot.config import AppConfig, DriverConfig, OneBotConfig, StorageConfig, WebUiConfig
from wechat_onebot.driver.base import DriverError, DriverUnsupported
from wechat_onebot.driver.helper import HelperDriver
from wechat_onebot.driver.mock import MockDriver
from wechat_onebot.driver.sdk import SdkDriver
from wechat_onebot.ids import IdMapper
from wechat_onebot.media import MediaStore
from wechat_onebot.models import RequestEvent
from wechat_onebot.onebot.message_store import MessageStore
from wechat_onebot.service import OneBotService

REQUEST_ID = "fr_" + "a1b2c3d4" * 4


def test_helper_request_event_uses_native_request_id_flag(tmp_path):
    driver = HelperDriver(make_config(tmp_path))
    event = driver._convert_raw_data({
        "wx_type": 37,
        "sender_id": "wxid_fallback",
        "friend_request": {
            "request_id": REQUEST_ID, "sender_id": "wxid_sender",
            "verification": "你好", "scene": 10, "status": "pending",
        },
    })
    assert isinstance(event, RequestEvent)
    assert event.request_type == "friend"
    assert event.external_user_id == "wxid_sender"
    assert event.flag == REQUEST_ID
    assert event.comment == "你好"


def test_helper_request_event_keeps_legacy_flag_fallbacks(tmp_path):
    driver = HelperDriver(make_config(tmp_path))
    public = driver._convert_raw_data({
        "wx_type": 37,
        "friend_request": {"public_id": "public-1", "sender_id": "wxid_a"},
    })
    legacy = driver._convert_raw_data({
        "wx_type": 37,
        "friend_request": {"id": "legacy-1", "sender_id": "wxid_b"},
    })
    assert public.flag == "public-1"
    assert legacy.flag == "legacy-1"


def test_helper_message_request_event_reads_request_id(tmp_path):
    driver = HelperDriver(make_config(tmp_path))
    message = SimpleNamespace(
        type=37,
        data={"friend_request": {"request_id": "fr_message_path", "sender_id": "wxid_msg"}},
        sender_id="wxid_fallback",
        chat_id="fmessage",
    )
    event = driver._convert_message(message)
    assert isinstance(event, RequestEvent)
    assert event.flag == "fr_message_path"
    assert event.external_user_id == "wxid_msg"


@pytest.mark.asyncio
async def test_helper_friend_requests_whitelists_public_fields(tmp_path):
    driver = HelperDriver(make_config(tmp_path))
    driver._online = True
    driver._service = SimpleNamespace(friend_requests=lambda: [{
        "request_id": REQUEST_ID, "sender_id": "wxid_sender", "nickname": "Alice",
        "verification": "请通过", "scene": "10", "opcode": 2, "status": "pending",
        "error": "", "ticket": "SECRET-TICKET", "encryptusername": "SECRET-ENC",
        "raw_xml": "<xml>SECRET</xml>",
    }, "not-a-row", {"request_id": "", "sender_id": "wxid_ignored"}])
    try:
        rows = await driver.friend_requests()
    finally:
        driver._online = False
        driver._service = None
    assert rows == [{
        "request_id": REQUEST_ID, "sender_id": "wxid_sender", "nickname": "Alice",
        "verification": "请通过", "scene": 10, "status": "pending",
    }]
    for secret in ("SECRET-TICKET", "SECRET-ENC", "raw_xml", "ticket", "encryptusername"):
        assert secret not in str(rows)


@pytest.mark.asyncio
async def test_helper_friend_requests_wraps_native_errors(tmp_path):
    driver = HelperDriver(make_config(tmp_path))
    driver._online = True

    def broken():
        raise RuntimeError("好友申请目录所属连接已结束")

    driver._service = SimpleNamespace(friend_requests=broken)
    try:
        with pytest.raises(DriverError, match="读取好友申请失败"):
            await driver.friend_requests()
    finally:
        driver._online = False
        driver._service = None
        with pytest.raises(DriverError):
            await driver.friend_requests()


def test_capability_names_are_build_gated_for_helper_only(tmp_path):
    driver = HelperDriver(make_config(tmp_path))
    driver._backend = SimpleNamespace(capabilities=frozenset({
        "text.receive", "text.send", "friend.request.receive"}))
    assert "friend.request.list" in driver.capabilities
    assert "request.friend" in driver.capabilities
    driver._backend = SimpleNamespace(capabilities=frozenset({"text.receive"}))
    assert "friend.request.list" not in driver.capabilities
    sdk = SdkDriver(make_config(tmp_path))
    assert "friend.request.list" not in sdk.capabilities


@pytest.mark.asyncio
async def test_sdk_driver_has_no_friend_request_catalog(tmp_path):
    driver = SdkDriver(make_config(tmp_path))
    with pytest.raises(DriverUnsupported):
        await driver.friend_requests()


@pytest.mark.asyncio
async def test_get_friend_requests_action_reads_mock_catalog(tmp_path):
    service, driver, mapper, _ = await make_service(tmp_path)
    driver.friend_request_rows = [
        {"request_id": REQUEST_ID, "sender_id": "wxid_alice", "nickname": "Alice",
         "verification": "请加好友", "scene": 10, "status": "pending",
         "ticket": "SECRET"},
        {"request_id": "fr_second", "sender_id": "wxid_bob", "nickname": "",
         "verification": "", "scene": 0, "status": "accepted"},
        {"sender_id": "wxid_missing_flag"},
    ]
    try:
        response = await service.dispatch("get_friend_requests")
        assert response["status"] == "ok"
        rows = response["data"]["requests"]
        assert [row["flag"] for row in rows] == [REQUEST_ID, "fr_second"]
        first = rows[0]
        assert first["user_id"] == mapper.get_or_create("user", "wxid_alice")
        assert first["nickname"] == "Alice"
        assert first["comment"] == "请加好友"
        assert first["scene"] == 10
        assert first["status"] == "pending"
        assert "ticket" not in str(response)
        limited = await service.dispatch("get_friend_requests", {"count": 1})
        assert len(limited["data"]["requests"]) == 1
        assert len((await service.dispatch(
            "get_friend_requests", {"count": "2"}))["data"]["requests"]) == 2
        assert (await service.dispatch("get_friend_requests", {"count": 0}))["retcode"] == 1400
        assert (await service.dispatch("get_friend_requests", {"count": 101}))["retcode"] == 1400
        assert await MockDriver().friend_requests() == []
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_get_friend_requests_requires_driver_capability(tmp_path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        driver.capabilities = frozenset({"text.receive", "text.send"})
        response = await service.dispatch("get_friend_requests")
        assert response["status"] == "failed" and response["retcode"] == 1404
        assert "不支持" in response["message"]
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_sdk_service_reports_friend_requests_as_unsupported(tmp_path):
    config = AppConfig(
        path=tmp_path / "config.json",
        onebot=OneBotConfig(access_token="test-token"),
        reverse_ws=(),
        driver=DriverConfig(type="sdk", helper_root=tmp_path / "helper",
                            wechat_account="wxid_robot"),
        storage=StorageConfig(tmp_path / "data", tmp_path / "media", 1024 * 1024),
        webui=WebUiConfig(access_token="webui-token"),
    )
    driver = SdkDriver(config)
    mapper = IdMapper(tmp_path / "ids.sqlite3")
    media = MediaStore(config.storage.media_dir, max_bytes=config.storage.max_media_bytes,
                       public_base_url="http://127.0.0.1:6700")
    store = MessageStore(":memory:")
    service = OneBotService(config, driver, mapper, media,
                            message_store=store, account="wxid_robot")
    try:
        response = await service.dispatch("get_friend_requests")
        assert response["status"] == "failed" and response["retcode"] == 1404
    finally:
        mapper.close()
        store.close()
