from __future__ import annotations

import asyncio

import pytest

from test_service import make_service
from wechat_onebot.models import NoticeEvent
from wechat_onebot.models import IncomingMessage, Segment


@pytest.mark.asyncio
async def test_cq_object_auto_escape_and_sent_snapshot(tmp_path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = (await service.dispatch("get_group_list"))["data"][0]["group_id"]
        user = (await service.dispatch("get_friend_list"))["data"][0]["user_id"]
        response = await service.dispatch("send_group_msg", {
            "group_id": str(group), "message": f"[CQ:at,qq={user}] hi &amp; &#91;ok&#93;"})
        assert response["status"] == "ok"
        assert driver.sent[-1].kind == "at_text"
        assert driver.sent[-1].payload["text"] == " hi & [ok]"
        snapshot = (await service.dispatch("get_msg", response["data"]))["data"]
        assert snapshot["message"][0] == {"type": "at", "data": {"qq": str(user)}}
        assert "[CQ:at," in snapshot["raw_message"]
        assert snapshot["sender"]["user_id"] == service.self_id
        response = await service.dispatch("send_private_msg", {
            "user_id": user, "message": {"type": "text", "data": {"text": "single"}}})
        assert response["status"] == "ok" and driver.sent[-1].payload["text"] == "single"
        response = await service.dispatch("send_private_msg", {
            "user_id": user, "message": "[CQ:at,qq=all] &amp;", "auto_escape": "true"})
        assert response["status"] == "ok"
        assert driver.sent[-1].payload["text"] == "[CQ:at,qq=all] &amp;"
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_unsupported_reply_is_atomic_and_mention_only_is_supported(tmp_path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        group = (await service.dispatch("get_group_list"))["data"][0]["group_id"]
        user = (await service.dispatch("get_friend_list"))["data"][0]["user_id"]
        result = await service.dispatch("send_group_msg", {"group_id": group, "message": [
            {"type": "text", "data": {"text": "must not send"}},
            {"type": "reply", "data": {"id": "123"}}]})
        assert result["retcode"] == 1404 and not driver.sent
        result = await service.dispatch("send_group_msg", {"group_id": group,
            "message": {"type": "at", "data": {"qq": str(user)}}})
        assert result["status"] == "ok" and driver.sent[-1].kind == "at_text"
        assert driver.sent[-1].payload["user_ids"] == ["wxid_alice"]
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_async_rate_limit_echo_and_shutdown(tmp_path):
    service, driver, mapper, _ = await make_service(tmp_path)
    try:
        user = (await service.dispatch("get_friend_list"))["data"][0]["user_id"]
        service.rate_limit_interval_ms = 30
        times = []
        original_send = driver.send_text

        async def send(chat, text):
            times.append(asyncio.get_running_loop().time())
            return await original_send(chat, text)

        driver.send_text = send
        assert "echo" not in await service.dispatch("get_status")
        assert (await service.dispatch("get_status", echo=None))["echo"] is None
        for value in ("first", "second"):
            result = await service.dispatch("send_private_msg_rate_limited", {
                "user_id": user, "message": value}, echo={"seq": value})
            assert result == {"status": "async", "retcode": 1, "data": None, "echo": {"seq": value}}
        await asyncio.wait_for(service._rate_queue.join(), 2)
        assert [row.payload["text"] for row in driver.sent] == ["first", "second"]
        assert times[1] - times[0] >= .025
        result = await service.dispatch("get_status_async", echo=False)
        assert result["status"] == "async" and result["echo"] is False
        await asyncio.sleep(0)
    finally:
        await service.stop()
        assert not service._action_tasks
        mapper.close()


@pytest.mark.asyncio
async def test_recall_reuses_original_message_id_and_drops_unknown(tmp_path):
    service, driver, mapper, events = await make_service(tmp_path)
    try:
        await driver.inject_message(chat_id="wxid_alice", message_id="9001", text="original")
        original_id = events[-1]["message_id"]
        await service._on_driver_event(NoticeEvent("friend_recall", "wxid_alice", "wxid_alice",
            raw={"recall": {"message_id": "9001"}}))
        assert events[-1]["message_id"] == original_id
        assert (await service.dispatch("get_msg", {"message_id": original_id}))["status"] == "ok"
        count = len(events)
        await service._on_driver_event(NoticeEvent("friend_recall", "wxid_alice", "wxid_alice",
            raw={"recall": {"message_id": "9002"}}))
        assert len(events) == count
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_private_recall_without_sender_uses_the_peer(tmp_path):
    service, driver, mapper, events = await make_service(tmp_path)
    try:
        await driver.inject_message(chat_id="wxid_alice", message_id="9001", text="original")
        original_id = events[-1]["message_id"]
        await service._on_driver_event(NoticeEvent(
            "friend_recall", "wxid_alice", "", raw={"recall": {"message_id": "9001"}}))
        notice = events[-1]
        assert notice["notice_type"] == "friend_recall"
        assert notice["message_id"] == original_id
        assert notice["user_id"] == mapper.get_or_create("user", "wxid_alice")
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_sent_message_native_id_resolves_a_later_quote(tmp_path, monkeypatch):
    service, driver, mapper, events = await make_service(tmp_path)
    pending = ["900123"]

    def take_native():
        return (pending.pop(0),) if pending else ()

    monkeypatch.setattr(driver, "take_native_message_ids", take_native)
    try:
        alice = mapper.get_or_create("user", "wxid_alice")
        sent = await service.dispatch("send_private_msg", {"user_id": alice, "message": "self msg"})
        sent_id = sent["data"]["message_id"]
        cached = service.message_store.native_message(service.account, "wxid_alice", "900123")
        assert cached is not None and cached["message_id"] == sent_id
        await service._on_driver_event(IncomingMessage(
            external_chat_id="wxid_alice", external_sender_id="wxid_alice",
            segments=(Segment("text", {"text": "quoted"}),), sequence=77,
            raw={"reply": {"native_message_id": "900123", "sender_id": "wxid_robot",
                           "type": 1, "content": "self msg"}}))
        payload = events[-1]
        assert payload["message"][0] == {"type": "reply", "data": {"id": str(sent_id)}}
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_quick_reply_and_unknown_profile_fields(tmp_path):
    service, driver, mapper, events = await make_service(tmp_path)
    try:
        await driver.inject_message(chat_id="10001@chatroom", is_group=True, message_id="123")
        event = events[-1]
        await service.handle_quick_operation(event, {"reply": "hello"})
        assert driver.sent[-1].kind == "at_text"
        await service.handle_quick_operation(event, {"reply": "plain", "at_sender": False})
        assert driver.sent[-1].kind == "text"
        info = await service.dispatch("get_stranger_info", {"user_id": event["user_id"], "no_cache": "false"})
        assert info["data"]["sex"] == "unknown" and info["data"]["age"] == 0
        members = (await service.dispatch("get_group_member_list", {"group_id": event["group_id"]}))["data"]
        assert members[0]["role"] in ("owner", "admin", "member")
        assert type(members[0]["card_changeable"]) is bool
        count = len(driver.sent)
        await service.handle_quick_operation(event, {"reply": "independent", "ban": True})
        assert len(driver.sent) == count + 1
        assert driver.sent[-1].payload["text"] == "independent"
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["_async", "_rate_limited"])
async def test_shutdown_drains_inflight_native_thread_before_driver_close(tmp_path, suffix):
    import threading

    service, driver, mapper, _ = await make_service(tmp_path)
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    stop_called = []
    original_stop = driver.stop

    def native_send():
        entered.set()
        assert release.wait(5)
        completed.set()
        return True

    async def send(chat, text):
        return await asyncio.to_thread(native_send)

    async def close():
        stop_called.append(completed.is_set())
        await original_stop()

    driver.send_text, driver.stop = send, close
    try:
        user = (await service.dispatch("get_friend_list"))["data"][0]["user_id"]
        result = await service.dispatch("send_private_msg" + suffix, {"user_id": user, "message": "owned"})
        assert result["status"] == "async"
        assert await asyncio.to_thread(entered.wait, 2)
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(.03)
        assert not stopping.done() and not stop_called
        release.set()
        await asyncio.wait_for(stopping, 2)
        assert stop_called == [True]
    finally:
        release.set()
        if service.started:
            await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_received_same_basename_images_roundtrip_unchanged_after_restart(tmp_path):
    from wechat_onebot.media import MediaStore

    service, driver, mapper, events = await make_service(tmp_path)
    try:
        for folder, content in (("a", b"first"), ("b", b"second")):
            image = tmp_path / folder / "same.png"
            image.parent.mkdir()
            image.write_bytes(content)
            await service._on_driver_event(IncomingMessage("wxid_alice", "wxid_alice",
                (Segment("image", {"file": str(image)}),), external_message_id=folder))
        first, second = events[-2:]
        assert first["message"][0]["data"]["file"] != second["message"][0]["data"]["file"]
        service.media = MediaStore(service.media.media_dir, max_bytes=1024, public_base_url="http://test")
        for event, content in ((first, b"first"), (second, b"second")):
            result = await service.dispatch("send_private_msg", {"user_id": event["user_id"],
                "message": event["message"]})
            assert result["status"] == "ok"
            from pathlib import Path
            assert Path(driver.sent[-1].payload["path"]).read_bytes() == content
    finally:
        await service.stop()
        mapper.close()


@pytest.mark.asyncio
async def test_cancelled_http_quick_reply_retains_native_send_ownership(tmp_path):
    import threading

    service, driver, mapper, events = await make_service(tmp_path)
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    closed = []
    original_stop = driver.stop

    def native_send():
        entered.set()
        assert release.wait(5)
        completed.set()
        return True

    async def send(chat, text):
        return await asyncio.to_thread(native_send)

    async def close():
        closed.append(completed.is_set())
        await original_stop()

    driver.send_text, driver.stop = send, close
    try:
        await driver.inject_message(text="question")
        reporter = asyncio.create_task(service.handle_quick_operation(events[-1], {"reply": "answer"}))
        assert await asyncio.to_thread(entered.wait, 2)
        reporter.cancel()  # HTTP adapter shutdown must not cancel the native send.
        await asyncio.gather(reporter, return_exceptions=True)
        assert service._action_tasks
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(.03)
        assert not stopping.done() and not closed
        release.set()
        await asyncio.wait_for(stopping, 2)
        assert closed == [True]
    finally:
        release.set()
        if service.started:
            await service.stop()
        mapper.close()
