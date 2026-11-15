"""U-05a token save policy and login rate limiter unit tests."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from wechat_onebot.onebot.network import token_policy
from wechat_onebot.onebot.network.config import (
    DEFAULT_MAX_BODY_BYTES,
    DEFAULT_MAX_MESSAGE_BYTES,
    AccountNetworkConfig,
    HttpServerEndpoint,
    WsServerEndpoint,
)
from wechat_onebot.webui.rate_limit import LoginRateLimiter

ROOT = Path(__file__).resolve().parents[1]
STRONG_TOKEN = "Xk9mP2vL8qT4wZ7c"


def make_network(http_servers=(), ws_servers=()) -> AccountNetworkConfig:
    return AccountNetworkConfig(
        heartbeat_interval_seconds=30.0,
        http_servers=tuple(http_servers),
        http_clients=(),
        ws_servers=tuple(ws_servers),
        ws_clients=(),
    )


def http_endpoint(token: str, *, host: str = "127.0.0.1", name: str = "api",
                  enabled: bool = True) -> HttpServerEndpoint:
    return HttpServerEndpoint(name, host, 6700, "/", token, enabled)


@pytest.mark.parametrize("token,reason", [
    ("", "empty"),
    ("short", "too_short"),
    ("onebot-token", "too_short"),
    ("change-this-token", "guessable"),
    ("a" * 24, "guessable"),
    ("abcdefghijklmnop", "guessable"),
    ("1234567890123456", "guessable"),
    ("has space in token value", "whitespace"),
])
def test_validate_access_token_rejects_weak_values(token, reason):
    ok, actual = token_policy.validate_access_token(token)
    assert ok is False and actual == reason


def test_validate_access_token_rejects_context_and_accepts_random():
    ok, reason = token_policy.validate_access_token(
        "wxid_robot-12345678", user_inputs=("wxid_robot", "api", "127.0.0.1"))
    assert ok is False and reason == "guessable"
    ok, reason = token_policy.validate_access_token(STRONG_TOKEN)
    assert ok is True and reason == "acceptable"


def test_loopback_detection_accepts_local_names_only():
    for host in ("127.0.0.1", "127.8.9.10", "::1", "[::1]", "localhost"):
        assert token_policy.is_loopback_host(host) is True
    for host in ("0.0.0.0", "::", "192.168.1.10", "example.com", "", "testclient"):
        assert token_policy.is_loopback_host(host) is False


def test_token_changes_allow_loopback_empty_token_only_for_loopback_binds():
    previous = make_network()
    loopback = make_network([http_endpoint("", host="127.0.0.1")])
    ok, _ = token_policy.validate_token_changes(previous, loopback, client_ip="127.0.0.1")
    assert ok is True
    lan = make_network([http_endpoint("", host="0.0.0.0")])
    ok, message = token_policy.validate_token_changes(previous, lan, client_ip="127.0.0.1")
    assert ok is False and "非 loopback" in message
    ok, message = token_policy.validate_token_changes(previous, loopback, client_ip="192.168.1.20")
    assert ok is False and "非 loopback" in message


def test_token_changes_react_to_host_change_with_unchanged_empty_token():
    previous = make_network([http_endpoint("", host="127.0.0.1")])
    same_loopback = make_network([http_endpoint("", host="127.0.0.2")])
    ok, _ = token_policy.validate_token_changes(previous, same_loopback, client_ip="127.0.0.1")
    assert ok is True
    exposed = make_network([http_endpoint("", host="0.0.0.0")])
    ok, message = token_policy.validate_token_changes(previous, exposed, client_ip="127.0.0.1")
    assert ok is False and "非 loopback" in message
    ok, message = token_policy.validate_token_changes(previous, exposed, client_ip="192.168.1.5")
    assert ok is False and "非 loopback" in message
    # A newly created non-loopback bind without a token is rejected too.
    empty = make_network()
    new_lan = make_network([http_endpoint("", host="0.0.0.0")])
    ok, message = token_policy.validate_token_changes(empty, new_lan, client_ip="127.0.0.1")
    assert ok is False and "非 loopback" in message
    # Already-saved non-loopback empty tokens keep working (warn only).
    legacy = make_network([http_endpoint("", host="0.0.0.0")])
    ok, _ = token_policy.validate_token_changes(legacy, legacy, client_ip="127.0.0.1")
    assert ok is True
    assert token_policy.insecure_endpoint_warnings(legacy)


def test_token_changes_allow_host_move_with_existing_nonempty_token():
    previous = make_network([http_endpoint("onebot-token", host="127.0.0.1")])
    moved = make_network([http_endpoint("onebot-token", host="0.0.0.0")])
    ok, _ = token_policy.validate_token_changes(previous, moved, client_ip="203.0.113.5")
    assert ok is True  # Unchanged weak token is warned about, not revalidated.


def test_token_changes_validate_only_changed_tokens_and_enabling():
    previous = make_network([http_endpoint("onebot-token", enabled=False),
                             http_endpoint(STRONG_TOKEN, name="stable")])
    unchanged = make_network([http_endpoint("onebot-token", enabled=False),
                              http_endpoint(STRONG_TOKEN, name="stable")])
    ok, _ = token_policy.validate_token_changes(previous, unchanged, client_ip="203.0.113.5")
    assert ok is True  # Existing weak token keeps working until rotated.
    enabling = make_network([http_endpoint("onebot-token", enabled=True),
                             http_endpoint(STRONG_TOKEN, name="stable")])
    ok, message = token_policy.validate_token_changes(previous, enabling, client_ip="203.0.113.5")
    assert ok is False and "不可用" in message
    changed = make_network([http_endpoint("onebot-token", enabled=False),
                            http_endpoint("short", name="stable")])
    ok, message = token_policy.validate_token_changes(previous, changed, client_ip="203.0.113.5")
    assert ok is False and "至少需要 16" in message
    rotated = make_network([http_endpoint("onebot-token", enabled=False),
                            http_endpoint(STRONG_TOKEN + "x", name="stable")])
    ok, _ = token_policy.validate_token_changes(previous, rotated, client_ip="203.0.113.5")
    assert ok is True


def test_token_changes_allow_rename_with_same_token():
    previous = make_network([http_endpoint("short", enabled=False)])
    renamed = make_network([http_endpoint("short", name="renamed", enabled=False)])
    ok, _ = token_policy.validate_token_changes(previous, renamed, client_ip="203.0.113.5")
    assert ok is True


def test_ws_server_tokens_follow_the_same_policy():
    previous = make_network()
    weak = make_network(ws_servers=[WsServerEndpoint("ws", "0.0.0.0", 6701, "/ws", "onebot-token")])
    ok, message = token_policy.validate_token_changes(previous, weak, client_ip="127.0.0.1")
    assert ok is False and "WebSocket" in message


def test_insecure_endpoint_warnings_cover_exposure_and_weak_tokens():
    exposed = make_network([http_endpoint("", host="0.0.0.0")])
    warnings = token_policy.insecure_endpoint_warnings(exposed)
    assert len(warnings) == 1 and "accessToken" in warnings[0]
    weak = make_network([http_endpoint("onebot-token", host="127.0.0.1")])
    warnings = token_policy.insecure_endpoint_warnings(weak)
    assert len(warnings) == 1 and "较弱" in warnings[0]
    safe = make_network([http_endpoint(STRONG_TOKEN), http_endpoint("", host="127.0.0.1", name="local")])
    assert token_policy.insecure_endpoint_warnings(safe) == []


def test_http_server_max_body_bytes_roundtrip_and_validation():
    from wechat_onebot.config import ConfigError
    from wechat_onebot.onebot.network.config import DEFAULT_MAX_BODY_BYTES

    endpoint = HttpServerEndpoint.from_dict({"name": "api", "port": 6700})
    assert DEFAULT_MAX_BODY_BYTES == 100 * 1024 * 1024
    assert endpoint.max_body_bytes == DEFAULT_MAX_BODY_BYTES
    # The default must cover base64 of a 50 MiB decoded media item (4/3 + JSON).
    media_bytes = 50 * 1024 * 1024
    assert DEFAULT_MAX_BODY_BYTES >= 4 * ((media_bytes + 2) // 3) + 4096
    network = make_network([endpoint])
    assert network.to_dict()["httpServers"][0]["maxBodyBytes"] == DEFAULT_MAX_BODY_BYTES
    custom = HttpServerEndpoint.from_dict({
        "name": "api", "port": 6700, "maxBodyBytes": 2 * 1024 * 1024})
    assert custom.max_body_bytes == 2 * 1024 * 1024
    for broken in (0, 1024, 512 * 1024 * 1024 + 1, True, "104857600"):
        with pytest.raises(ConfigError):
            HttpServerEndpoint.from_dict({
                "name": "api", "port": 6700, "maxBodyBytes": broken})


def test_webui_new_endpoint_defaults_match_backend_limits():
    """New WebUI endpoints must not freeze stale backend defaults (W-01)."""
    html = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert "67108864" not in html
    start = html.index("function defaultEndpoint")
    block = html[start:html.index("\n}", start)]
    body_defaults = {int(value) for value in re.findall(r"maxBodyBytes:\s*(\d+)", block)}
    assert body_defaults == {DEFAULT_MAX_BODY_BYTES}
    message_defaults = [int(value) for value in re.findall(
        r"maxMessageBytes:\s*(\d+)", block)]
    assert message_defaults == [DEFAULT_MAX_MESSAGE_BYTES, DEFAULT_MAX_MESSAGE_BYTES]
    assert block.count("maxMessageBytes:") == 2


def test_login_rate_limiter_window_and_success_reset():
    now = [1000.0]
    limiter = LoginRateLimiter(max_failures=5, window_seconds=60.0, clock=lambda: now[0])
    for _ in range(4):
        limiter.record_failure("127.0.0.1")
    assert limiter.retry_after("127.0.0.1") == 0.0
    limiter.record_failure("127.0.0.1")
    assert limiter.retry_after("127.0.0.1") == pytest.approx(60.0)
    assert LoginRateLimiter.retry_after_header(limiter.retry_after("127.0.0.1")) == "60"
    limiter.clear("127.0.0.1")
    assert limiter.retry_after("127.0.0.1") == 0.0
    for _ in range(5):
        limiter.record_failure("127.0.0.1")
    now[0] += 59.0
    assert limiter.retry_after("127.0.0.1") > 0
    now[0] += 2.0
    assert limiter.retry_after("127.0.0.1") == 0.0
