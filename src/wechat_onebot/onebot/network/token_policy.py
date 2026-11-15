"""Save-time policy for OneBot inbound access tokens (U-05a, not U-05b).

Only *changed* inbound endpoint tokens are evaluated, so an existing weak token
keeps working until an operator rotates it. Empty tokens are allowed only when
the management client and the listening address are both loopback (local
development); a non-loopback bind must keep a non-empty token. Manual edits of
``onebot.json`` are not rejected: the network manager logs a warning instead,
which preserves existing installations.
"""
from __future__ import annotations

import ipaddress

from .config import AccountNetworkConfig

MIN_TOKEN_LENGTH = 16

# Placeholder and common weak values from documentation, examples and scanners.
WEAK_TOKENS = frozenset({
    "change-this-token", "change-me", "changeme", "onebot-token", "test-token",
    "access-token", "access_token", "wechat-onebot", "wechatbot", "token",
    "secret", "password", "passwd", "letmein", "admin", "qwerty",
    "123456", "12345678", "123456789", "1234567890",
})

REASON_TEXT = {
    "empty": "空 accessToken 只允许同时满足本机客户端与 loopback 监听",
    "too_short": f"accessToken 至少需要 {MIN_TOKEN_LENGTH} 个字符",
    "whitespace": "accessToken 不能包含空白字符",
    "guessable": "accessToken 过于简单（占位/常见弱值/顺序/纯数字或包含账号与端点信息）",
}


def is_loopback_host(host: str) -> bool:
    """True for localhost, 127.0.0.0/8 and ::1 (brackets accepted)."""
    value = str(host or "").strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    if not value:
        return False
    if value.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _is_sequential(value: str) -> bool:
    if len(value) < 4:
        return False
    codes = [ord(char) for char in value]
    difference = codes[1] - codes[0]
    if difference not in (1, -1):
        return False
    return all(right - left == difference for left, right in zip(codes, codes[1:]))


def validate_access_token(token: str, *, user_inputs: tuple[str, ...] | list[str] = ()) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for a newly configured non-empty token."""
    if token == "":
        return False, "empty"
    if any(char.isspace() for char in token):
        return False, "whitespace"
    if len(token) < MIN_TOKEN_LENGTH:
        return False, "too_short"
    lowered = token.lower()
    if lowered in WEAK_TOKENS:
        return False, "guessable"
    if len(set(token)) == 1 or token.isdigit() or _is_sequential(lowered):
        return False, "guessable"
    for value in user_inputs:
        candidate = str(value or "").strip().lower()
        if len(candidate) >= 4 and candidate in lowered:
            return False, "guessable"
    return True, "acceptable"


def _pairs(previous_rows, next_rows):
    by_name = {row.name: row for row in previous_rows}
    for index, row in enumerate(next_rows):
        previous = by_name.get(row.name)
        if previous is None and index < len(previous_rows):
            # A rename keeps the same token semantics; only the secret matters.
            previous = previous_rows[index]
        yield row, previous


def validate_token_changes(previous: AccountNetworkConfig, next: AccountNetworkConfig, *,
                           client_ip: str,
                           user_inputs: tuple[str, ...] | list[str] = ()) -> tuple[bool, str]:
    """Validate tokens for changed tokens and newly exposed bind addresses.

    Unchanged non-empty tokens are not re-strength-checked (an existing weak
    token keeps working and is only warned about at runtime). An existing empty
    token is re-evaluated when a host change or enable makes it non-loopback
    exposed.
    """
    client_loopback = is_loopback_host(client_ip)
    groups = (
        ("HTTP 服务端", previous.http_servers, next.http_servers),
        ("WebSocket 服务端", previous.ws_servers, next.ws_servers),
    )
    for label, previous_rows, next_rows in groups:
        for row, old in _pairs(previous_rows, next_rows):
            token_changed = old is None or old.access_token != row.access_token
            if not token_changed:
                if row.access_token:
                    if old.enabled and not row.enabled:
                        continue  # Disabling an endpoint never needs revalidation.
                    if not (old.enabled is False and row.enabled):
                        # Unchanged non-empty tokens stay accepted even if the
                        # bind address moves; runtime warnings cover weak ones.
                        continue
                    # Enabling previously disabled endpoint: validate the token
                    # that is about to be exposed.
                elif (old.enabled or not row.enabled) and old.host == row.host:
                    # Existing empty token at the same bind address is allowed
                    # for compatibility (loopback stays allowed, others warn).
                    continue
                # An empty token newly exposed by a host change or by enabling
                # falls through to the empty-token checks below.
            elif old is None and not row.enabled:
                continue
            if not row.access_token:
                if not (client_loopback and is_loopback_host(row.host)):
                    return False, (f"{label} {row.name!r} 的 accessToken 为空；"
                                   "非 loopback 绑定必须配置非空 Token")
                continue
            ok, reason = validate_access_token(row.access_token, user_inputs=user_inputs)
            if not ok:
                return False, f"{label} {row.name!r} 的新 accessToken 不可用：{REASON_TEXT[reason]}"
    return True, ""


def insecure_endpoint_warnings(config: AccountNetworkConfig) -> list[str]:
    """Describe saved inbound endpoints that an operator should rotate or close."""
    warnings: list[str] = []
    for row in (*config.http_servers, *config.ws_servers):
        if not row.enabled:
            continue
        if not row.access_token:
            if not is_loopback_host(row.host):
                warnings.append(
                    f"端点 {row.name!r} 监听 {row.host} 且未设置 accessToken，任何可达客户端都可调用")
            continue
        ok, reason = validate_access_token(row.access_token)
        if not ok:
            warnings.append(f"端点 {row.name!r} 的 accessToken 较弱（{REASON_TEXT[reason]}），建议轮换")
    return warnings
