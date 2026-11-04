from __future__ import annotations

from pathlib import Path

import pytest

from wechat_onebot.config import AppConfig, DriverConfig, OneBotConfig, StorageConfig
from wechat_onebot.driver.mock import MockDriver
from wechat_onebot.ids import IdMapper
from wechat_onebot.media import MediaStore
from wechat_onebot.service import OneBotService


def make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        path=tmp_path / "config.json",
        onebot=OneBotConfig(access_token="test-token"),
        reverse_ws=(),
        driver=DriverConfig(type="mock", helper_root=tmp_path / "helper", wechat_account="wxid_robot"),
        storage=StorageConfig(tmp_path / "data", tmp_path / "media", 1024 * 1024),
    )


async def make_service(tmp_path: Path):
    config = make_config(tmp_path)
    driver = MockDriver()
    mapper = IdMapper(tmp_path / "ids.sqlite3")
    media = MediaStore(config.storage.media_dir, max_bytes=config.storage.max_media_bytes,
                       public_base_url="http://127.0.0.1:6700")
    service = OneBotService(config, driver, mapper, media)
    events = []
    async def collect(event):
        events.append(event)
    service.set_event_sink(collect)
    await service.start()
    return service, driver, mapper, events


@pytest.mark.asyncio
async def test_dispatch_rejects_empty_non_object_params(tmp_path: Path):
    service, _, mapper, _ = await make_service(tmp_path)
    try:
        response = await service.dispatch("get_status", [])
        assert response["status"] == "failed"
        assert response["retcode"] == 1400
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_send_group_at_text_uses_native_mention(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        groups = (await service.dispatch("get_group_list"))["data"]
        group_id = groups[0]["group_id"]
        friends = (await service.dispatch("get_friend_list"))["data"]
        alice = next(row for row in friends if row["nickname"] == "Alice")
        response = await service.dispatch("send_group_msg", {
            "group_id": group_id,
            "message": [
                {"type": "at", "data": {"qq": alice["user_id"], "name": "Alice"}},
                {"type": "text", "data": {"text": " 你好"}},
            ],
        })
        assert response["status"] == "ok"
        assert driver.sent[-1].kind == "at_text"
        assert driver.sent[-1].chat_id == "10001@chatroom"
        assert driver.sent[-1].payload["user_ids"] == ["wxid_alice"]
        assert driver.sent[-1].payload["display_names"] == ["Alice"]
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_native_reply_unavailable_rejects_entire_chain(tmp_path: Path):
    service, driver, mapper, events = await make_service(tmp_path)
    try:
        groups = (await service.dispatch("get_group_list"))["data"]
        await driver.inject_message(chat_id="10001@chatroom", sender_id="wxid_alice",
                                    text="今日人品", is_group=True, message_id="native-123")
        source = events[-1]
        response = await service.dispatch("send_group_msg", {
            "group_id": groups[0]["group_id"],
            "message": [
                {"type": "reply", "data": {"id": str(source["message_id"])}},
                {"type": "at", "data": {"qq": source["user_id"], "name": "Alice"}},
                {"type": "text", "data": {"text": "\n今日人品值：87 / 100"}},
            ],
        })
        assert response["status"] == "failed" and response["retcode"] == 1404
        assert driver.sent == []
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_reply_only_and_other_unsupported_segments_do_not_send(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        friends = (await service.dispatch("get_friend_list"))["data"]
        user_id = friends[0]["user_id"]
        reply_only = await service.dispatch("send_private_msg", {
            "user_id": user_id, "message": [{"type": "reply", "data": {"id": "123"}}],
        })
        assert reply_only["status"] == "failed" and reply_only["retcode"] == 1404
        unsupported = await service.dispatch("send_private_msg", {
            "user_id": user_id,
            "message": [{"type": "text", "data": {"text": "hello"}},
                        {"type": "node", "data": {"id": "123"}}],
        })
        assert unsupported["status"] == "failed" and unsupported["retcode"] == 1404
        assert driver.sent == []
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_inbound_message_is_broadcast(tmp_path: Path):
    service, driver, mapper, events = await make_service(tmp_path)
    try:
        await driver.inject_message(text="ping", chat_id="wxid_alice")
        await __import__("asyncio").sleep(0)
        assert events
        assert events[-1]["post_type"] == "message"
        assert events[-1]["message"][0]["data"]["text"] == "ping"
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_inbound_message_is_durable_and_queryable_by_signed_id(tmp_path: Path):
    service, driver, mapper, events = await make_service(tmp_path)
    try:
        await driver.inject_message(text="stored", chat_id="wxid_alice")
        await __import__("asyncio").sleep(0)
        message_id = events[-1]["message_id"]
        assert message_id != 0
        assert -(2**31) <= message_id <= 2**31 - 1
        response = await service.dispatch("get_msg", {"message_id": message_id})
        assert response["status"] == "ok"
        assert response["data"]["real_id"] == message_id
        assert response["data"]["message"][0]["data"]["text"] == "stored"
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_runtime_scoped_history_and_cursor(tmp_path: Path):
    service, driver, mapper, events = await make_service(tmp_path)
    try:
        friends = (await service.dispatch("get_friend_list"))["data"]
        alice = next(row for row in friends if row["nickname"] == "Alice")
        for index in range(1, 4):
            await driver.inject_message(text=f"history-{index}", chat_id="wxid_alice",
                                        message_id=f"h{index}", timestamp=1700000000 + index)
            await __import__("asyncio").sleep(0)
        history = await service.dispatch("get_friend_msg_history", {
            "user_id": alice["user_id"], "count": 2,
        })
        assert history["status"] == "ok"
        assert [row["message"][0]["data"]["text"] for row in history["data"]] == ["history-2", "history-3"]
        cursor = events[0]["message_id"]
        newer = await service.dispatch("get_friend_msg_history", {
            "user_id": alice["user_id"], "message_id": cursor,
            "count": 10, "reverse_order": True,
        })
        assert [row["message"][0]["data"]["text"] for row in newer["data"]] == ["history-2", "history-3"]
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_group_history_is_account_scoped(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        groups = (await service.dispatch("get_group_list"))["data"]
        group_id = groups[0]["group_id"]
        await driver.inject_message(chat_id="10001@chatroom", sender_id="wxid_alice",
                                    text="group-history", is_group=True,
                                    message_id="g1", timestamp=1700000010)
        await __import__("asyncio").sleep(0)
        history = await service.dispatch("get_group_msg_history", {"group_id": group_id})
        assert history["status"] == "ok"
        assert history["data"][0]["message"][0]["data"]["text"] == "group-history"
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_duplicate_inbound_event_is_suppressed(tmp_path: Path):
    service, driver, mapper, events = await make_service(tmp_path)
    try:
        await driver.inject_message(text="once", chat_id="wxid_alice", message_id="same-1", timestamp=1700000000)
        await __import__("asyncio").sleep(0)
        assert len(events) == 1
        await driver.inject_message(text="once", chat_id="wxid_alice", message_id="same-1", timestamp=1700000000)
        await __import__("asyncio").sleep(0)
        assert len(events) == 1
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_action_validation_fails_before_side_effects(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        response = await service.dispatch("send_group_msg", {"group_id": 1})
        assert response["status"] == "failed"
        assert response["retcode"] == 1400
        assert driver.sent == []
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_unknown_action_fails_explicitly(tmp_path: Path):
    service, _, mapper, _ = await make_service(tmp_path)
    try:
        response = await service.dispatch("set_group_ban", {"group_id": 1, "user_id": 2, "duration": 60})
        assert response["status"] == "failed"
        assert response["retcode"] == 1404
    finally:
        await service.stop()
        mapper.close()
