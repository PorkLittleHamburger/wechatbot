from __future__ import annotations

import asyncio
import queue
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_onebot.bridge.discovery import discover_sdk_accounts
from wechat_onebot.config import AppConfig, ConfigError, DriverConfig, OneBotConfig, StorageConfig
from wechat_onebot.driver.base import DriverError, DriverUnsupported
from wechat_onebot.driver.sdk import SdkDriver


# --------------------------------------------------------------- fake client
class FakeHandle:
    def __init__(self, data: bytes, root: Path):
        self._data = data
        self.path = root / "native.bin"
        self.released = 0

    def export(self, destination):
        destination = Path(destination)
        destination.write_bytes(self._data)
        return destination

    def release(self):
        self.released += 1


class FakeSubscription:
    _END = object()

    def __init__(self):
        self.items: queue.Queue = queue.Queue()

    def put(self, event):
        self.items.put(event)

    def get(self, timeout=None):
        value = self.items.get(timeout=0.1 if timeout is None else timeout)
        if value is self._END:
            raise StopIteration
        return value

    def close(self, wait=True):
        self.items.put(self._END)


def event(**changes):
    base = dict(
        event_id="evt-1", account_id="wxid_robot", session_id="s",
        sequence=1, kind="message", chat_id="wxid_friend", sender_id="wxid_friend",
        native_message_id="100", native_timestamp=1700000000.0,
        content_type="text", text="", mentions=(), reference=None,
        content={}, media=None, bridge_sequence=1,
    )
    base.update(changes)
    return SimpleNamespace(**base)


def result(state="completed", error=None, detail=""):
    return SimpleNamespace(state=SimpleNamespace(value=state), error=error,
                           detail=detail, operation_id="op-1")


class FakeSession:
    def __init__(self, root: Path):
        self.root = root
        self.sub = None
        self.connected = 0
        self.closed = 0
        self.cleaned = 0
        self.requests: list[tuple[str, str | None]] = []
        self.sent: list[tuple[str, tuple, dict]] = []
        self.allow_unverified = None
        self.receive = None
        self.media_payload = b"png-bytes"
        self.media_state = "ready"
        self.media_format = "PNG"
        self.send_state = "completed"

    def subscribe(self):
        self.sub = FakeSubscription()
        return self.sub

    def connect(self, timeout=30, allow_unverified=False, receive=False):
        self.connected += 1
        self.allow_unverified = allow_unverified
        self.receive = receive
        return self

    def close(self, timeout=30):
        self.closed += 1

    def wait_closed(self, timeout=None):
        return None

    def cleanup_media(self):
        self.cleaned += 1
        return 0

    def request_media(self, source, media_id=None, timeout=30):
        self.requests.append((source.event_id, media_id))
        handle = FakeHandle(self.media_payload, self.root)
        self._last_handle = handle
        self.sub.put(event(
            event_id="update-1", kind="media_update", content_type=source.content_type,
            text=None, content={"message_event_id": source.event_id, "media_id": media_id,
                                "media_state": self.media_state,
                                "media_metadata": {"format": self.media_format},
                                "reason": ""},
            media=handle if self.media_state == "ready" else None,
        ))
        return result()

    def _record(self, name, *args, **kwargs):
        self.sent.append((name, args, kwargs))
        return result(self.send_state)

    def send_text(self, chat_id, text, timeout=30):
        return self._record("send_text", chat_id, text, timeout=timeout)

    def send_at_text(self, chat_id, text, members, display_names=None, timeout=30):
        return self._record("send_at_text", chat_id, text, members=members,
                            display_names=display_names, timeout=timeout)

    def send_image(self, chat_id, path, timeout=30):
        return self._record("send_image", chat_id, path, timeout=timeout)

    def send_file(self, chat_id, path, timeout=30):
        return self._record("send_file", chat_id, path, timeout=timeout)

    def send_voice(self, chat_id, path, timeout=30):
        return self._record("send_voice", chat_id, path, timeout=timeout)

    def send_video(self, chat_id, path, timeout=30):
        return self._record("send_video", chat_id, path, timeout=timeout)

    def status(self):
        return SimpleNamespace(state="connected", pid=123, pending_operations=0)

    def diagnostics(self):
        return {"watchdog": {"state": "running", "running": True}}


class FakeClient:
    def __init__(self, data_root=None, **kwargs):
        self.data_root = data_root
        self.session_obj = FakeSession(Path(data_root or "."))
        self.rows = [discovery_result()]

    def discover(self):
        return tuple(self.rows)

    def session(self, pid, account_id=None):
        self.session_obj.pid = pid
        self.session_obj.account_id = account_id
        return self.session_obj

    def contacts(self, pid, account_id=None):
        return SimpleNamespace(records=(SimpleNamespace(
            wxid="wxid_a", wechat_name="甲", remark="备注甲"),))

    def groups(self, pid, account_id=None):
        return SimpleNamespace(records=(SimpleNamespace(wxid="1@chatroom", name="测试群"),))

    def group_members(self, room, pid, account_id=None):
        return SimpleNamespace(records=(SimpleNamespace(
            wxid="wxid_a", group_nickname="群昵称", wechat_name="甲",
            remark=None, role="admin"),))


def discovery_result(pid=123, wxid="wxid_robot", version="4.2.0.52", error=None, bridge=True,
                     wechat_name="机器人"):
    account = None if wxid is None else SimpleNamespace(wxid=wxid, wechat_name=wechat_name)
    return SimpleNamespace(
        pid=pid, process_created=0,
        build=SimpleNamespace(version=version, sha256="ab" * 32, size=1),
        account=account, error=error, detail="read-only", bridge_supported=bridge,
    )


def install_fake_sdk(monkeypatch, client=None) -> FakeClient:
    holder = client or FakeClient()

    class Holder:
        def __call__(self, *args, **kwargs):
            return holder

    module = types.ModuleType("wechatbot_api")
    module.Client = Holder()
    module.__version__ = "0.1.0.dev14"
    monkeypatch.setitem(sys.modules, "wechatbot_api", module)
    return holder


def make_config(tmp_path: Path, **changes) -> AppConfig:
    values = dict(type="sdk", wechat_account="wxid_robot", wechat_pid=123,
                  sdk_data_root=tmp_path / "sdk", sdk_media_timeout=2.0)
    values.update(changes)
    return AppConfig(
        path=tmp_path / "config.json",
        onebot=OneBotConfig(access_token="test"),
        reverse_ws=(),
        driver=DriverConfig(**values),
        storage=StorageConfig(tmp_path / "data", tmp_path / "media", 10 * 1024 * 1024),
    )


async def started_driver(monkeypatch, tmp_path: Path, **config_changes):
    client = install_fake_sdk(monkeypatch)
    driver = SdkDriver(make_config(tmp_path, **config_changes))
    received: list = []
    arrived = asyncio.Event()

    async def emit(message):
        received.append(message)
        arrived.set()

    await driver.start(emit)
    return driver, client, received, arrived


async def wait_for(predicate, timeout=3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.01)
    raise AssertionError("等待事件超时")


# ------------------------------------------------------------------- mapping
def test_capabilities_exclude_unmapped_actions(tmp_path: Path):
    driver = SdkDriver(make_config(tmp_path))
    capabilities = driver.capabilities
    assert "text.send" in capabilities
    assert "voice.receive" in capabilities
    assert "emoticon.send" not in capabilities
    assert "message.recall" not in capabilities
    assert "request.friend" not in capabilities


def test_text_message_maps_identity_and_timestamp(tmp_path: Path):
    driver = SdkDriver(make_config(tmp_path))
    message = driver._convert_message(event(text="你好", native_message_id="42", sequence=9))
    assert message is not None
    assert message.external_chat_id == "wxid_friend"
    assert message.external_sender_id == "wxid_friend"
    assert message.external_message_id == "42"
    assert message.sequence == 9
    assert message.timestamp == 1700000000
    assert message.is_group is False
    assert [(segment.type, segment.data) for segment in message.segments] == [
        ("text", {"text": "你好"})
    ]


def test_group_mentions_become_at_segments_and_labels_are_trimmed(tmp_path: Path):
    driver = SdkDriver(make_config(tmp_path))
    message = driver._convert_message(event(
        chat_id="1@chatroom", sender_id="wxid_a", text="@机器人\u2005你好",
        mentions=("wxid_robot", "wxid_b"),
    ))
    assert [segment.type for segment in message.segments] == ["at", "at", "text"]
    assert [message.segments[0].data["qq"], message.segments[1].data["qq"]] == [
        "wxid_robot", "wxid_b"
    ]
    assert message.segments[2].data["text"] == "你好"
    assert message.raw["is_at_self"] is True
    assert message.is_group is True


def test_reference_message_keeps_reply_identity_for_the_service(tmp_path: Path):
    driver = SdkDriver(make_config(tmp_path))
    message = driver._convert_message(event(
        content_type="reference", text="收到",
        reference={"type": 1, "native_message_id": "99", "sender_id": "wxid_a",
                   "sender_name": "甲", "content": "原始文本"},
    ))
    reply = message.raw["reply"]
    assert reply["native_message_id"] == "99"
    assert reply["sender_id"] == "wxid_a"
    assert reply["type"] == 1
    assert reply["content"] == "原始文本"
    assert message.segments[-1].data["text"] == "收到"


def test_quoted_forwarded_records_expose_forward_segment(tmp_path: Path):
    driver = SdkDriver(make_config(tmp_path))
    records = [{"kind": "text", "sender_name": "甲", "source_time": "", "text": "内容"}]
    message = driver._convert_message(event(
        content_type="reference", text="收到",
        reference={"type": 49, "native_message_id": "77", "sender_id": "wxid_a",
                   "sender_name": "甲", "content": "",
                   "structured_content": {"app_type": 19, "forwarded_title": "聊天记录",
                                          "forwarded_records": records,
                                          "forwarded_truncated": False}},
    ))
    assert message.raw["reply"]["is_forward"] is True
    assert message.raw["reply"]["records"] == records
    assert [(segment.type, segment.data.get("text")) for segment in message.segments] == [
        ("text", "收到")
    ]


def test_inbound_forward_maps_to_forward_segment(tmp_path: Path):
    driver = SdkDriver(make_config(tmp_path))
    message = driver._convert_message(event(
        content_type="forwarded", text="",
        content={"forwarded_title": "记录", "forwarded_truncated": False,
                 "forwarded_records": [{"kind": "text", "sender_name": "甲",
                                        "source_time": "", "text": "内容"}]},
    ))
    assert [segment.type for segment in message.segments] == ["forward"]
    assert message.segments[0].data["title"] == "记录"


def test_unavailable_media_becomes_an_explicit_placeholder(tmp_path: Path):
    driver = SdkDriver(make_config(tmp_path))
    message = driver._convert_message(event(
        content_type="image",
        content={"media_state": "unavailable", "media_reason": "No supported image"},
    ))
    assert [(segment.type, segment.data) for segment in message.segments] == [
        ("text", {"text": "[图片]"})
    ]


# --------------------------------------------------------------------- media
@pytest.mark.asyncio
async def test_pending_image_is_downloaded_and_handle_released(monkeypatch, tmp_path: Path):
    driver, client, received, arrived = await started_driver(monkeypatch, tmp_path)
    try:
        session = client.session_obj
        session.sub.put(event(
            event_id="evt-image", content_type="image",
            content={"media_state": "pending", "media_id": "mid-1"},
            text="",
        ))
        message = await wait_for(lambda: received[0] if received else None)
        assert session.requests == [("evt-image", "mid-1")]
        image = message.segments[0]
        assert image.type == "image"
        exported = Path(image.data["file"])
        assert exported.is_file()
        assert exported.read_bytes() == b"png-bytes"
        assert exported.parent == tmp_path / "media" / "wxid_robot" / "inbound"
        assert session._last_handle.released == 1
    finally:
        await driver.stop()


@pytest.mark.asyncio
async def test_media_without_ready_update_is_not_faked(monkeypatch, tmp_path: Path):
    driver, client, received, arrived = await started_driver(monkeypatch, tmp_path)
    client.session_obj.media_state = "failed"
    try:
        client.session_obj.sub.put(event(
            event_id="evt-fail", content_type="image",
            content={"media_state": "pending", "media_id": "mid-2"}, text="",
        ))
        message = await wait_for(lambda: received[0] if received else None)
        assert [(segment.type, segment.data) for segment in message.segments] == [
            ("text", {"text": "[图片]"})
        ]
    finally:
        await driver.stop()


# ------------------------------------------------------------------- sending
@pytest.mark.asyncio
async def test_send_completed_maps_to_true(monkeypatch, tmp_path: Path):
    driver, client, _, _ = await started_driver(monkeypatch, tmp_path)
    try:
        assert await driver.send_text("wxid_friend", "hello") is True
        name, args, kwargs = client.session_obj.sent[0]
        assert name == "send_text" and args == ("wxid_friend", "hello")
    finally:
        await driver.stop()


@pytest.mark.asyncio
async def test_unknown_send_result_is_never_reported_as_success(monkeypatch, tmp_path: Path):
    driver, client, _, _ = await started_driver(monkeypatch, tmp_path)
    client.session_obj.send_state = "unknown"
    try:
        with pytest.raises(DriverError, match="未知"):
            await driver.send_text("wxid_friend", "hello")
        assert len(client.session_obj.sent) == 1
    finally:
        await driver.stop()


@pytest.mark.asyncio
async def test_failed_send_raises_without_retry(monkeypatch, tmp_path: Path):
    driver, client, _, _ = await started_driver(monkeypatch, tmp_path)
    client.session_obj.send_state = "failed"
    try:
        with pytest.raises(DriverError):
            await driver.send_text("wxid_friend", "hello")
        assert len(client.session_obj.sent) == 1
    finally:
        await driver.stop()


@pytest.mark.asyncio
async def test_emoticon_send_is_unsupported(monkeypatch, tmp_path: Path):
    driver = SdkDriver(make_config(tmp_path))
    with pytest.raises(DriverUnsupported):
        await driver.send_emoticon("wxid_friend", "sticker.gif")


@pytest.mark.asyncio
async def test_send_exception_surfaces_sdk_detail(monkeypatch, tmp_path: Path):
    driver, client, _, _ = await started_driver(monkeypatch, tmp_path)

    def fail_send(chat_id, text, timeout=30):
        raise ValueError("native detail")

    client.session_obj.send_text = fail_send
    try:
        with pytest.raises(DriverError, match="native detail"):
            await driver.send_text("wxid_friend", "hi")
    finally:
        await driver.stop()


@pytest.mark.asyncio
async def test_at_text_passes_members_and_display_names(monkeypatch, tmp_path: Path):
    driver, client, _, _ = await started_driver(monkeypatch, tmp_path)
    try:
        await driver.send_at_text("1@chatroom", "hi", ["wxid_a"], ["甲"])
        name, args, kwargs = client.session_obj.sent[0]
        assert name == "send_at_text"
        assert kwargs["members"] == ["wxid_a"]
        assert kwargs["display_names"] == ["甲"]
    finally:
        await driver.stop()


@pytest.mark.asyncio
async def test_at_text_uses_sdk_names_when_gateway_only_has_wxid(monkeypatch, tmp_path: Path):
    driver, client, _, _ = await started_driver(monkeypatch, tmp_path)
    try:
        await driver.send_at_text("1@chatroom", "hi", ["wxid_a"], ["wxid_a"])
        _, _, kwargs = client.session_obj.sent[0]
        assert kwargs["display_names"] is None
    finally:
        await driver.stop()


@pytest.mark.asyncio
async def test_at_text_retries_with_cache_names_after_not_verified(monkeypatch, tmp_path: Path):
    driver, client, _, _ = await started_driver(monkeypatch, tmp_path)
    session = client.session_obj
    calls: list[list[str] | None] = []

    def send_at_text(chat_id, text, members, display_names=None, timeout=30):
        calls.append(display_names)
        if display_names is not None:
            return SimpleNamespace(
                state=SimpleNamespace(value="failed"),
                error=SimpleNamespace(code=SimpleNamespace(value="NOT_VERIFIED")),
                detail="stale label", operation_id="op-1",
            )
        return SimpleNamespace(
            state=SimpleNamespace(value="completed"), error=None,
            detail="", operation_id="op-2",
        )

    session.send_at_text = send_at_text
    try:
        assert await driver.send_at_text("1@chatroom", "hi", ["wxid_a"], ["旧昵称"]) is True
        assert calls == [["旧昵称"], None]
    finally:
        await driver.stop()


# ------------------------------------------------------------------ reads
@pytest.mark.asyncio
async def test_login_info_falls_back_to_the_cached_account_name(tmp_path: Path):
    driver = SdkDriver(make_config(tmp_path))
    driver._target = SimpleNamespace(
        pid=31004, account=SimpleNamespace(wxid="wxid_robot", wechat_name=None),
        build=SimpleNamespace(version="4.2.0.52", sha256="ab"))
    driver._client = SimpleNamespace(
        contact=lambda wxid, pid=None, account_id=None: SimpleNamespace(
            wechat_name="墨染青花", remark=None))
    info = await driver.get_login_info()
    assert info.external_user_id == "wxid_robot"
    assert info.nickname == "墨染青花"


@pytest.mark.asyncio
async def test_login_info_prefers_existing_configured_nickname(tmp_path: Path):
    driver = SdkDriver(make_config(tmp_path, nickname="配置昵称"))
    driver._target = SimpleNamespace(pid=1, account=SimpleNamespace(wxid="wxid_robot", wechat_name=None))
    driver._client = SimpleNamespace(contact=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no cache")))
    info = await driver.get_login_info()
    assert info.nickname == "配置昵称"


@pytest.mark.asyncio
async def test_reads_come_from_wechat_cache(monkeypatch, tmp_path: Path):
    driver, _, _, _ = await started_driver(monkeypatch, tmp_path)
    try:
        friends = await driver.get_friend_list()
        groups = await driver.get_group_list()
        members = await driver.get_group_member_list("1@chatroom")
        login = await driver.get_login_info()
        assert friends[0].external_user_id == "wxid_a"
        assert friends[0].remark == "备注甲"
        assert groups[0].group_name == "测试群"
        assert members[0].card == "群昵称"
        assert login.external_user_id == "wxid_robot"
    finally:
        await driver.stop()


@pytest.mark.asyncio
async def test_status_reports_watchdog_and_build(monkeypatch, tmp_path: Path):
    driver, _, _, _ = await started_driver(monkeypatch, tmp_path)
    try:
        status = await driver.get_status()
        assert status.online is True
        assert status.details["state"] == "connected"
        assert status.details["watchdog"]["state"] == "running"
        assert status.details["wechat_version"] == "4.2.0.52"
    finally:
        await driver.stop()


# --------------------------------------------------------------- lifecycle
@pytest.mark.asyncio
async def test_stop_closes_session_and_cleans_media(monkeypatch, tmp_path: Path):
    driver, client, _, _ = await started_driver(monkeypatch, tmp_path)
    session = client.session_obj
    await driver.stop()
    assert driver.online is False
    assert session.closed == 1
    assert session.cleaned == 1


@pytest.mark.asyncio
async def test_start_retries_while_old_bridge_is_releasing(monkeypatch, tmp_path: Path):
    from wechat_onebot.driver import sdk as sdk_module

    class BusyError(Exception):
        code = SimpleNamespace(value="SESSION_BUSY")

    class FlakyClient(FakeClient):
        def __init__(self, root: Path):
            super().__init__(root)
            self.sessions: list[FakeSession] = []
            self.failures = 2

        def session(self, pid, account_id=None):
            session = FakeSession(self.data_root)
            if len(self.sessions) < self.failures:
                def busy_connect(*args, **kwargs):
                    session.connected += 1
                    raise BusyError("old bridge still owns the process")
                session.connect = busy_connect
            self.sessions.append(session)
            return session

    client = FlakyClient(tmp_path / "sdk")
    install_fake_sdk(monkeypatch, client)
    monkeypatch.setattr(sdk_module, "_SESSION_BUSY_RETRY_INTERVAL", 0.01)
    monkeypatch.setattr(sdk_module, "_SESSION_BUSY_RETRY_SECONDS", 1.0)
    driver = SdkDriver(make_config(tmp_path))

    async def emit(message):
        return None

    await driver.start(emit)
    try:
        assert len(client.sessions) == 3
        assert all(session.connected >= 1 for session in client.sessions)
        assert client.sessions[0].closed == 1 and client.sessions[1].closed == 1
        assert client.sessions[2].closed == 0
    finally:
        await driver.stop()


@pytest.mark.asyncio
async def test_start_reports_target_selection_problems(monkeypatch, tmp_path: Path):
    client = FakeClient(tmp_path / "sdk")
    client.rows = [discovery_result(pid=1, wxid="wxid_robot"),
                   discovery_result(pid=2, wxid="wxid_robot")]
    install_fake_sdk(monkeypatch, client)
    driver = SdkDriver(make_config(tmp_path))

    async def emit(message):
        return None

    with pytest.raises(DriverError, match="多个"):
        await driver.start(emit)

    client.rows = [discovery_result(pid=3, wxid="wxid_other")]
    with pytest.raises(DriverError, match="4\\.2\\.0\\.52"):
        await driver.start(emit)


# ------------------------------------------------------------------- config
def test_sdk_driver_is_created_from_config(tmp_path: Path):
    from wechat_onebot.driver import create_driver

    assert isinstance(create_driver(make_config(tmp_path)), SdkDriver)


def test_frozen_sdk_structures_are_thawed_for_rich_content(tmp_path: Path):
    from types import MappingProxyType

    records = (MappingProxyType({"kind": "text", "sender_name": "甲",
                                 "source_time": "", "text": "内容"}),)
    reference = MappingProxyType({
        "type": 49, "native_message_id": "77", "sender_id": "wxid_a",
        "sender_name": "甲", "content": "",
        "structured_content": MappingProxyType({
            "app_type": 19, "forwarded_title": "聊天记录",
            "forwarded_records": records, "forwarded_truncated": False,
        }),
    })
    driver = SdkDriver(make_config(tmp_path))
    message = driver._convert_message(event(content_type="reference", text="收到",
                                            reference=reference))
    assert message.raw["reply"]["records"] == [
        {"kind": "text", "sender_name": "甲", "source_time": "", "text": "内容"}
    ]
    frozen = MappingProxyType({"forwarded_title": "记录", "forwarded_truncated": False,
                               "forwarded_records": records})
    forwarded = driver._convert_message(event(content_type="forwarded", text="",
                                              content=frozen))
    assert [segment.type for segment in forwarded.segments] == ["forward"]
    assert forwarded.segments[0].data["records"][0]["text"] == "内容"


def test_sdk_driver_config_accepts_new_fields(tmp_path: Path):
    config = DriverConfig.from_dict({
        "type": "sdk", "wechat_account": "wxid_robot",
        "sdk_data_root": "sdk-data", "sdk_allow_unverified": True,
        "sdk_media_timeout": 12.5,
    }, base_dir=tmp_path)
    assert config.type == "sdk"
    assert config.sdk_data_root == (tmp_path / "sdk-data").resolve()
    assert config.sdk_allow_unverified is True
    assert config.sdk_media_timeout == 12.5


@pytest.mark.parametrize("payload", [
    {"type": "unknown"},
    {"type": "sdk", "sdk_data_root": ""},
    {"type": "sdk", "sdk_allow_unverified": "yes"},
    {"type": "sdk", "sdk_media_timeout": 0},
    {"type": "sdk", "sdk_media_timeout": 301},
])
def test_sdk_driver_config_rejects_invalid_values(tmp_path: Path, payload):
    with pytest.raises(ConfigError):
        DriverConfig.from_dict(payload, base_dir=tmp_path)


# --------------------------------------------------------------- discovery
def test_sdk_discovery_fills_missing_nickname_from_contact_cache(monkeypatch, tmp_path: Path):
    class Client:
        def discover(self):
            return (discovery_result(pid=10, wxid="wxid_a", wechat_name=None),)

        def contact(self, wxid, pid=None, account_id=None):
            return SimpleNamespace(wechat_name="墨染青花", remark=None)

    module = types.ModuleType("wechatbot_api")
    module.Client = Client
    module.__version__ = "0.1.0.dev14"
    monkeypatch.setitem(sys.modules, "wechatbot_api", module)
    rows = discover_sdk_accounts(make_config(tmp_path))
    assert rows[0].wxid == "wxid_a"
    assert rows[0].nickname == "墨染青花"


def test_sdk_discovery_filters_unverified_rows(monkeypatch):
    from wechatbot_api import ErrorCode

    class Client:
        def discover(self):
            return (
                discovery_result(pid=10, wxid="wxid_a"),
                discovery_result(pid=11, wxid=None, error=ErrorCode.UNSUPPORTED_BUILD),
                discovery_result(pid=12, wxid="wxid_b", version="4.2.0.52"),
            )

    module = types.ModuleType("wechatbot_api")
    module.Client = Client
    module.ErrorCode = ErrorCode
    module.__version__ = "0.1.0.dev14"
    monkeypatch.setitem(sys.modules, "wechatbot_api", module)
    rows = discover_sdk_accounts(make_config(Path(".")))
    assert [row.wxid for row in rows] == ["wxid_a", "wxid_b"]
    assert rows[0].version == "4.2.0.52"
    assert rows[0].extra["bridge_supported"] is True
