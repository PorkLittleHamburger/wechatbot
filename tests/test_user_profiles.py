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
async def test_group_no_cache_is_documented_compatibility_parameter(tmp_path):
    service, _, mapper, _ = await make_service(tmp_path)
    try:
        group = (await service.dispatch("get_group_list"))["data"][0]["group_id"]
        member = (await service.dispatch("get_friend_list"))["data"][0]["user_id"]
        group_plain = await service.dispatch("get_group_info", {"group_id": group})
        group_forced = await service.dispatch("get_group_info", {"group_id": group, "no_cache": True})
        assert group_forced["retcode"] == 0
        assert group_forced["data"] == group_plain["data"]
        member_plain = await service.dispatch("get_group_member_info", {
            "group_id": group, "user_id": member})
        member_forced = await service.dispatch("get_group_member_info", {
            "group_id": group, "user_id": member, "no_cache": "true"})
        assert member_forced["retcode"] == 0
        assert member_forced["data"] == member_plain["data"]
        catalog = {row["name"]: row for row in
                   (await service.dispatch("get_capabilities"))["data"]["action_catalog"]}
        for name in ("get_group_info", "get_group_member_info"):
            spec = catalog[name]["params"]["no_cache"]
            assert spec["kind"] == "bool" and spec["required"] is False
            assert "兼容参数" in spec["description"]
            assert "不触发额外刷新" in spec["description"]
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


# ------------------------------------------------ automatic notification names
LEGACY_PROFILES_DDL = """
    CREATE TABLE user_profiles (
        account TEXT NOT NULL, user_id TEXT NOT NULL, group_id TEXT NOT NULL,
        nickname TEXT NOT NULL, card TEXT NOT NULL, source TEXT NOT NULL,
        observed_at INTEGER NOT NULL,
        PRIMARY KEY (account, user_id, group_id)
    )
"""


def test_notification_name_column_is_added_to_existing_databases(tmp_path):
    import sqlite3

    path = tmp_path / "messages.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(LEGACY_PROFILES_DDL)
    connection.execute(
        "INSERT INTO user_profiles VALUES ('a', 'u', 'g1', '旧昵称', '旧卡片', 'group_member', 1)")
    connection.commit()
    connection.close()

    with MessageStore(path) as store:
        assert store.profile("a", "u", "g1") == {"nickname": "", "card": "旧卡片"}
        assert store.notification_name("a", "u", "g1") == ""
        # Explicit column names are required now that the table has 8 columns.
        store.remember_profiles("a", [("u", "", "新昵称", "", "message")])
        assert store.profile("a", "u", "g1") == {"nickname": "新昵称", "card": "旧卡片"}
        assert store.remember_notification_name("a", "u", "g1", "自动名") is True
        assert store.profile("a", "u", "g1") == {"nickname": "新昵称", "card": "自动名"}
        assert store.notification_name("a", "u", "g1") == "自动名"


def test_notification_name_keeps_its_source_across_roster_refreshes(tmp_path):
    import sqlite3

    path = tmp_path / "messages.sqlite3"
    with MessageStore(path) as store:
        store.remember_profiles("a", [("u", "", "全局昵称", "", "friend")])
        assert store.remember_notification_name("a", "u", "g1", "自动名") is True
        # The global nickname row is never overwritten and no global row appears
        # because of a notification.
        assert store.profile("a", "u") == {"nickname": "全局昵称", "card": ""}
        assert store.profile("a", "u", "g1") == {"nickname": "全局昵称", "card": "自动名"}
        with sqlite3.connect(path) as db:
            row = db.execute(
                "SELECT nickname, card, source, notification_name FROM user_profiles "
                "WHERE account='a' AND user_id='u' AND group_id='g1'").fetchone()
        assert row == ("", "自动名", "notification", "自动名")

        # A later roster/message observation changes source but must keep the
        # automatic name identifiable instead of turning it into a profile.
        store.remember_profiles("a", [("u", "g1", "新昵称", "新卡片", "group_member")])
        assert store.notification_name("a", "u", "g1") == "自动名"
        assert store.profile("a", "u", "g1") == {"nickname": "全局昵称", "card": "新卡片"}

        # Repeated identical notifications are a no-op (also the file app+file
        # twin case); a changed name is written again.
        assert store.remember_notification_name("a", "u", "g1", "自动名") is True
        assert store.remember_notification_name("a", "u", "g1", "自动名") is False
        assert store.notification_name("a", "u", "g1") == "自动名"


def test_notification_names_are_isolated_per_account_and_group(tmp_path):
    path = tmp_path / "messages.sqlite3"
    with MessageStore(path) as store:
        assert store.remember_notification_name("a", "u", "g1", "甲群名片") is True
        store.remember_profiles("b", [("u", "", "B昵称", "", "friend")])
        assert store.notification_name("a", "u", "g1") == "甲群名片"
        assert store.notification_name("a", "u", "g2") == ""
        assert store.notification_name("b", "u", "g1") == ""
        assert store.notification_name("a", "u", "") == ""
        assert store.remember_notification_name("a", "u", "", "空群") is False
        assert store.remember_notification_name("a", "", "g1", "空用户") is False
        assert store.remember_notification_name("a", "u", "g1", "   ") is False
        assert store.remember_notification_name("a", "u", "g2", "乙群名片") is True
        assert store.notification_name("a", "u", "g1") == "甲群名片"
        assert store.notification_name("a", "u", "g2") == "乙群名片"


@pytest.mark.asyncio
async def test_group_notification_name_is_stored_separately_and_shown_on_card(tmp_path):
    service, driver, mapper, events = await make_service(tmp_path)
    try:
        service.message_store.remember_profiles("wxid_robot", [
            ("wxid_alice", "", "全局昵称", "", "friend")])
        driver.members["10001@chatroom"] = ()
        await driver.inject_message(
            chat_id="10001@chatroom", sender_id="wxid_alice", sender_name="",
            is_group=True, notification_name="群里的甲",
            notification_name_source="template")
        sender = events[-1]["sender"]
        assert sender["card"] == "群里的甲"
        assert sender["nickname"] == "全局昵称"
        store = service.message_store
        assert store.notification_name(
            "wxid_robot", "wxid_alice", "10001@chatroom") == "群里的甲"
        assert store.profile("wxid_robot", "wxid_alice") == {
            "nickname": "全局昵称", "card": ""}
        # Only one event-driven global row exists; the notification name lives on
        # the group row and never becomes the global nickname.
        assert store.profile("wxid_robot", "wxid_alice", "10001@chatroom") == {
            "nickname": "全局昵称", "card": "群里的甲"}
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_notification_name_switch_off_keeps_display_but_stops_learning(tmp_path):
    service, driver, mapper, events = await make_service(
        tmp_path, sdk_notification_names=False)
    try:
        await driver.inject_message(
            chat_id="10001@chatroom", sender_id="wxid_alice", sender_name="",
            is_group=True, notification_name="群里的甲",
            notification_name_source="sender_display_name")
        assert events[-1]["sender"]["card"] == "群里的甲"
        assert service.message_store.notification_name(
            "wxid_robot", "wxid_alice", "10001@chatroom") == ""
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_own_account_and_private_notification_names_are_not_learned(tmp_path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        await driver.inject_message(
            chat_id="10001@chatroom", sender_id="wxid_robot", sender_name="",
            is_group=True, notification_name="机器人",
            notification_name_source="sender_display_name")
        assert service.message_store.notification_name(
            "wxid_robot", "wxid_robot", "10001@chatroom") == ""
        await driver.inject_message(
            chat_id="wxid_alice", sender_id="wxid_alice", sender_name="",
            is_group=False, notification_name="甲",
            notification_name_source="template")
        assert service.message_store.notification_name(
            "wxid_robot", "wxid_alice", "") == ""
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_invalid_notification_name_is_not_stored(tmp_path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        for value in ("wxid_alice", "x" * 65, "张\u0000三", ""):
            await driver.inject_message(
                chat_id="10001@chatroom", sender_id="wxid_alice", sender_name="",
                is_group=True, notification_name=value,
                notification_name_source="template")
        assert service.message_store.notification_name(
            "wxid_robot", "wxid_alice", "10001@chatroom") == ""
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_stored_messages_never_contain_the_raw_notification_preview(tmp_path):
    """The raw preview crosses real dev18 conversion but must not reach storage."""
    from test_sdk_driver import event as sdk_event
    from wechat_onebot.driver.sdk import SdkDriver

    config = make_config(tmp_path)
    driver = MockDriver()
    mapper = IdMapper(tmp_path / "ids.sqlite3")
    store = MessageStore(tmp_path / "messages.sqlite3")
    media = MediaStore(tmp_path / "media", max_bytes=1024 * 1024,
                       public_base_url="http://127.0.0.1:6700")
    service = OneBotService(config, driver, mapper, media, message_store=store)
    events = []

    async def collect(payload):
        events.append(payload)

    service.set_event_sink(collect)
    sentinel = "SENTINEL-原始通知正文-不应入库"
    try:
        await service.start()
        # Real driver conversion of a dev18 SDK event: the raw preview enters
        # the converter and only the extracted display name may leave it.
        message = SdkDriver(config)._convert_message(sdk_event(
            event_id="evt-sentinel", chat_id="10001@chatroom",
            sender_id="wxid_alice", text="实际消息正文",
            notification_text=f"群里的甲 : {sentinel}"))
        assert message is not None
        assert message.notification_name == "群里的甲"
        assert sentinel not in repr(message.raw)
        await service._on_driver_event(message)

        assert events[-1]["sender"]["card"] == "群里的甲"
        assert store.notification_name(
            "wxid_robot", "wxid_alice", "10001@chatroom") == "群里的甲"
        history = store.history(account="wxid_robot",
                                session_key="10001@chatroom", count=10)
        assert history
        # The learned name is stored; the raw preview body is not.
        assert "群里的甲" in repr(history)
        assert sentinel not in repr(history)
        assert sentinel not in repr(events[-1])
        database = store.path.read_bytes()
        wal = store.path.with_name(store.path.name + "-wal")
        if wal.is_file():
            database += wal.read_bytes()
        assert sentinel.encode("utf-8") not in database
    finally:
        await service.stop()
        mapper.close()
        store.close()
