"""Outbound group-@ name priority and degradation.

Priority per target: verified SDK cache name -> the target's own automatic
notification name (unverified) -> another local account's cache name
(unverified) -> historical profile.  A notification name is never verified,
never reused for another target/group and never a wxid.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from test_service import make_service
from wechat_onebot.driver.base import DriverError


GROUP = "10001@chatroom"


def at_message(*targets: int) -> list[dict]:
    message: list[dict] = []
    for index, target in enumerate(targets):
        if index:
            message.append({"type": "text", "data": {"text": " "}})
        message.append({"type": "at", "data": {"qq": target}})
    message.append({"type": "text", "data": {"text": "hi"}})
    return message


@pytest.mark.asyncio
async def test_learned_notification_name_is_reused_for_a_later_at(tmp_path: Path):
    service, driver, mapper, events = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        user = mapper.get_or_create("user", "wxid_alice")
        driver.members[GROUP] = ()
        driver.resolve_mention_label = AsyncMock(return_value=None)
        driver.resolve_foreign_mention_label = AsyncMock(return_value=None)
        # Inbound group notification teaches this sender's own display name.
        await driver.inject_message(
            chat_id=GROUP, sender_id="wxid_alice", sender_name="", is_group=True,
            notification_name="群里的甲", notification_name_source="template")
        assert events[-1]["sender"]["card"] == "群里的甲"
        response = await service.dispatch("send_group_msg", {
            "group_id": group, "message": at_message(user)})
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert driver.sent[-1].kind == "at_text"
        assert payload["user_ids"] == ["wxid_alice"]
        assert payload["display_names"] == ["群里的甲"]
        assert payload["labels_verified"] is False
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_learned_notification_name_keeps_special_characters(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        user = mapper.get_or_create("user", "wxid_alice")
        service.message_store.remember_notification_name(
            service.account, "wxid_alice", GROUP, "🙂小明·A")
        response = await service.dispatch("send_group_msg", {
            "group_id": group, "message": at_message(user)})
        assert response["status"] == "ok"
        assert driver.sent[-1].payload["display_names"] == ["🙂小明·A"]
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_learned_notification_name_survives_a_later_profile_observation(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        user = mapper.get_or_create("user", "wxid_alice")
        service.message_store.remember_notification_name(
            service.account, "wxid_alice", GROUP, "自动名")
        # A later roster/message observation changes the source but must not
        # turn the automatic name into a verified profile name.
        service.message_store.remember_profiles(service.account, [
            ("wxid_alice", GROUP, "", "", "message")])
        driver.members[GROUP] = ()
        driver.resolve_mention_label = AsyncMock(return_value=None)
        driver.resolve_foreign_mention_label = AsyncMock(return_value=None)
        response = await service.dispatch("send_group_msg", {
            "group_id": group, "message": at_message(user)})
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert payload["display_names"] == ["自动名"]
        assert payload["labels_verified"] is False
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_cached_mention_label_stays_verified(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        user = mapper.get_or_create("user", "wxid_alice")
        driver.resolve_mention_label = AsyncMock(return_value="缓存真实名")
        response = await service.dispatch("send_group_msg", {
            "group_id": group,
            "message": [{"type": "at", "data": {"qq": user}},
                        {"type": "text", "data": {"text": "hi"}}],
        })
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert payload["display_names"] == ["缓存真实名"]
        assert payload["labels_verified"] is True
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_cache_name_wins_over_a_learned_notification_name(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        user = mapper.get_or_create("user", "wxid_alice")
        service.message_store.remember_notification_name(
            service.account, "wxid_alice", GROUP, "自动旧名")
        driver.resolve_mention_label = AsyncMock(return_value="缓存当前名")
        response = await service.dispatch("send_group_msg", {
            "group_id": group,
            "message": [{"type": "at", "data": {"qq": user}},
                        {"type": "text", "data": {"text": "hi"}}],
        })
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert payload["display_names"] == ["缓存当前名"]
        assert payload["labels_verified"] is True
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_missing_mention_label_lets_sdk_resolve_or_falls_back_to_text(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        user = mapper.get_or_create("user", "wxid_alice")
        driver.members[GROUP] = ()
        driver.resolve_mention_label = AsyncMock(return_value=None)
        response = await service.dispatch("send_group_msg", {
            "group_id": group,
            "message": [{"type": "at", "data": {"qq": user}},
                        {"type": "text", "data": {"text": "hi"}}],
        })
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert payload["display_names"] == []
        assert payload["labels_verified"] is True

        async def reject(*args, **kwargs):
            raise DriverError("发送失败: INVALID_ARGUMENT No verified name is cached")

        driver.send_at_text = reject
        response = await service.dispatch("send_group_msg", {
            "group_id": group,
            "message": [{"type": "at", "data": {"qq": user}},
                        {"type": "text", "data": {"text": "fallback"}}],
        })
        assert response["status"] == "ok"
        assert driver.sent[-1].kind == "text"
        assert "@wxid_alice" in driver.sent[-1].payload["text"]
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_foreign_account_cache_name_is_used_when_own_cache_missing(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        user = mapper.get_or_create("user", "wxid_alice")
        driver.resolve_mention_label = AsyncMock(return_value=None)
        driver.resolve_foreign_mention_label = AsyncMock(return_value="外账号名")
        response = await service.dispatch("send_group_msg", {
            "group_id": group,
            "message": [{"type": "at", "data": {"qq": user}},
                        {"type": "text", "data": {"text": "hi"}}],
        })
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert payload["display_names"] == ["外账号名"]
        assert payload["labels_verified"] is False
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_notification_name_wins_over_foreign_account_name(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        user = mapper.get_or_create("user", "wxid_alice")
        service.message_store.remember_notification_name(
            service.account, "wxid_alice", GROUP, "自动真名")
        driver.resolve_mention_label = AsyncMock(return_value=None)
        driver.resolve_foreign_mention_label = AsyncMock(return_value="外账号名")
        response = await service.dispatch("send_group_msg", {
            "group_id": group,
            "message": [{"type": "at", "data": {"qq": user}},
                        {"type": "text", "data": {"text": "hi"}}],
        })
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert payload["display_names"] == ["自动真名"]
        assert payload["labels_verified"] is False
        driver.resolve_foreign_mention_label.assert_not_called()
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_stale_profile_falls_back_to_visible_text_without_duplicate(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        user = mapper.get_or_create("user", "wxid_alice")
        service.message_store.remember_profiles(service.account, [
            ("wxid_alice", "", "旧资料名", "", "message")])
        driver.members[GROUP] = ()
        calls = 0

        async def reject(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise DriverError("发送失败: NOT_VERIFIED stale profile label")

        driver.send_at_text = reject
        response = await service.dispatch("send_group_msg", {
            "group_id": group,
            "message": [{"type": "at", "data": {"qq": user}},
                        {"type": "text", "data": {"text": "fallback"}}],
        })
        assert response["status"] == "ok"
        assert calls == 1
        assert driver.sent[-1].kind == "text"
        assert "@旧资料名" in driver.sent[-1].payload["text"]
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_notification_and_cached_names_use_one_unverified_native_attempt(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        alice = mapper.get_or_create("user", "wxid_alice")
        bob = mapper.get_or_create("user", "wxid_bob")
        service.message_store.remember_notification_name(
            service.account, "wxid_alice", GROUP, "自动名")
        driver.resolve_mention_label = AsyncMock(
            side_effect=lambda _chat, user: "缓存名" if user == "wxid_bob" else None)
        response = await service.dispatch("send_group_msg", {
            "group_id": group, "message": at_message(alice, bob)})
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert payload["display_names"] == ["自动名", "缓存名"]
        assert payload["labels_verified"] is False
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_at_only_qq_segment_uses_native_mention_without_text(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        user = mapper.get_or_create("user", "wxid_alice")
        response = await service.dispatch("send_group_msg", {
            "group_id": group,
            "message": [{"type": "at", "data": {"qq": user}}],
        })
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert driver.sent[-1].kind == "at_text"
        assert payload["user_ids"] == ["wxid_alice"]
        assert payload["display_names"] == ["Alice"]
        assert payload["labels_verified"] is True
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_cache_notification_foreign_priority_in_one_batch(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        alice = mapper.get_or_create("user", "wxid_alice")
        bob = mapper.get_or_create("user", "wxid_bob")
        carol = mapper.get_or_create("user", "wxid_carol")
        service.message_store.remember_notification_name(
            service.account, "wxid_bob", GROUP, "自动名")
        driver.resolve_mention_label = AsyncMock(
            side_effect=lambda _chat, user: "缓存名" if user == "wxid_alice" else None)
        driver.resolve_foreign_mention_label = AsyncMock(
            side_effect=lambda _chat, user: "外账号名" if user == "wxid_carol" else None)
        response = await service.dispatch("send_group_msg", {
            "group_id": group, "message": at_message(alice, bob, carol)})
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert payload["display_names"] == ["缓存名", "自动名", "外账号名"]
        assert payload["labels_verified"] is False
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_notification_and_profile_labels_degrade_without_a_native_attempt(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        alice = mapper.get_or_create("user", "wxid_alice")
        bob = mapper.get_or_create("user", "wxid_bob")
        service.message_store.remember_notification_name(
            service.account, "wxid_alice", GROUP, "自动名")
        service.message_store.remember_profiles(service.account, [
            ("wxid_bob", GROUP, "", "资料卡片", "group_member")])
        driver.members[GROUP] = ()
        driver.send_at_text = AsyncMock(side_effect=AssertionError("must degrade"))
        response = await service.dispatch("send_group_msg", {
            "group_id": group, "message": at_message(alice, bob)})
        assert response["status"] == "ok"
        assert driver.sent[-1].kind == "text"
        text = driver.sent[-1].payload["text"]
        assert "@自动名" in text and "@资料卡片" in text
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_missing_target_with_a_notification_name_degrades_the_batch(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        alice = mapper.get_or_create("user", "wxid_alice")
        bob = mapper.get_or_create("user", "wxid_bob")
        service.message_store.remember_notification_name(
            service.account, "wxid_alice", GROUP, "自动名")
        driver.members[GROUP] = ()
        driver.resolve_mention_label = AsyncMock(return_value=None)
        driver.resolve_foreign_mention_label = AsyncMock(return_value=None)
        driver.send_at_text = AsyncMock(side_effect=AssertionError("must degrade"))
        response = await service.dispatch("send_group_msg", {
            "group_id": group, "message": at_message(alice, bob)})
        assert response["status"] == "ok"
        assert driver.sent[-1].kind == "text"
        text = driver.sent[-1].payload["text"]
        assert "@自动名" in text and "@wxid_bob" in text
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_profile_in_mixed_batch_degrades_entire_visible_text(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        alice = mapper.get_or_create("user", "wxid_alice")
        bob = mapper.get_or_create("user", "wxid_bob")
        carol = mapper.get_or_create("user", "wxid_carol")
        service.message_store.remember_profiles(service.account, [
            ("wxid_carol", "", "资料名", "", "message")])
        driver.resolve_mention_label = AsyncMock(
            side_effect=lambda _chat, user: "缓存名" if user == "wxid_alice" else None)
        driver.resolve_foreign_mention_label = AsyncMock(
            side_effect=lambda _chat, user: "外账号名" if user == "wxid_bob" else None)
        driver.send_at_text = AsyncMock(side_effect=AssertionError("must degrade"))
        response = await service.dispatch("send_group_msg", {
            "group_id": group, "message": at_message(alice, bob, carol)})
        assert response["status"] == "ok"
        assert driver.sent[-1].kind == "text"
        text = driver.sent[-1].payload["text"]
        assert "@缓存名" in text and "@外账号名" in text and "@资料名" in text
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_wxid_is_never_passed_as_a_native_label(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        user = mapper.get_or_create("user", "wxid_ghost")
        # Legacy/cache/notification data can disagree with the stored guard.
        service.message_store.remember_notification_name(
            service.account, "wxid_ghost", GROUP, "wxid_ghost")
        driver.resolve_mention_label = AsyncMock(return_value="wxid_ghost")
        driver.resolve_foreign_mention_label = AsyncMock(return_value="wxid_ghost")
        response = await service.dispatch("send_group_msg", {
            "group_id": group,
            "message": [{"type": "at", "data": {"qq": user}},
                        {"type": "text", "data": {"text": "hi"}}],
        })
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert payload["display_names"] == []
        assert "wxid_ghost" not in (payload["display_names"] or [])
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_notification_name_is_not_reused_for_another_target(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        alice = mapper.get_or_create("user", "wxid_alice")
        bob = mapper.get_or_create("user", "wxid_bob")
        service.message_store.remember_notification_name(
            service.account, "wxid_alice", GROUP, "甲的名")
        driver.members[GROUP] = ()
        driver.resolve_mention_label = AsyncMock(return_value=None)
        driver.resolve_foreign_mention_label = AsyncMock(return_value=None)
        response = await service.dispatch("send_group_msg", {
            "group_id": group, "message": at_message(bob)})
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert payload["display_names"] == []
        assert "甲的名" not in (payload["display_names"] or [])
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_notification_name_is_not_reused_in_another_group(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        other = mapper.get_or_create("group", "10002@chatroom")
        user = mapper.get_or_create("user", "wxid_alice")
        service.message_store.remember_notification_name(
            service.account, "wxid_alice", GROUP, "甲群的名字")
        driver.members["10002@chatroom"] = ()
        driver.resolve_mention_label = AsyncMock(return_value=None)
        driver.resolve_foreign_mention_label = AsyncMock(return_value=None)
        response = await service.dispatch("send_group_msg", {
            "group_id": other, "message": at_message(user)})
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert payload["display_names"] == []
        assert "甲群的名字" not in (payload["display_names"] or [])
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_switch_off_ignores_learned_names_and_notification_cards(tmp_path: Path):
    service, driver, mapper, _ = await make_service(
        tmp_path, sdk_notification_names=False)
    try:
        group = mapper.get_or_create("group", GROUP)
        user = mapper.get_or_create("user", "wxid_alice")
        # An old automatic name from an earlier run, later observed by a roster.
        service.message_store.remember_notification_name(
            service.account, "wxid_alice", GROUP, "旧自动名")
        service.message_store.remember_profiles(service.account, [
            ("wxid_alice", "", "全局昵称", "", "group_member")])
        driver.members[GROUP] = ()
        driver.resolve_mention_label = AsyncMock(return_value=None)
        driver.resolve_foreign_mention_label = AsyncMock(return_value=None)
        response = await service.dispatch("send_group_msg", {
            "group_id": group, "message": at_message(user)})
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        # The learned name is not used, and it also does not resurface as a
        # "verified" profile card; the remaining global nickname is used.
        assert payload["display_names"] == ["全局昵称"]
        assert payload["labels_verified"] is True
        assert "旧自动名" not in (payload["display_names"] or [])
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_switch_off_ignores_a_notification_only_target(tmp_path: Path):
    service, driver, mapper, _ = await make_service(
        tmp_path, sdk_notification_names=False)
    try:
        group = mapper.get_or_create("group", GROUP)
        user = mapper.get_or_create("user", "wxid_carol")
        service.message_store.remember_notification_name(
            service.account, "wxid_carol", GROUP, "旧自动名")
        driver.members[GROUP] = ()
        driver.resolve_mention_label = AsyncMock(return_value=None)
        driver.resolve_foreign_mention_label = AsyncMock(return_value=None)
        response = await service.dispatch("send_group_msg", {
            "group_id": group, "message": at_message(user)})
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert payload["display_names"] == []
        assert "旧自动名" not in (payload["display_names"] or [])
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_switch_off_does_not_learn_from_new_events(tmp_path: Path):
    service, driver, mapper, _ = await make_service(
        tmp_path, sdk_notification_names=False)
    try:
        group = mapper.get_or_create("group", GROUP)
        user = mapper.get_or_create("user", "wxid_alice")
        driver.members[GROUP] = ()
        driver.resolve_mention_label = AsyncMock(return_value=None)
        driver.resolve_foreign_mention_label = AsyncMock(return_value=None)
        await driver.inject_message(
            chat_id=GROUP, sender_id="wxid_alice", sender_name="", is_group=True,
            notification_name="新自动名", notification_name_source="sender_display_name")
        assert service.message_store.notification_name(
            service.account, "wxid_alice", GROUP) == ""
        response = await service.dispatch("send_group_msg", {
            "group_id": group, "message": at_message(user)})
        assert response["status"] == "ok"
        assert driver.sent[-1].payload["display_names"] == []
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_cache_plus_profile_stays_verified_native(tmp_path: Path):
    """Existing behaviour: cache and profile are both treated as verified."""
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", GROUP)
        alice = mapper.get_or_create("user", "wxid_alice")
        bob = mapper.get_or_create("user", "wxid_bob")
        service.message_store.remember_profiles(service.account, [
            ("wxid_bob", GROUP, "", "资料卡片", "group_member")])
        driver.members[GROUP] = ()
        driver.resolve_mention_label = AsyncMock(
            side_effect=lambda _chat, user: "缓存名" if user == "wxid_alice" else None)
        driver.resolve_foreign_mention_label = AsyncMock(return_value=None)
        response = await service.dispatch("send_group_msg", {
            "group_id": group, "message": at_message(alice, bob)})
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert payload["display_names"] == ["缓存名", "资料卡片"]
        assert payload["labels_verified"] is True
    finally:
        await service.stop()
        mapper.close()
