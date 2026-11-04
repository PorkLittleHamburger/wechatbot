from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from test_service import make_service
from test_service import make_config
from wechat_onebot.driver.base import DriverError
from wechat_onebot.models import GroupMemberInfo
from wechat_onebot.models import NoticeEvent
from wechat_onebot.onebot.message_store import MessageStore
from wechat_onebot.driver.mock import MockDriver
from wechat_onebot.ids import IdMapper
from wechat_onebot.media import MediaStore
from wechat_onebot.service import OneBotService


def test_profiles_persist_isolate_accounts_groups_and_preserve_nonempty_names(tmp_path):
    path = tmp_path / "messages.sqlite3"
    with MessageStore(path) as store:
        store.remember_profiles("a", [("u", "", "Nick", "", "message"),
                                      ("u", "g1", "", "Card1", "members"),
                                      ("u", "g2", "", "Card2", "members")])
        store.remember_profiles("a", [("u", "", "", "", "message")])
        store.remember_profiles("b", [("u", "", "Other", "", "message")])
        store.replace_memberships("a", "g1", [])
    with MessageStore(path) as store:
        assert store.profile("a", "u", "g1") == {"nickname": "Nick", "card": "Card1"}
        assert store.profile("a", "u", "g2")["card"] == "Card2"
        assert store.profile("b", "u")["nickname"] == "Other"
        assert store.profile("a", "new") is None
        assert not store.has_membership("a", "u", "g1")
        assert store.has_membership("a", "u", "g2")


@pytest.mark.asyncio
async def test_first_empty_name_is_filled_before_event_then_reused_for_at(tmp_path):
    service, driver, mapper, events = await make_service(tmp_path)
    try:
        driver.members["10001@chatroom"] = (GroupMemberInfo(
            "10001@chatroom", "wxid_alice", "Alice", "群昵称"),)
        await driver.inject_message(chat_id="10001@chatroom", sender_name="", is_group=True)
        sender = events[-1]["sender"]
        assert sender["nickname"] == "Alice" and sender["card"] == "群昵称"
        assert (await service.dispatch("get_stranger_info", {"user_id": sender["user_id"]}))["data"]["nickname"] == "Alice"
        driver.get_group_member_list = AsyncMock(side_effect=DriverError("cache not ready"))
        await driver.inject_message(chat_id="10001@chatroom", sender_name="", is_group=True)
        assert events[-1]["sender"] == sender
        result = await service.dispatch("send_group_msg", {
            "group_id": events[-1]["group_id"], "message": [
                {"type": "at", "data": {"qq": sender["user_id"]}},
                {"type": "text", "data": {"text": "hello"}}]})
        assert result["status"] == "ok"
        assert driver.sent[-1].payload["display_names"] == ["群昵称"]
        assert driver.sent[-1].payload["user_ids"] == ["wxid_alice"]
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_member_observation_not_global_contact_controls_group_lookup(tmp_path):
    service, driver, mapper, events = await make_service(tmp_path)
    try:
        user = mapper.get_or_create("user", "wxid_alice")
        group = mapper.get_or_create("group", "10001@chatroom")
        await service.dispatch("get_friend_list")
        driver.get_group_member_list = AsyncMock(side_effect=DriverError("unavailable"))
        assert (await service.dispatch("get_group_member_info", {"group_id": group, "user_id": user}))["retcode"] == 1404
        await driver.inject_message(chat_id="10001@chatroom", is_group=True)
        result = await service.dispatch("get_group_member_info", {"group_id": group, "user_id": user})
        assert result["data"]["nickname"] == "Alice"
        assert result["data"]["card"] == "" and "role" in result["data"]["unavailable_fields"]
        driver.get_group_member_list = AsyncMock(return_value=())
        assert (await service.dispatch("get_group_member_info", {"group_id": group, "user_id": user}))["retcode"] == 1404
        driver.get_group_member_list = AsyncMock(side_effect=DriverError("unavailable again"))
        assert (await service.dispatch("get_group_member_info", {"group_id": group, "user_id": user}))["retcode"] == 1404
        assert (await service.dispatch("get_stranger_info", {"user_id": user}))["data"]["nickname"] == "Alice"
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_unknown_user_not_fabricated_and_no_cache_refreshes(tmp_path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        unknown = mapper.get_or_create("user", "wxid_unknown")
        assert (await service.dispatch("get_stranger_info", {"user_id": unknown}))["retcode"] == 1404
        alice = mapper.get_or_create("user", "wxid_alice")
        assert (await service.dispatch("get_stranger_info", {"user_id": alice}))["data"]["nickname"] == "Alice"
        driver.friends = (replace(driver.friends[0], nickname="New Alice"),)
        assert (await service.dispatch("get_stranger_info", {"user_id": alice, "no_cache": True}))["data"]["nickname"] == "New Alice"
        assert (await service.dispatch("get_stranger_info", {"user_id": alice, "no_cache": "false"}))["retcode"] == 0
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_card_only_observation_does_not_block_later_nickname_refresh(tmp_path):
    service, driver, mapper, events = await make_service(tmp_path)
    try:
        driver.members["10001@chatroom"] = (GroupMemberInfo("10001@chatroom", "wxid_alice", "", "Card only"),)
        await driver.inject_message(chat_id="10001@chatroom", is_group=True, sender_name="")
        assert events[-1]["sender"]["card"] == "Card only"
        driver.members["10001@chatroom"] = (GroupMemberInfo("10001@chatroom", "wxid_alice", "Fresh Nick", "Card only"),)
        await driver.inject_message(chat_id="10001@chatroom", is_group=True, sender_name="")
        assert events[-1]["sender"]["nickname"] == "Fresh Nick"
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_leave_notice_drops_membership_but_preserves_identity(tmp_path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        await driver.inject_message(chat_id="10001@chatroom", is_group=True)
        await service._on_driver_event(NoticeEvent("group_decrease", "10001@chatroom", "wxid_alice"))
        driver.get_group_member_list = AsyncMock(side_effect=DriverError("unavailable"))
        result = await service.dispatch("get_group_member_info", {
            "group_id": mapper.get("group", "10001@chatroom"),
            "user_id": mapper.get("user", "wxid_alice")})
        assert result["retcode"] == 1404
        assert service.message_store.profile(service.account, "wxid_alice")["nickname"] == "Alice"
    finally:
        await service.stop()
        mapper.close()


def test_mention_label_is_inline_and_native_ids_unchanged(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "vendor/helper"))
    from wxbot_framework.sdk import ServiceFacade
    facade = object.__new__(ServiceFacade)
    facade.send_text = Mock(return_value=True)
    assert facade.send_at_text("10001@chatroom", "正文", ["wxid_alice"], display_names=["Alice"])
    args, kwargs = facade.send_text.call_args
    assert args[1] == "@Alice\u2005 正文"
    assert kwargs["_mentions"] == ("wxid_alice",)


@pytest.mark.asyncio
async def test_restart_retains_user_names_before_first_callback(tmp_path):
    config = make_config(tmp_path)
    database = tmp_path / "messages.sqlite3"
    for first in (True, False):
        driver = MockDriver()
        driver.get_group_member_list = AsyncMock(side_effect=DriverError("unavailable"))
        mapper = IdMapper(tmp_path / "ids.sqlite3")
        store = MessageStore(database)
        media = MediaStore(tmp_path / "media", max_bytes=1024 * 1024, public_base_url="http://127.0.0.1:6700")
        service = OneBotService(config, driver, mapper, media, message_store=store)
        events = []
        async def receive(event):
            events.append(event)
            number = event["user_id"]
            # Querying immediately inside the delivery callback must already work.
            response = await service.dispatch("get_stranger_info", {"user_id": number})
            assert response["data"]["nickname"] == "Observed Nick"
        service.set_event_sink(receive)
        try:
            await service.start()
            await driver.inject_message(chat_id="10001@chatroom", is_group=True,
                                        sender_name="Observed Nick" if first else "")
            assert events[-1]["sender"]["nickname"] == "Observed Nick"
        finally:
            await service.stop()
            mapper.close()
            store.close()
