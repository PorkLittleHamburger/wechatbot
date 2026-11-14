from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from test_service import make_service
from wechat_onebot.driver.base import DriverError
from wechat_onebot.onebot.message_store import MessageStore
from wechat_onebot.service import OneBotActionError


def test_mention_labels_persist_and_isolate_accounts(tmp_path: Path):
    path = tmp_path / "messages.sqlite3"
    with MessageStore(path) as store:
        store.set_mention_label("a", "wxid_alice", "g1", "Alice")
        store.set_mention_label("a", "wxid_bob", "g1", "Bob")
        store.set_mention_label("b", "wxid_alice", "g1", "Other")
        assert store.mention_label("a", "wxid_alice", "g1") == "Alice"
        assert store.list_mention_labels("a", "g1") == [
            {"group": "g1", "user": "wxid_alice", "label": "Alice"},
            {"group": "g1", "user": "wxid_bob", "label": "Bob"},
        ]
        store.delete_mention_label("a", "wxid_alice", "g1")
    with MessageStore(path) as store:
        assert store.mention_label("a", "wxid_alice", "g1") == ""
        assert store.mention_label("b", "wxid_alice", "g1") == "Other"
        assert store.list_mention_labels("a", "g1") == [
            {"group": "g1", "user": "wxid_bob", "label": "Bob"},
        ]


@pytest.mark.asyncio
async def test_manual_mention_label_is_sent_as_unverified_real_name(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", "10001@chatroom")
        user = mapper.get_or_create("user", "wxid_alice")
        service.set_mention_label(group, user, "群里的真实名")
        assert service.mention_labels(group) == [{
            "group_id": group, "user_id": user, "label": "群里的真实名"}]
        response = await service.dispatch("send_group_msg", {
            "group_id": group,
            "message": [{"type": "at", "data": {"qq": user}},
                        {"type": "text", "data": {"text": "hi"}}],
        })
        assert response["status"] == "ok"
        payload = driver.sent[-1].payload
        assert payload["user_ids"] == ["wxid_alice"]
        assert payload["display_names"] == ["群里的真实名"]
        assert payload["labels_verified"] is False
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_cached_mention_label_stays_verified(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", "10001@chatroom")
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
async def test_missing_mention_label_lets_sdk_resolve_or_falls_back_to_text(tmp_path: Path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", "10001@chatroom")
        user = mapper.get_or_create("user", "wxid_alice")
        driver.members["10001@chatroom"] = ()
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
async def test_mention_label_validation_rejects_wxid_and_blank(tmp_path: Path):
    service, _, mapper, _ = await make_service(tmp_path)
    try:
        group = mapper.get_or_create("group", "10001@chatroom")
        user = mapper.get_or_create("user", "wxid_alice")
        with pytest.raises(OneBotActionError):
            service.set_mention_label(group, user, "wxid_alice")
        with pytest.raises(OneBotActionError):
            service.set_mention_label(group, user, "   ")
        with pytest.raises(OneBotActionError):
            service.set_mention_label(group, user, "x" * 65)
    finally:
        await service.stop()
        mapper.close()
