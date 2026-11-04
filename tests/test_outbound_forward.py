"""Offline Action-level contracts for forward nodes sent as ordinary messages."""
from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import pytest

from test_service import make_service
from test_inbound_replies import helper_path
from wechat_onebot.driver.base import DriverError, DriverUnsupported


def text(value):
    return {"type": "text", "data": {"text": value}}


def node(content, name=""):
    # AstrBot's serialized Node uses name/uin/content, not a native forward ID.
    return {"type": "node", "data": {"name": name, "uin": "10001", "content": content}}


def reference(message_id):
    return {"type": "node", "data": {"id": message_id}}


def forward(forward_id):
    return {"type": "forward", "data": {"id": forward_id}}


@pytest.fixture
async def bot(tmp_path):
    service, driver, mapper, events = await make_service(tmp_path)
    try:
        yield service, driver, mapper, events
    finally:
        await service.stop()
        mapper.close()


async def send(bot, messages, *, private=False):
    service, _, mapper, _ = bot
    if private:
        params = {"user_id": mapper.get_or_create("user", "wxid_alice"), "messages": messages}
        return await service.dispatch("send_private_forward_msg", params)
    params = {"group_id": mapper.get_or_create("group", "10001@chatroom"), "messages": messages}
    return await service.dispatch("send_group_forward_msg", params)


def failed(response, retcode, diagnostic):
    assert response["status"] == "failed", response
    assert response["retcode"] == retcode, response
    assert diagnostic in response["message"], response


def ordinary(response, *, nodes, sends, chains):
    assert response["status"] == "ok", response
    data = response["data"]
    assert data["forward_mode"] == "normal_messages"
    assert data["native_forward"] is False
    assert data["node_count"] == nodes
    assert data["sent_segments"] == sends
    assert len(data["message_ids"]) == chains
    assert len(set(data["message_ids"])) == chains
    assert all(type(mid) is int and mid != 0 and -(2**31) <= mid < 2**31
               for mid in data["message_ids"])
    assert data["message_id"] == data["message_ids"][0]
    return data


@pytest.mark.parametrize("private", [False, True], ids=["group", "private"])
async def test_astrbot_nodes_are_ordinary_stored_messages(bot, private, monkeypatch):
    service, driver, _, events = bot

    async def forbidden_native_forward(*args, **kwargs):
        pytest.fail("forward actions must only use ordinary driver sends")

    monkeypatch.setattr(driver, "send_forward", forbidden_native_forward, raising=False)
    monkeypatch.setattr(driver, "send_group_forward_msg", forbidden_native_forward, raising=False)
    messages = [node([text("first")], "AstrBot"), node("second", "Alice")]
    original = copy.deepcopy(messages)
    data = ordinary(await send(bot, messages, private=private), nodes=2, sends=2, chains=2)
    assert messages == original
    assert [row.kind for row in driver.sent] == ["text", "text"]
    assert [row.chat_id for row in driver.sent] == ["wxid_alice" if private else "10001@chatroom"] * 2
    expected = ["[转发内容 · AstrBot]\nfirst", "[转发内容 · Alice]\nsecond"]
    assert [row.payload["text"] for row in driver.sent] == expected
    for mid, body in zip(data["message_ids"], expected):
        response = await service.dispatch("get_msg", {"message_id": mid})
        assert response["status"] == "ok"
        stored = response["data"]
        assert stored["real_id"] == mid
        assert stored["message_type"] == ("private" if private else "group")
        assert "".join(seg["data"]["text"] for seg in stored["message"]) == body
        assert all(seg["type"] != "forward" for seg in stored["message"])
    assert events == []  # internal sent snapshots are not incoming events


async def test_utf8_chunks_cq_code_blocks_and_author_are_lossless(bot):
    _, driver, _, _ = bot
    body = "```python\nprint('你好🙂')\n```\n" + "汉🙂" * 600 + "\n&#91;CQ:at,qq=123&#93; &amp; end"
    expected = "[转发内容 · Bot  Author]\n" + body.replace("&#91;", "[").replace("&#93;", "]").replace("&amp;", "&")
    response = await send(bot, [node(body, "\tBot\n Author\x7f")])
    chunks = [row.payload["text"] for row in driver.sent]
    ordinary(response, nodes=1, sends=len(chunks), chains=1)
    assert len(chunks) > 3
    assert "".join(chunks) == expected
    assert all(0 < len(chunk.encode("utf-8")) <= 1023 for chunk in chunks)
    assert all(row.kind == "text" for row in driver.sent)


async def test_cq_native_mention_is_distinct_from_author_label(bot):
    _, driver, mapper, _ = bot
    alice = mapper.get_or_create("user", "wxid_alice")
    response = await send(bot, [node(f"[CQ:at,qq={alice}] hello", "Quoted author")])
    ordinary(response, nodes=1, sends=1, chains=1)
    assert driver.sent[0].kind == "at_text"
    assert driver.sent[0].payload["user_ids"] == ["wxid_alice"]
    assert driver.sent[0].payload["text"] == "[转发内容 · Quoted author]\n hello"


async def test_nested_astrbot_messages_wrapper_and_nodes_preserve_order(bot):
    messages = [node([text("before"), {"messages": [node("inner", "Nested")]},
                      node([text("deeper")], "Child"), text("after")], "Outer")]
    ordinary(await send(bot, messages), nodes=3, sends=4, chains=4)
    assert [row.payload["text"] for row in bot[1].sent] == [
        "[转发内容 · Outer]\nbefore", "[转发内容 · Nested]\ninner",
        "[转发内容 · Child]\ndeeper", "[转发内容 · Outer]\nafter",
    ]


def put_message(service, mid, content, *, account=None, name="Stored"):
    service.message_store.put(mid, account=account or service.account,
                              event_name="private_message", session_key="wxid_alice",
                              payload={"message_id": mid, "message": content,
                                       "sender": {"nickname": name}})


async def test_account_local_message_and_forward_refs_expand_and_can_repeat(bot):
    service = bot[0]
    put_message(service, -123, [text("source")])
    # Legacy stored projection is deliberately exercised through canonical_forward.
    service.message_store.put_forward(service.account, "cached", {
        "messages": [{"sender": {"nickname": "Cached"}, "content": [text("record")]}]})
    messages = [reference("-123"), node([forward("cached")]), reference(-123)]
    ordinary(await send(bot, messages), nodes=4, sends=3, chains=3)
    assert [row.payload["text"] for row in bot[1].sent] == [
        "[转发内容 · Stored]\nsource", "[转发内容 · Cached]\nrecord",
        "[转发内容 · Stored]\nsource",
    ]


@pytest.mark.parametrize("kind", ["message", "forward"])
@pytest.mark.parametrize("foreign", [False, True], ids=["missing", "other-account"])
async def test_references_never_resolve_outside_current_account(bot, kind, foreign):
    service = bot[0]
    if foreign:
        if kind == "message":
            put_message(service, 123, [text("secret")], account="other-account")
        else:
            service.message_store.put_forward("other-account", "secret", {"message": [node("secret")]})
    tail = reference(123) if kind == "message" else node([forward("secret")])
    failed(await send(bot, [node("valid first"), tail]), 1404, "当前账号存储")
    assert bot[1].sent == []


@pytest.mark.parametrize("kind", ["message", "forward", "mixed"])
async def test_reference_cycles_fail_before_any_send(bot, kind):
    service = bot[0]
    if kind == "message":
        put_message(service, 123, [reference(124)])
        put_message(service, 124, [reference(123)])
        messages = [reference(123)]
    elif kind == "forward":
        service.message_store.put_forward(service.account, "loop", {"message": [node([forward("loop")])]})
        messages = [node([forward("loop")])]
    else:
        put_message(service, 123, [forward("loop")])
        service.message_store.put_forward(service.account, "loop", {"message": [reference(123)]})
        messages = [reference(123)]
    failed(await send(bot, [node("valid first"), *messages]), 1400, "循环")
    assert bot[1].sent == []


@pytest.mark.parametrize("value", [True, False, 0, "0", "", "1.0", "+1", "１２", 2**31, -(2**31)-1, None])
async def test_reference_ids_require_nonzero_signed_int32(bot, value):
    failed(await send(bot, [node("valid first"), reference(value)]), 1400, "signed int32")
    assert bot[1].sent == []


@pytest.mark.parametrize("bad,diagnostic", [
    ({}, "messages"), ([], "messages"), ([text("not a node")], "node"),
    ([node(42)], "转发内容"), ([node("")], "没有可发送内容"),
    ([node({"messages": []})], "非空"),
    ([node("x", "a" * 129)], "作者名称"),
    ([{"type": "node", "data": {"id": 123, "content": "x"}}], "同时指定"),
])
async def test_malformed_nodes_are_parameter_failures(bot, bad, diagnostic):
    failed(await send(bot, bad), 1400, diagnostic)
    assert bot[1].sent == []


@pytest.mark.parametrize("kind", ["reply", "video", "xml", "json", "music", "contact", "unknown"])
async def test_unsupported_later_node_rejects_entire_forward(bot, kind):
    failed(await send(bot, [node("valid first"), node([{"type": kind, "data": {"id": "123"}}])]),
           1404, "不支持")
    assert bot[1].sent == []


@pytest.mark.parametrize("private", [False, True])
async def test_image_voice_file_use_ordinary_media_sends(bot, tmp_path, private):
    files = []
    for kind, filename in [("image", "picture.png"), ("record", "voice.amr"), ("file", "document.bin")]:
        path = tmp_path / filename
        path.write_bytes(b"synthetic-offline-media")
        files.append({"type": kind, "data": {"file": str(path)}})
    messages = [node([text("before"), *files, text("after")])]
    ordinary(await send(bot, messages, private=private), nodes=1, sends=5, chains=1)
    assert [row.kind for row in bot[1].sent] == ["text", "image", "voice", "file", "text"]
    for row, segment in zip(bot[1].sent[1:4], files):
        assert Path(row.payload["path"]).read_bytes() == Path(segment["data"]["file"]).read_bytes()


async def test_all_media_prepared_before_first_send(bot, tmp_path, monkeypatch):
    path = tmp_path / "exists.bin"
    path.write_bytes(b"fixture")
    resolved = []
    original = bot[0].media.resolve_input

    async def resolve(value, **kwargs):
        assert bot[1].sent == []
        resolved.append(value)
        return await original(value, **kwargs)

    monkeypatch.setattr(bot[0].media, "resolve_input", resolve)
    messages = [node("valid first"), node([{"type": "file", "data": {"file": str(path)}}]),
                node([{"type": "image", "data": {"file": str(tmp_path / "missing.png")}}])]
    failed(await send(bot, messages), 1400, "不存在")
    assert len(resolved) == 2
    assert bot[1].sent == []


@pytest.mark.parametrize("kind,capability", [("text", "text.send"), ("image", "image.send"),
                                           ("record", "voice.send"), ("file", "file.send")])
async def test_missing_driver_capability_is_atomic(bot, tmp_path, monkeypatch, kind, capability):
    path = tmp_path / "media.bin"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(bot[1], "capabilities", bot[1].capabilities - {capability})
    content = [text("second")] if kind == "text" else [{"type": kind, "data": {"file": str(path)}}]
    failed(await send(bot, [node("first"), node(content)]), 1404, "不支持")
    assert bot[1].sent == []


@pytest.mark.parametrize("budget", ["nodes", "segments", "text", "depth", "media", "media-bytes"])
async def test_shared_budgets_reject_whole_forward(bot, tmp_path, budget):
    if budget == "nodes":
        messages = [node("x") for _ in range(201)]
        diagnostic = "节点"
    elif budget == "segments":
        messages = [node([text("x") for _ in range(1000)]), node([text("x") for _ in range(1001)])]
        diagnostic = "消息段"
    elif budget == "text":
        messages = [node("汉" * 44000), node("汉" * 44000)]
        diagnostic = "256 KiB"
    elif budget == "depth":
        nested = node("x")
        for _ in range(8):
            nested = node({"messages": [nested]})
        messages = [nested]
        diagnostic = "8 层"
    else:
        path = tmp_path / "media.bin"
        size = 8 * 1024 * 1024 if budget == "media-bytes" else 1
        with path.open("wb") as stream:
            stream.truncate(size)
        bot[0].media.max_bytes = size
        count = 9 if budget == "media-bytes" else 17
        messages = [node([{"type": "file", "data": {"file": str(path)}}]) for _ in range(count)]
        diagnostic = "额度"
    failed(await send(bot, messages), 1400, diagnostic)
    assert bot[1].sent == []


async def test_text_send_estimate_joins_segments_and_includes_author(bot):
    # Counting each short text segment as a send used to spuriously exceed 512.
    messages = [node([text("x") for _ in range(600)], "Author")]
    ordinary(await send(bot, messages), nodes=1, sends=1, chains=1)
    assert bot[1].sent[0].payload["text"] == "[转发内容 · Author]\n" + "x" * 600


@pytest.mark.parametrize("budget", ["nodes", "segments", "text", "depth"])
async def test_expansion_budget_boundaries_accept_valid_input(bot, budget):
    if budget == "nodes":
        messages, nodes, chains = [node("x") for _ in range(200)], 200, 200
    elif budget == "segments":
        messages, nodes, chains = [node([text("x") for _ in range(2000)])], 1, 1
    elif budget == "text":
        messages, nodes, chains = [node("x" * (256 * 1024))], 1, 1
    else:
        nested = node("leaf")
        for _ in range(7):
            nested = node([nested])
        messages, nodes, chains = [nested], 8, 1
    response = await send(bot, messages)
    ordinary(response, nodes=nodes, sends=len(bot[1].sent), chains=chains)
    assert bot[1].sent


async def test_repeated_forward_references_share_node_budget(bot):
    service = bot[0]
    service.message_store.put_forward(service.account, "hundred", {
        "message": [node("x") for _ in range(100)]})
    failed(await send(bot, [node([forward("hundred"), forward("hundred")])]), 1400, "节点")
    assert bot[1].sent == []


async def test_author_prefix_counts_toward_text_budget(bot):
    failed(await send(bot, [node("x" * (256 * 1024), "Author")]), 1400, "256 KiB")
    assert bot[1].sent == []


async def test_current_account_forward_wins_over_identical_foreign_id(bot):
    service = bot[0]
    service.message_store.put_forward("other-account", "same-id", {"message": [node("secret")]})
    service.message_store.put_forward(service.account, "same-id", {"message": [node("local")]})
    ordinary(await send(bot, [node([forward("same-id")])]), nodes=2, sends=1, chains=1)
    assert bot[1].sent[0].payload["text"] == "local"


@pytest.mark.parametrize("private", [False, True])
async def test_missing_messages_and_unknown_target_are_action_errors(bot, private):
    service, driver, mapper, _ = bot
    action = "send_private_forward_msg" if private else "send_group_forward_msg"
    key, kind, external = ("user_id", "user", "wxid_alice") if private else (
        "group_id", "group", "10001@chatroom")
    failed(await service.dispatch(action, {key: mapper.get_or_create(kind, external)}), 1400, "messages")
    failed(await service.dispatch(action, {key: 987654321, "messages": [node("x")]}), 1400, "未知")
    assert driver.sent == []


@pytest.mark.parametrize("mode,code", [("false", 1500), ("driver-error", 1500), ("unsupported", 1404)])
@pytest.mark.parametrize("fail_at,completed_nodes", [(2, 0), (5, 1)])
async def test_runtime_failure_counts_completed_chunks_across_nodes(bot, monkeypatch, mode, code,
                                                                 fail_at, completed_nodes):
    service, driver, _, _ = bot
    original = driver.send_text
    attempts = 0

    async def send_text(chat_id, value):
        nonlocal attempts
        attempts += 1
        if attempts == fail_at:
            if mode == "false":
                return False
            if mode == "unsupported":
                raise DriverUnsupported("synthetic unsupported")
            raise DriverError("synthetic driver failure")
        return await original(chat_id, value)

    monkeypatch.setattr(driver, "send_text", send_text)
    response = await send(bot, [node("a" * 2500), node("b" * 2500), node("never")])
    failed(response, code, "失败" if mode == "false" else "synthetic")
    assert attempts == fail_at
    assert len(driver.sent) == fail_at - 1
    data = response["data"]
    assert data["forward_mode"] == "normal_messages"
    assert data["native_forward"] is False
    assert data["sent_segments"] == fail_at - 1
    assert len(data["message_ids"]) == completed_nodes
    for mid in data["message_ids"]:
        stored = await service.dispatch("get_msg", {"message_id": mid})
        assert stored["status"] == "ok"
        assert "".join(seg["data"]["text"] for seg in stored["data"]["message"]) == "a" * 2500


@pytest.mark.parametrize("mode,code", [("false", 1500), ("driver-error", 1500), ("unsupported", 1404)])
async def test_media_failure_retains_prior_node_and_pending_text_counts(bot, tmp_path, monkeypatch,
                                                                     mode, code):
    path = tmp_path / "picture.png"
    path.write_bytes(b"offline image fixture")

    async def reject_image(chat_id, path):
        if mode == "false":
            return False
        if mode == "unsupported":
            raise DriverUnsupported("synthetic image unsupported")
        raise DriverError("synthetic image failure")

    monkeypatch.setattr(bot[1], "send_image", reject_image)
    response = await send(bot, [node("a" * 2500), node([
        text("before image"), {"type": "image", "data": {"file": str(path)}}, text("never")])])
    failed(response, code, "失败" if mode == "false" else "synthetic image")
    assert response["data"]["sent_segments"] == 4
    assert len(response["data"]["message_ids"]) == 1
    assert [row.kind for row in bot[1].sent] == ["text"] * 4
    assert bot[1].sent[-1].payload["text"] == "before image"


@pytest.mark.parametrize("operation", ["put", "allocate_sent_id"])
async def test_persist_failure_reports_already_sent_chunks(bot, monkeypatch, operation):
    import sqlite3

    service = bot[0]
    original = getattr(service.message_store, operation)
    calls = 0

    def put(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise sqlite3.OperationalError("synthetic database failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(service.message_store, operation, put)
    response = await send(bot, [node("a" * 2500), node("b" * 2500), node("never")])
    failed(response, 1500, "保存失败")
    assert response["data"]["sent_segments"] == 6
    assert len(response["data"]["message_ids"]) == 1
    assert len(bot[1].sent) == 6
    assert response["data"]["forward_mode"] == "normal_messages"
    assert response["data"]["native_forward"] is False
    retained = await service.dispatch("get_msg", {"message_id": response["data"]["message_ids"][0]})
    assert retained["status"] == "ok"


async def test_cancelled_forward_and_queued_normal_are_drained_before_driver_stop(bot, monkeypatch):
    service, driver, mapper, _ = bot
    entered, release, normal_prepared = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_send = driver.send_text
    original_prepare = service._prepare_segments
    original_stop = driver.stop
    stopped = []

    async def send_text(chat_id, value):
        if not entered.is_set():
            entered.set()
            await release.wait()
        assert driver.online
        return await original_send(chat_id, value)

    async def prepare(raw, **kwargs):
        result = await original_prepare(raw, **kwargs)
        if raw == "normal queued":
            normal_prepared.set()
        return result

    async def stop():
        stopped.append([row.payload["text"] for row in driver.sent])
        await original_stop()

    monkeypatch.setattr(driver, "send_text", send_text)
    monkeypatch.setattr(service, "_prepare_segments", prepare)
    monkeypatch.setattr(driver, "stop", stop)
    forward_task = asyncio.create_task(send(bot, [node("a" * 1500), node("last node")]))
    normal_task = stop_task = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        normal_task = asyncio.create_task(service.dispatch("send_group_msg", {
            "group_id": mapper.get_or_create("group", "10001@chatroom"), "message": "normal queued"}))
        await asyncio.wait_for(normal_prepared.wait(), 3)
        assert len(service._action_tasks) == 2
        owned = tuple(service._action_tasks)
        forward_task.cancel()
        normal_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await forward_task
        with pytest.raises(asyncio.CancelledError):
            await normal_task
        assert all(not task.done() for task in owned)
        stop_task = asyncio.create_task(service.stop())
        await asyncio.sleep(0)  # allow stop to reach its drain wait
        assert not stop_task.done()
        assert driver.online
        assert stopped == []
        failed(await send(bot, [node("rejected during shutdown")]), 1500, "关闭")
        release.set()
        await asyncio.wait_for(asyncio.shield(stop_task), 3)
        results = await asyncio.gather(*owned)
        forward_result = next(result for result in results if isinstance(result, dict))
        assert forward_result["sent_segments"] == 3
        assert forward_result["native_forward"] is False
        assert forward_result["forward_mode"] == "normal_messages"
        assert stopped == [["a" * 1023, "a" * 477, "last node", "normal queued"]]
        assert not driver.online
        assert service._action_tasks == set()
    finally:
        release.set()
        await asyncio.gather(*(task for task in (forward_task, normal_task, stop_task) if task is not None),
                             return_exceptions=True)


@pytest.mark.parametrize("kind", ["image", "record", "file", "face"])
async def test_empty_media_is_rejected_by_generic_preflight_before_any_send(bot, tmp_path, kind):
    path = tmp_path / "empty.bin"
    path.touch()
    response = await send(bot, [node("valid first"), node([{"type": kind, "data": {"file": str(path)}}])])
    failed(response, 1400, "非空")
    assert bot[1].sent == []


@pytest.mark.parametrize("kind,diagnostic", [("image", "图片"), ("record", "语音"),
                                           ("face", "GIF"), ("file", "20 MiB")])
async def test_helper_pure_media_preflight_rejects_later_invalid_node_atomically(
        bot, tmp_path, monkeypatch, helper_path, kind, diagnostic):
    from dataclasses import replace
    from wechat_onebot.driver.helper import HelperDriver
    from wxbot_framework import files

    service, mock, _, _ = bot
    helper_root = Path(__file__).resolve().parents[1] / "vendor" / "helper"
    config = replace(service.config, driver=replace(service.config.driver, helper_root=helper_root))
    helper = HelperDriver(config)
    helper._online = True
    helper._service = object()  # never start/inject or install a native service
    path = tmp_path / "invalid.bin"
    path.write_bytes(b"not valid media")
    if kind == "file":
        monkeypatch.setattr(files, "MAX_FILE_BYTES", 8)

    async def forbidden_send(*args, **kwargs):
        pytest.fail("Helper preflight must fail before the first ordinary send")

    monkeypatch.setattr(helper, "send_text", forbidden_send)
    # Keep the fixture's Mock lifecycle; only the Action uses Helper's pure validators.
    with monkeypatch.context() as patch:
        patch.setattr(service, "driver", helper)
        response = await send(bot, [node("valid first"),
                                    node([{"type": kind, "data": {"file": str(path)}}])])
    failed(response, 1400, diagnostic)
    assert mock.sent == []


async def test_helper_wav_encoder_unavailable_is_detected_before_any_send(
        bot, tmp_path, monkeypatch, helper_path):
    import wave
    from wechat_onebot.driver.helper import HelperDriver
    from wxbot_framework import voices

    path = tmp_path / "valid.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 160)

    def unavailable_encoder():
        raise RuntimeError("synthetic unavailable WAV encoder")

    monkeypatch.setattr(voices, "_load_encoder", unavailable_encoder)
    helper = HelperDriver(bot[0].config)
    helper._online = True
    helper._service = object()
    with monkeypatch.context() as patch:
        patch.setattr(bot[0], "driver", helper)
        response = await send(bot, [node("valid first"),
                                    node([{"type": "record", "data": {"file": str(path)}}])])
    failed(response, 1500, "synthetic unavailable WAV encoder")
    assert bot[1].sent == []


async def test_media_count_limit_rejects_before_any_resolve(bot, monkeypatch):
    async def forbidden_resolve(*args, **kwargs):
        pytest.fail("17 media segments must be rejected before resolving even the first")

    monkeypatch.setattr(bot[0].media, "resolve_input", forbidden_resolve)
    # Nested nodes must share the root count, even with URL/base64-looking inputs.
    media = {"type": "file", "data": {"file": "https://fixture.invalid/never-requested"}}
    messages = [node("valid first"), node({"messages": [node([media] * 9), node([media] * 8)]})]
    failed(await send(bot, messages), 1400, "媒体额度")
    assert bot[1].sent == []


@pytest.mark.parametrize("across_nodes", [False, True], ids=["same-node", "across-nodes"])
async def test_remaining_media_budget_passed_per_item_before_oversized_base64_persist(
        bot, tmp_path, monkeypatch, across_nodes):
    import base64
    from wechat_onebot.protocol import outbound_forward

    monkeypatch.setattr(outbound_forward, "MAX_MEDIA_BYTES", 10)
    first = tmp_path / "first.bin"
    first.write_bytes(b"123456")
    items = [{"type": "file", "data": {"file": str(first)}},
             {"type": "file", "data": {"file": "base64://" + base64.b64encode(b"12345").decode("ascii")}},
             {"type": "file", "data": {"file": "must never be resolved"}}]
    original = bot[0].media.resolve_input
    limits = []

    async def resolve(value, **kwargs):
        assert bot[1].sent == []
        limits.append(kwargs.get("max_bytes"))
        return await original(value, **kwargs)

    monkeypatch.setattr(bot[0].media, "resolve_input", resolve)
    cache = bot[0].media.media_dir / "cache"
    before = set(cache.iterdir())
    messages = [node([item]) for item in items] if across_nodes else [node(items)]
    failed(await send(bot, [node("valid first"), *messages]), 1400, "大小限制")
    assert limits == [10, 4]
    assert set(cache.iterdir()) == before
    assert bot[1].sent == []


async def test_generic_exception_during_mention_preserves_prior_chunk_and_media_progress(
        bot, tmp_path, monkeypatch):
    import sqlite3

    service, driver, mapper, _ = bot
    alice = mapper.get_or_create("user", "wxid_alice")
    path = tmp_path / "file.bin"
    path.write_bytes(b"fixture")

    def fail_profiles(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic member profile failure")

    monkeypatch.setattr(service.message_store, "remember_profiles", fail_profiles)
    response = await send(bot, [node("a" * 2500), node([
        text("before file"), {"type": "file", "data": {"file": str(path)}},
        {"type": "at", "data": {"qq": str(alice)}}, text("never sent")]), node("never reached")])
    failed(response, 1500, "内部错误")
    assert response["data"]["sent_segments"] == 5
    assert len(response["data"]["message_ids"]) == 1
    assert response["data"]["forward_mode"] == "normal_messages"
    assert response["data"]["native_forward"] is False
    assert [row.kind for row in driver.sent] == ["text"] * 4 + ["file"]
    assert driver.sent[3].payload["text"] == "before file"
    retained = await service.dispatch("get_msg", {"message_id": response["data"]["message_ids"][0]})
    assert retained["status"] == "ok"


@pytest.mark.parametrize("over_limit", [False, True], ids=["exact-boundary", "over-limit"])
async def test_empty_text_segments_consume_shared_visited_segment_budget(bot, over_limit):
    # Nonempty content keeps both nodes valid; empty rows still consume work.
    first = node([text("first")] + [text("") for _ in range(999)])
    second = node([text("") for _ in range(1000 if over_limit else 999)] + [text("second")])
    response = await send(bot, [first, second])
    if over_limit:
        failed(response, 1400, "消息段")
        assert bot[1].sent == []
    else:
        ordinary(response, nodes=2, sends=2, chains=2)
        assert [row.payload["text"] for row in bot[1].sent] == ["first", "second"]
