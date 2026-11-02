from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ...config import AppConfig, ConfigError


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _normalize_path(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ConfigError("网络 path 必须以 / 开头")
    if "\x00" in value:
        raise ConfigError("网络 path 不能包含 NUL")
    return value


def _validate_host_port(host: Any, port: Any) -> tuple[str, int]:
    if not isinstance(host, str) or not host.strip():
        raise ConfigError("网络 host 必须是非空字符串")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ConfigError("网络 port 必须是 1–65535 的整数")
    return host.strip(), port


@dataclass(frozen=True)
class HttpServerEndpoint:
    name: str
    host: str
    port: int
    path: str
    access_token: str
    enabled: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "HttpServerEndpoint":
        name = value.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("httpServers.name 必须是非空字符串")
        host, port = _validate_host_port(value.get("host", "127.0.0.1"), value.get("port"))
        token = value.get("accessToken", "")
        enabled = value.get("enabled", True)
        if not isinstance(token, str):
            raise ConfigError("httpServers.accessToken 必须是字符串")
        if type(enabled) is not bool:
            raise ConfigError("httpServers.enabled 必须是布尔值")
        return cls(name.strip(), host, port, _normalize_path(value.get("path", "/")), token, enabled)


@dataclass(frozen=True)
class HttpClientEndpoint:
    name: str
    url: str
    access_token: str
    timeout_seconds: float = 10.0
    enabled: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "HttpClientEndpoint":
        name = value.get("name")
        url = value.get("url")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("httpClients.name 必须是非空字符串")
        parsed = urlparse(url) if isinstance(url, str) else None
        if parsed is None or parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ConfigError("httpClients.url 必须是 http(s) URL")
        token = value.get("accessToken", "")
        timeout = value.get("timeoutSeconds", 10.0)
        enabled = value.get("enabled", True)
        if not isinstance(token, str):
            raise ConfigError("httpClients.accessToken 必须是字符串")
        if type(timeout) not in (int, float) or not 0.5 <= float(timeout) <= 120:
            raise ConfigError("httpClients.timeoutSeconds 必须在 0.5–120 之间")
        if type(enabled) is not bool:
            raise ConfigError("httpClients.enabled 必须是布尔值")
        return cls(name.strip(), url, token, float(timeout), enabled)


@dataclass(frozen=True)
class WsServerEndpoint:
    name: str
    host: str
    port: int
    path: str
    access_token: str
    enabled: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WsServerEndpoint":
        name = value.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("wsServers.name 必须是非空字符串")
        host, port = _validate_host_port(value.get("host", "127.0.0.1"), value.get("port"))
        token = value.get("accessToken", "")
        enabled = value.get("enabled", True)
        if not isinstance(token, str):
            raise ConfigError("wsServers.accessToken 必须是字符串")
        if type(enabled) is not bool:
            raise ConfigError("wsServers.enabled 必须是布尔值")
        return cls(name.strip(), host, port, _normalize_path(value.get("path", "/ws")), token, enabled)


@dataclass(frozen=True)
class WsClientEndpoint:
    name: str
    url: str
    access_token: str
    reconnect_seconds: float = 5.0
    enabled: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WsClientEndpoint":
        name = value.get("name")
        url = value.get("url")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("wsClients.name 必须是非空字符串")
        parsed = urlparse(url) if isinstance(url, str) else None
        if parsed is None or parsed.scheme not in ("ws", "wss") or not parsed.netloc:
            raise ConfigError("wsClients.url 必须是 ws(s) URL")
        token = value.get("accessToken", "")
        reconnect = value.get("reconnectSeconds", 5.0)
        enabled = value.get("enabled", True)
        if not isinstance(token, str):
            raise ConfigError("wsClients.accessToken 必须是字符串")
        if type(reconnect) not in (int, float) or not 0.5 <= float(reconnect) <= 300:
            raise ConfigError("wsClients.reconnectSeconds 必须在 0.5–300 之间")
        if type(enabled) is not bool:
            raise ConfigError("wsClients.enabled 必须是布尔值")
        return cls(name.strip(), url, token, float(reconnect), enabled)


@dataclass(frozen=True)
class AccountNetworkConfig:
    heartbeat_interval_seconds: float
    http_servers: tuple[HttpServerEndpoint, ...]
    http_clients: tuple[HttpClientEndpoint, ...]
    ws_servers: tuple[WsServerEndpoint, ...]
    ws_clients: tuple[WsClientEndpoint, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AccountNetworkConfig":
        heartbeat = value.get("heartbeatIntervalSeconds", 5.0)
        if type(heartbeat) not in (int, float) or not 0.5 <= float(heartbeat) <= 300:
            raise ConfigError("heartbeatIntervalSeconds 必须在 0.5–300 之间")
        lists: dict[str, tuple[type, ...]] = {
            "httpServers": (HttpServerEndpoint,),
            "httpClients": (HttpClientEndpoint,),
            "wsServers": (WsServerEndpoint,),
            "wsClients": (WsClientEndpoint,),
        }
        parsed: dict[str, tuple[Any, ...]] = {}
        for key, (kind,) in lists.items():
            rows = value.get(key, [])
            if not isinstance(rows, list):
                raise ConfigError(f"{key} 必须是数组")
            parsed[key] = tuple(kind.from_dict(row) for row in rows if isinstance(row, dict))
            names = [row.name for row in parsed[key]]
            if len(names) != len(set(names)):
                raise ConfigError(f"{key} 中 name 必须唯一")
        binds = [(row.host, row.port) for row in parsed["httpServers"]] + [
            (row.host, row.port) for row in parsed["wsServers"]
        ]
        if len(binds) != len(set(binds)):
            raise ConfigError("HTTP/WS 服务端不能重复监听同一 host/port")
        return cls(float(heartbeat), *(parsed[key] for key in ("httpServers", "httpClients", "wsServers", "wsClients")))

    def to_dict(self) -> dict[str, Any]:
        return {
            "heartbeatIntervalSeconds": self.heartbeat_interval_seconds,
            "httpServers": [{
                "name": row.name, "host": row.host, "port": row.port, "path": row.path,
                "accessToken": row.access_token, "enabled": row.enabled,
            } for row in self.http_servers],
            "httpClients": [{
                "name": row.name, "url": row.url, "accessToken": row.access_token,
                "timeoutSeconds": row.timeout_seconds, "enabled": row.enabled,
            } for row in self.http_clients],
            "wsServers": [{
                "name": row.name, "host": row.host, "port": row.port, "path": row.path,
                "accessToken": row.access_token, "enabled": row.enabled,
            } for row in self.ws_servers],
            "wsClients": [{
                "name": row.name, "url": row.url, "accessToken": row.access_token,
                "reconnectSeconds": row.reconnect_seconds, "enabled": row.enabled,
            } for row in self.ws_clients],
        }


def default_account_network_config(legacy: AppConfig, account: str) -> AccountNetworkConfig:
    return AccountNetworkConfig(
        heartbeat_interval_seconds=legacy.onebot.heartbeat_interval_seconds,
        http_servers=(),
        http_clients=(),
        ws_servers=(),
        ws_clients=(),
    )


def save_account_network_config(path: Path, config: AccountNetworkConfig) -> None:
    _atomic_write(path, config.to_dict())


def load_or_create_account_network_config(path: Path, legacy: AppConfig, account: str) -> AccountNetworkConfig:
    if not path.is_file():
        config = default_account_network_config(legacy, account)
        _atomic_write(path, config.to_dict())
        return config
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"无法读取账号网络配置 {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError("账号网络配置根节点必须是 JSON 对象")
    return AccountNetworkConfig.from_dict(raw)
